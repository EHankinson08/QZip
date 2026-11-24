"""QZip fast codecs: type-aware column mode (qtyp) and block-parallel lpaq1 context mixing.
(c) 2026 Elliott Hankinson. QZip, GPL-3.0-or-later. Helper binaries in src/native (built by `make native`):
  qtyp    src/native/fast/qtyp.c   (ours)   reversible type-aware column transform for CSV / TSV / logs
  qzlpaq  src/native/fast/lpaq1.cpp (lpaq1, (C) 2007 Matt Mahoney, GPL-2.0-or-later, used under GPL-3.0) run as a separate program

Blob formats (compressed_data of a .qc container, reconstruction_metadata=None):
  b'\\x03QC\\x02' + u32 nstreams + nstreams*(u8 namelen, name, u8 backend, u64 rawlen, u64 clen) + payloads   (qtyp)
      backend: 0 stored | 1 lpaq1 | 4 xz (LZMA preset 9e) | 5 libbsc | 6 zstd-19 long
  b'\\x03QF\\x01' + u8 codec(1=lpaq1) + u8 flags + u32 nblocks + nblocks*(u64 rawlen, u64 clen) + payloads   (block lpaq)
Each qtyp stream gets its own smallest backend; per column the typed streams or the plain text form are kept,
whichever is smaller. Blocks / streams are compressed and decompressed in parallel."""
import concurrent.futures as cf, lzma, os, re, struct, subprocess, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.environ.get('QZIP_FAST_BIN', os.path.join(_HERE, 'native'))
QTYP = os.path.join(NATIVE, 'qtyp')
LPAQ = os.path.join(NATIVE, 'qzlpaq')
CMAGIC, FMAGIC = b'\x03QC', b'\x03QF'
JOBS = int(os.environ.get('QZIP_FAST_JOBS', '12'))
XZF = [{'id': lzma.FILTER_LZMA2, 'preset': 9 | lzma.PRESET_EXTREME}]

def _native():
    try:
        from src.native import qz_native as N
    except ImportError:
        from native import qz_native as N
    return N

def edition():
    return _native().edition()

def qtyp_available():
    return os.access(QTYP, os.X_OK)

def lpaq_available():
    return os.access(LPAQ, os.X_OK)

def _tmp():
    return tempfile.TemporaryDirectory(dir=os.environ.get('QZIP_TMP') or None)

def _lpaq(args):
    subprocess.check_call([LPAQ] + args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _lpaq_c(raw, td, tag):
    i = os.path.join(td, tag); o = i + '.lpq'
    with open(i, 'wb') as f: f.write(raw)
    _lpaq(['9', i, o]); r = open(o, 'rb').read(); os.remove(i); os.remove(o); return r

def _lpaq_d(comp, td, tag):
    if not os.access(LPAQ, os.X_OK):
        raise RuntimeError('this .qc uses lpaq1: build it with `make native`')
    i = os.path.join(td, tag); o = i + '.out'
    with open(i, 'wb') as f: f.write(comp)
    _lpaq(['d', i, o]); r = open(o, 'rb').read(); os.remove(i); os.remove(o); return r

def _zstd():
    import zstandard
    return zstandard

def _enc(b, raw, td, tag):
    if b == 4: return lzma.compress(raw, format=lzma.FORMAT_XZ, filters=XZF)
    if b == 5: return _native().bsc_compress(raw, threads=2)
    if b == 6:
        zs = _zstd(); p = zs.ZstdCompressionParameters.from_level(19, window_log=min(27, max(10, (len(raw) - 1).bit_length())), enable_ldm=True)
        return zs.ZstdCompressor(compression_params=p).compress(raw)
    if b == 1: return _lpaq_c(raw, td, tag)
    raise ValueError(b)

def _dec(b, comp, td, tag):
    if b == 0: return comp
    if b == 4: return lzma.decompress(comp)
    if b == 5: return _native().bsc_decompress(comp, threads=2)
    if b == 6: return _zstd().ZstdDecompressor().decompressobj().decompress(comp)
    if b == 1: return _lpaq_d(comp, td, tag)
    raise ValueError(b)

def backends():
    out = [4]
    try:
        if _native().lib_available(): out.append(5)
    except Exception: pass
    try:
        _zstd(); out.append(6)
    except ImportError: pass
    if lpaq_available(): out.append(1)
    return tuple(out)

# ---------------------------------------------------------------- qtyp column mode
def qtyp_compress(data, jobs=None):
    bks = backends()
    with _tmp() as td:
        ip = os.path.join(td, 'in'); sd = os.path.join(td, 's'); os.mkdir(sd)
        with open(ip, 'wb') as f: f.write(data)
        subprocess.check_call([QTYP, 'e', ip, sd], stderr=subprocess.DEVNULL); os.remove(ip)
        names = sorted(os.listdir(sd)); raws = {n: open(os.path.join(sd, n), 'rb').read() for n in names}
        tasks = [(n, b) for n in names for b in bks if raws[n]]
        def job(t):
            n, b = t
            try: return t, _enc(b, raws[n], td, f'{n}.{b}')
            except Exception: return t, None
        with cf.ThreadPoolExecutor(jobs or JOBS) as ex:
            res = {k: v for k, v in ex.map(job, tasks) if v is not None}
    best = {}
    for n in names:
        bb = (0, raws[n])
        for b in bks:
            r = res.get((n, b))
            if r is not None and len(r) < len(bb[1]): bb = (b, r)
        best[n] = bb
    # per column keep the plain-text form (.x) OR the typed streams; per digit run keep varint (.nQQ) OR text (.tQQ)
    sz = lambda n: len(best[n][1]); drop = set()
    for c in sorted({n.split('.')[0] for n in names if re.match(r'c\d{3}\.', n)}):
        mine = [n for n in names if n.startswith(c + '.')]
        typed, tdrop = 0, set()
        for q in sorted({n[-2:] for n in mine if re.search(r'\.[nt]\d\d$', n)}):
            nn, tn = f'{c}.n{q}', f'{c}.t{q}'
            if nn in best and tn in best:
                if sz(tn) < sz(nn): tdrop.add(nn); typed += sz(tn)
                else: tdrop.add(tn); typed += sz(nn)
        typed += sum(sz(n) for n in mine if re.search(r'\.(f|r|d|e)$', n))
        xn = f'{c}.x'
        if xn in best:
            drop |= ({n for n in mine if n != xn} if sz(xn) <= typed else tdrop | {xn})
        else:
            drop |= tdrop
    names = [n for n in names if n not in drop]
    head = [CMAGIC + b'\x02' + struct.pack('<I', len(names))]
    for n in names:
        nb = n.encode(); head.append(struct.pack('<B', len(nb)) + nb + struct.pack('<BQQ', best[n][0], len(raws[n]), sz(n)))
    return b''.join(head) + b''.join(best[n][1] for n in names)

def qtyp_decompress(blob, jobs=None):
    assert blob[:4] == CMAGIC + b'\x02', 'not a qtyp blob'
    ns = struct.unpack('<I', blob[4:8])[0]; p = 8; ents = []
    for _ in range(ns):
        L = blob[p]; n = blob[p + 1:p + 1 + L].decode(); p += 1 + L
        b, rl, cl = struct.unpack('<BQQ', blob[p:p + 17]); p += 17; ents.append((n, b, rl, cl))
    with _tmp() as td:
        sd = os.path.join(td, 's'); os.mkdir(sd); offs = []
        for n, b, rl, cl in ents: offs.append((n, b, rl, p, cl)); p += cl
        def job(e):
            n, b, rl, q, cl = e
            r = _dec(b, blob[q:q + cl], td, n) if cl else b''
            if len(r) != rl: raise ValueError(f'qtyp stream {n}: length mismatch')
            with open(os.path.join(sd, n), 'wb') as f: f.write(r)
        with cf.ThreadPoolExecutor(jobs or JOBS) as ex: list(ex.map(job, offs))
        if not qtyp_available():
            raise RuntimeError('this .qc uses qtyp: build it with `make native`')
        op = os.path.join(td, 'out'); subprocess.check_call([QTYP, 'd', sd, op])
        return open(op, 'rb').read()

# ---------------------------------------------------------------- block-parallel lpaq1 (free-form logs)
def lpaq_compress(data, block_mb=48, jobs=None):
    n = len(data); nb = max(1, -(-n // (block_mb << 20))); sz = -(-n // nb) if n else 0
    spans = [(k, min(n, k + sz)) for k in range(0, n, sz)] if n else [(0, 0)]
    with _tmp() as td:
        def job(k):
            a, b = spans[k]; return _lpaq_c(data[a:b], td, f'b{k}')
        with cf.ThreadPoolExecutor(jobs or JOBS) as ex: outs = list(ex.map(job, range(len(spans))))
    hdr = FMAGIC + bytes([1, 1, 0]) + struct.pack('<I', len(spans))
    hdr += b''.join(struct.pack('<QQ', b - a, len(c)) for (a, b), c in zip(spans, outs))
    return hdr + b''.join(outs)

def lpaq_decompress(blob, jobs=None):
    assert blob[:5] == FMAGIC + b'\x01\x01', 'not a block-lpaq blob'
    nb = struct.unpack('<I', blob[6:10])[0]; p = 10 + 16 * nb; offs = []
    for k in range(nb):
        rl, cl = struct.unpack('<QQ', blob[10 + 16 * k:26 + 16 * k]); offs.append((p, cl, rl)); p += cl
    with _tmp() as td:
        def job(k):
            q, cl, rl = offs[k]; r = _lpaq_d(blob[q:q + cl], td, f'c{k}')
            if len(r) != rl: raise ValueError('lpaq block length mismatch')
            return r
        with cf.ThreadPoolExecutor(jobs or JOBS) as ex: return b''.join(ex.map(job, range(nb)))
