#!/usr/bin/env python3
"""Safe compression layer: a CHECK-ONLY layer. It never ships a container that does not byte-exact round-trip.

  * Run ONE encoder for the request (the routed Oct-2025 engine for the default modes), then DECODE the result and
    verify sha256 (engine results with the CURRENT engine decoder; routed codecs with their registered decoder).
  * Reject RL-neural payloads (flag 0x80): they need a ~12.8 GB external model to decode.
  * Only if verification fails (or the encoder errors): fall back to xz (zstd above QZIP_XZ_MAX = 32 MiB if
    installed). `stored` if nothing is smaller than the input. No try-all.

Modes
  auto (default)          predictive router (src/qzrouter.py), objective "balanced": the GPL mcm fork (qzmf,
                          src/engine_mcmfork, -q11) for text / CSV / logs / binaries; predicted exceptions: CSVs
                          wider than 256 fields -> max codec (qzc2 / qzs), NIfTI -> qzm, label maps -> qzb,
                          incompressible -> stored. Without the qzmf binary (not built) it behaves as auto-max.
  auto-max (CLI --max)    same analysis, objective "max": smallest output from QZip's own codecs (qzc2 = x18 CM /
                          qzs CSV+log / qzm NIfTI / stored); ~3x slower than auto on text
  auto-fast               same analysis, picks among the fast codecs (qzb word+BWT on/off, zstd, stored)
  tier-fast (CLI --fast)  everyday fast tier: qze -1 (CSV column transform + zstd, parallel blocks)
  tier-balanced (--balanced) everyday balanced tier: qze -2 (CSV transform + BWT / zstd -19, parallel blocks)
  smart (CLI --smart)     picks fast / balanced / strong from the file size and type and --goal (qzrouter.smart_tier)
  codec:NAME              override: skip the prediction and use NAME (see qzrouter.CODECS)
  fast                    Oct-2025 engine with the QZip router (src/qzip_routes.py), fast level
  max                     same router, max level (CM text modes: 4 x stock mcm -h11)
  ultra                   same router, ultra level (2 x stock mcm -x11)
  legacy                  Oct-2025 engine with its ORIGINAL routes only (router off) = "QZip today" baseline
  hybrid                  current (Nov-2025) engine only
  (fast/max/ultra/legacy/hybrid and decoding 2025 web-app files need the optional 2025 engines: pip install -e .[legacy])
  text                    optional hook src/qzip_text.py (compress(bytes)->bytes, decompress(bytes)->bytes)

Container dict (stored in a .qz file as a JSON header + raw payload, see qzip/container.py; older .qc/.qc.gz files are JSON):
  {compressed_data:b64, reconstruction_metadata:b64|None, method,
   quantum_parameters:{..., qzip:{orig_sha256, orig_size, verified, format, decoder, mode, detected_type, chosen,
                                  route, fallback}},
   orig_sha256, orig_size, verified:true, format:'qzip-1'}
"""
import base64, json, hashlib, copy, lzma, os, time

try:
    from src import qzip_routes as R
    from src import qzrouter as QR
except ImportError:
    import qzip_routes as R
    import qzrouter as QR
QR.register(R)   # qzcore decoders (qzc2 / qzb / qzs / qzm) for routed containers

LEGACY_METHOD = "hybrid_quantum_v2025_10"
TEXT_METHOD = "text"
ENGINE_METHODS = [LEGACY_METHOD, "hybrid_quantum", "adaptive_quantum", "quantum_superposition", "quantum_entanglement"]
DEFAULT_MODE = "auto"
AUTO_METHOD = "qzrouter"
AUTO_OBJECTIVE = {"auto": "balanced", "auto-max": "max", "auto-fast": "fast",
                  "tier-fast": "tier-fast", "tier-balanced": "tier-balanced", "smart": "smart"}
LEVEL_OF = {"tier-fast": "fast", "tier-balanced": "fast", "smart": "fast", "fast": "fast", "auto": "fast", "auto-max": "fast", "auto-fast": "fast", "max": "max", "ultra": "ultra", "legacy": None}
MODES = {   # mode -> the single encoder it runs
    "fast":   [LEGACY_METHOD],
    "auto":   [AUTO_METHOD, LEGACY_METHOD],   # legacy only runs if the router could not encode
    "auto-max": [AUTO_METHOD, LEGACY_METHOD],
    "auto-fast": [AUTO_METHOD, LEGACY_METHOD],
    "tier-fast": [AUTO_METHOD],       # everyday tiers (CLI --fast / --balanced / --smart); --strong = auto
    "tier-balanced": [AUTO_METHOD],
    "smart": [AUTO_METHOD],
    "max":    [LEGACY_METHOD],
    "ultra":  [LEGACY_METHOD],
    "legacy": [LEGACY_METHOD],
    "hybrid": ["hybrid_quantum"],
    "text":   [TEXT_METHOD],
}
MB = 1 << 20
RL_FLAG = 0x80  # struct.pack('<B',0x80)+rl_payload (needs external model) -> never shipped
_LEGACY = {}

def _legacy_engine():
    if 'e' not in _LEGACY:
        try:
            from src import qzip_legacy_2025_10 as L
        except ImportError:
            import qzip_legacy_2025_10 as L
        _LEGACY['e'] = L.ElliottQuantumCompressionEnterprise(L.CompressionConfig())
    return _LEGACY['e']

def _text_codec():
    try:
        from src import qzip_text as T
    except ImportError:
        try:
            import qzip_text as T
        except ImportError:
            return None
    return T

def _b(v):
    if v is None: return b''
    if isinstance(v, (bytes, bytearray)): return bytes(v)
    if isinstance(v, str):
        try: return base64.b64decode(v, validate=True)
        except Exception: return v.encode()
    return json.dumps(v, default=str).encode()

def _tobytes(x):
    if isinstance(x, (bytes, bytearray)): return bytes(x)
    if isinstance(x, str): return x.encode()
    if isinstance(x, dict): return json.dumps(x).encode()
    return repr(x).encode()

def _engine_roundtrip(engine, r):
    """Decode an engine result dict with the given (current) engine; bytes or None on failure."""
    try:
        return _tobytes(engine.decompress_memory_data(copy.deepcopy(r)))
    except Exception:
        return None

def methods_for(mode):
    if isinstance(mode, str) and mode.startswith("codec:"):
        return [AUTO_METHOD, LEGACY_METHOD]
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; choose from {sorted(MODES)}")
    return list(MODES[mode])

def _xz_fallback(data):
    if len(data) > int(os.environ.get('QZIP_XZ_MAX', str(32 * MB))) and R.zstd_available() is True:
        return 'stored_zstd', R._zstd_c(data)
    return 'stored_xz', lzma.compress(data, preset=9)

def safe_compress(engine, data, methods=None, allow_rl=False, mode=None):
    """Return (container_dict, info). One encode + one verifying decode; xz only if that fails."""
    t_start = time.time()
    mode = mode or (None if methods else DEFAULT_MODE)
    methods = methods or methods_for(mode)
    osha = hashlib.sha256(data).hexdigest()
    candidates = []  # (payload_len, method, compressed_bytes, meta_bytes_or_None, quantum_parameters)
    tried = []
    route = None
    for m in methods:
        t0 = time.time()
        if m == LEGACY_METHOD and candidates and \
                (mode in AUTO_OBJECTIVE or str(mode).startswith("codec:")):
            continue   # the router already produced a verified candidate: no second encoder
        if m == AUTO_METHOD:
            codec = mode[6:] if str(mode).startswith("codec:") else None
            try:
                rmethod, cd, rinfo = QR.compress(data, objective=AUTO_OBJECTIVE.get(mode, "balanced"), codec=codec)
                t_enc = time.time() - t0
                dec = cd if rmethod == 'stored' else R.decode(rmethod, cd)
            except Exception as e:
                tried.append((m, f"error:{type(e).__name__}:{str(e)[:160]}")); continue
            if hashlib.sha256(dec).hexdigest() != osha:
                tried.append((m, f"roundtrip_FAIL({rmethod})")); continue
            route = {"detected_type": rinfo["detected_type"], "type_reason": rinfo["type_reason"],
                     "chain": ["qzrouter:" + rinfo["objective"]], "chosen": rinfo["chosen"],
                     "features": rinfo["features"], "predict_seconds": rinfo["predict_seconds"]}
            tried.append((m, f"ok:{len(cd)} codec={rinfo['chosen']} predict={rinfo['predict_seconds']*1000:.1f}ms "
                             f"encode={t_enc:.1f}s encode+verify={time.time()-t0:.1f}s"))
            candidates.append((len(cd), rmethod, cd, None, {"qzip_route": route}))
            continue
        if m == TEXT_METHOD:
            T = _text_codec()
            if T is None:
                tried.append((m, "not_installed(hook: src/qzip_text.py)")); continue
            try:
                cd = T.compress(data)
                ok = hashlib.sha256(T.decompress(cd)).hexdigest() == osha
            except Exception as e:
                tried.append((m, f"error:{type(e).__name__}")); continue
            if not ok:
                tried.append((m, "roundtrip_FAIL")); continue
            tried.append((m, f"ok:{len(cd)}")); candidates.append((len(cd), TEXT_METHOD, cd, None, {})); continue
        try:
            if m == LEGACY_METHOD:
                R.set_level(LEVEL_OF.get(mode, "fast") if mode else "fast")
                try:
                    r = _legacy_engine().compress_memory_data(data, "hybrid_quantum")
                    route = R.last_route()
                finally:
                    R.set_level(None)
                # keep the sub-method the Oct-2025 encoder actually used (e.g. medical_imaging_direct);
                # overwriting it breaks decoding of those paths
                if isinstance(r, dict) and not r.get('compression_method'): r['compression_method'] = "hybrid_quantum"
            else:
                r = engine.compress_memory_data(data, m)
        except Exception as e:
            tried.append((m, f"compress_error:{type(e).__name__}:{str(e)[:120]}")); continue
        if not isinstance(r, dict) or 'error' in r:
            tried.append((m, "no_result" + (f":{str(r.get('error'))[:120]}" if isinstance(r, dict) else ""))); continue
        t_enc = time.time() - t0
        cd = _b(r.get('compressed_data')); md = r.get('reconstruction_metadata')
        mdb = _b(md) if md not in (None, '', b'') else None
        rmethod = r.get('compression_method', m)
        qp = dict(r.get('quantum_parameters') or {}) if isinstance(r.get('quantum_parameters'), dict) else {}
        decoder = "current"
        if rmethod in R.DECODERS or rmethod == 'stored':
            try:
                dec = R.decode(rmethod, cd)
            except Exception:
                dec = None
            decoder = rmethod
        else:
            if not allow_rl and len(cd) >= 1 and cd[0] == RL_FLAG:
                tried.append((m, "rejected_RL_path(0x80,needs 12.8GB model)")); continue
            dec = _engine_roundtrip(engine, r)  # CURRENT decoder first
            if (dec is None or hashlib.sha256(dec).hexdigest() != osha) and m == LEGACY_METHOD:
                # Some Oct-2025 paths (e.g. medical) need the Oct-2025 decoder; record that in the container.
                dec = _engine_roundtrip(_legacy_engine(), r)
                decoder = LEGACY_METHOD
        if dec is None or hashlib.sha256(dec).hexdigest() != osha:
            tried.append((m, f"roundtrip_FAIL({rmethod})")); continue
        payload = len(cd) + (len(mdb) if mdb else 0)
        tried.append((m, f"ok:{payload} method={rmethod} encode={t_enc:.1f}s encode+verify={time.time()-t0:.1f}s"))
        if decoder == LEGACY_METHOD:
            qp['_qzip_decoder'] = decoder
        candidates.append((payload, rmethod, cd, mdb, qp))

    fallback = None
    if not candidates:   # only on failure: guaranteed-lossless general-purpose codec
        t0 = time.time()
        fm, z = _xz_fallback(data)
        fallback = fm
        tried.append((fm, f"fallback ok:{len(z)} {time.time()-t0:.1f}s"))
        candidates.append((len(z), fm, z, None, {}))
    candidates.append((len(data), 'stored', data, None, {}))
    candidates.sort(key=lambda c: c[0])
    payload, method, cd, mdb, qp = candidates[0]
    qp = dict(qp); dec_name = qp.pop('_qzip_decoder', 'current')
    rq = qp.pop('qzip_route', None) or route or {}
    chosen = fallback or ('stored' if method == 'stored' else (rq.get('chosen') or method))
    qp['qzip'] = {'orig_sha256': osha, 'orig_size': len(data), 'verified': True, 'format': 'qzip-1', 'decoder': dec_name,
                  'mode': mode or 'custom', 'detected_type': rq.get('detected_type'),
                  'type_reason': rq.get('type_reason'), 'chosen': chosen, 'route': rq.get('chain'),
                  'skipped': rq.get('skipped'), 'fallback': fallback}
    if rq.get('features'):
        qp['qzip']['features'] = rq['features']; qp['qzip']['predict_seconds'] = rq.get('predict_seconds')
    if rq.get('text_mode'):
        qp['qzip']['text_mode'] = rq['text_mode']
    container = {
        'compressed_data': base64.b64encode(cd).decode(),
        'reconstruction_metadata': base64.b64encode(mdb).decode() if mdb else None,
        'method': method,
        'quantum_parameters': qp,
        'orig_sha256': osha,
        'orig_size': len(data),
        'verified': True,
        'format': 'qzip-1',
    }
    info = {'chosen': method, 'payload': payload, 'orig': len(data),
            'ratio': round(len(data)/payload, 4) if payload else None, 'tried': tried,
            'detected_type': rq.get('detected_type'), 'route_choice': chosen, 'route': rq.get('chain'),
            'fallback': fallback, 'mode': mode or 'custom', 'seconds': round(time.time() - t_start, 2)}
    return container, info

def expected_sha(container):
    qp = container.get('quantum_parameters') or {}
    return container.get('orig_sha256') or (qp.get('qzip') or {}).get('orig_sha256')

def safe_decompress(engine, container):
    """Decode a QZip container (.qz, or older .qc.gz / .qc incl. pre-2026 artifacts); verify sha256 if recorded."""
    method = container.get('method') or container.get('compression_method') or 'hybrid_quantum'
    cd = _b(container.get('compressed_data'))
    if method == 'stored':
        out = cd
    elif method == 'stored_xz':
        out = lzma.decompress(cd)
    elif method in R.DECODERS or method in ('qtyp', 'lpaq', 'mcm'):
        out = R.decode(method, cd)          # routed codecs (src/qzip_routes.py registry)
    elif method == TEXT_METHOD:
        T = _text_codec()
        if T is None:
            raise RuntimeError("container uses the 'text' mode but src/qzip_text.py is not installed")
        out = T.decompress(cd)
    else:
        md = container.get('reconstruction_metadata')
        qp = dict(container.get('quantum_parameters') or {}); qz = qp.pop('qzip', None) or {}
        dec_engine = _legacy_engine() if qz.get('decoder') == LEGACY_METHOD else engine
        exp = expected_sha(container)
        # 1) exactly what the 2025 web app sent the engine (base64 strings from the .qc JSON);
        # 2) decoded bytes (some engine paths accept either). With a recorded sha256 we keep the one that matches.
        reqs = [{'compressed_data': container.get('compressed_data'), 'compression_method': method,
                 'reconstruction_metadata': md, 'quantum_parameters': qp},
                {'compressed_data': cd, 'compression_method': method,
                 'reconstruction_metadata': _b(md) if md else None, 'quantum_parameters': qp}]
        out, first, err = None, None, None
        for req in reqs:
            try:
                raw = dec_engine.decompress_memory_data(copy.deepcopy(req))
            except Exception as e:
                err = e; continue
            cands = [_tobytes(raw)]
            if isinstance(raw, str):
                try: cands.append(base64.b64decode(raw, validate=True))
                except Exception: pass
            for c in cands:
                if first is None: first = c
                if exp and hashlib.sha256(c).hexdigest() == exp:
                    out = c; break
            if out is not None: break
        if out is None:
            if first is None:
                raise err or RuntimeError("engine could not decode container")
            out = first
    exp = expected_sha(container)
    if exp and hashlib.sha256(out).hexdigest() != exp:
        raise ValueError("decoded bytes do not match recorded sha256")
    return out
