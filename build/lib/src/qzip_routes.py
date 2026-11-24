"""QZip router extension for the Oct-2025 engine (src/qzip_legacy_2025_10.py, `_hybrid_quantum_compress`).

The Oct-2025 engine already routes by type:
    _detect_data_type: UTF-8 text | medical_imaging (NIfTI/DICOM) | binary
    text   -> _detect_repetitive_pattern: "structured log" (consistent line lengths or any repeated line)
              -> ML pattern (ml_columnar_log) if smaller, else _neural_dictionary_compress:
              CSV (csv.reader: >100 rows, >1 column) -> _compress_csv_with_neural (ML columnar), else word dictionary
    medical_imaging -> medical_imaging_direct (LZMA)      binary -> _apply_binary_quantum_compression
This module refines that same detection (same engine helpers, plus image / compressed-format magic and a free-form log
sniff) and swaps each type's route to the best MEASURED method, keeping the old route where it is still best or where
the new method is not installed. Exactly ONE method is encoded; src/safe_compress.py then verifies it by decoding and
falls back to xz only if verification fails.

Routing (level = QZip mode fast | max | ultra; evidence in README "Routing table"):
    type            fast                max / ultra             if not installed
    text/markup     text_fast           text_max / text_ultra   text_fast; then old word-dictionary route
    csv             qtyp                qtyp                    old ML-columnar CSV route (kept: beats text_fast)
    structured log  qtyp                text_max / text_ultra   xz (beats text_fast on HealthApp.log)
    free-form log   lpaq                text_max / text_ultra   text_fast
    medical         old medical_imaging_direct route (unchanged)
    image           xz (old binary route made JPEGs larger)
    compressed      stored (already compressed: zip/gz/xz/zstd/pdf/media)
    other binary    xz (zstd above 32 MiB)

Candidates are pluggable: register_candidate(name, compress, decompress, method=None, available=None).
src/qzip_fast_codecs.py (qtyp, lpaq) plugs in via its register(registry) function when installed; modules in
src/candidates/ and $QZIP_CANDIDATE_MODULES are loaded the same way.
"""
import base64, bz2, importlib, io, lzma, os, pkgutil, re, threading, time
from datetime import datetime

MB = 1 << 20
LEVELS = ("fast", "max", "ultra")
NATIVE_TEXT_METHOD = "qzip_text"
TEXT_FAST, TEXT_MAX, TEXT_ULTRA = "text_fast", "text_max", "text_ultra"
OLD = "old_route"          # keep the Oct-2025 engine's own route
_T = {"fast": [TEXT_FAST, OLD], "max": [TEXT_MAX, TEXT_FAST, OLD], "ultra": [TEXT_ULTRA, TEXT_MAX, TEXT_FAST, OLD]}
ROUTES = {   # type -> level -> preference chain (first AVAILABLE entry is used; only one is encoded)
    "text":           _T,
    "csv":            {lv: ["qtyp", OLD] for lv in LEVELS},
    "log_structured": {"fast": ["qtyp", "xz"], "max": [TEXT_MAX, "qtyp", "xz"],
                       "ultra": [TEXT_ULTRA, TEXT_MAX, "qtyp", "xz"]},
    "log":            {"fast": ["lpaq", TEXT_FAST, OLD], "max": [TEXT_MAX, "lpaq", TEXT_FAST, OLD],
                       "ultra": [TEXT_ULTRA, TEXT_MAX, "lpaq", TEXT_FAST, OLD]},
    "medical":        {lv: [OLD] for lv in LEVELS},
    "image":          {lv: ["xz"] for lv in LEVELS},
    "compressed":     {lv: ["stored"] for lv in LEVELS},
    "binary":         {lv: ["xz_or_zstd"] for lv in LEVELS},
}
TYPES = tuple(ROUTES)

_CTX = threading.local()   # per-request level (None = router off: pure Oct-2025 routes, i.e. mode 'legacy')

def set_level(level):
    _CTX.level = level

def current_level():
    return getattr(_CTX, "level", None)

def last_route():
    return getattr(_CTX, "route", None)


# ------------------------------------------------------------------ candidate registry
_REGISTRY = {}   # name -> {compress, decompress, method, available}
DECODERS = {}    # container method -> decompress(bytes) -> bytes
PLUGIN_STATUS = {}

def register_decoder(method, decompress):
    DECODERS[method] = decompress

def register_candidate(name, compress, decompress, method=None, available=None):
    """compress(bytes)->bytes, decompress(bytes)->bytes (byte-exact inverse). method = container method written to
    .qc files (default name). available() -> True or a reason string. ROUTES decides where a name is used."""
    method = method or name
    _REGISTRY[name] = {"compress": compress, "decompress": decompress, "method": method, "available": available}
    register_decoder(method, decompress)

class _DictRegistry(dict):
    """Adapter for modules exposing register(registry) with registry[name] = (compress, decompress)."""
    def __setitem__(self, name, pair):
        super().__setitem__(name, pair)
        register_candidate(name, pair[0], pair[1])

def _native():
    try:
        from src.native import qz_native as N
    except ImportError:
        from native import qz_native as N
    return N

def _native_candidate(level):
    def enc(data): return _native().compress(data, level)
    def avail():
        N = _native()
        if level in N.CM_LEVELS:
            return N.cm_available() or "unavailable(qzcm not built: run `make native`)"
        return N.lib_available() or "unavailable(libqznative.so not built: run `make native`)"
    return enc, avail

def _native_decode(blob):
    N = _native()
    try:
        return N.decompress(blob)
    except N.NativeUnavailable as e:
        raise RuntimeError(f"this .qc uses the QZip native text mode: {e}") from e

def _zstd_c(data):
    import zstandard as zs
    p = zs.ZstdCompressionParameters.from_level(19, window_log=min(27, max(10, (len(data) - 1).bit_length())), enable_ldm=True)
    return zs.ZstdCompressor(compression_params=p, threads=-1).compress(data)

def _zstd_d(blob):
    import zstandard as zs
    return zs.ZstdDecompressor().decompressobj().decompress(blob)

def zstd_available():
    try:
        import zstandard  # noqa: F401
        return True
    except ImportError:
        return "unavailable(pip install zstandard)"

for _n, _lv in ((TEXT_FAST, "fast"), (TEXT_MAX, "max"), (TEXT_ULTRA, "ultra")):
    _e, _a = _native_candidate(_lv)
    register_candidate(_n, _e, _native_decode, method=NATIVE_TEXT_METHOD, available=_a)
register_candidate("xz", lambda b: lzma.compress(b, preset=9), lzma.decompress, method="stored_xz")
register_candidate("zstd", _zstd_c, _zstd_d, method="stored_zstd", available=zstd_available)
register_candidate("bzip2", lambda b: bz2.compress(b, 9), bz2.decompress, method="stored_bz2")

_LOADED = []

def load_plugins():
    """Import candidate modules once: src.qzip_fast_codecs, src/candidates/*, $QZIP_CANDIDATE_MODULES."""
    if _LOADED:
        return PLUGIN_STATUS
    _LOADED.append(True)
    names = ["src.qzip_fast_codecs"]
    try:
        import src.candidates as C
        names += [f"src.candidates.{m.name}" for m in pkgutil.iter_modules(C.__path__) if not m.name.startswith("_")]
    except ImportError:
        pass
    names += [n.strip() for n in os.environ.get("QZIP_CANDIDATE_MODULES", "").split(",") if n.strip()]
    for n in names:
        try:
            mod = importlib.import_module(n)
        except ImportError as e:
            if n != "src.qzip_fast_codecs":
                PLUGIN_STATUS[n] = f"import_error:{e}"
            continue
        except Exception as e:  # a broken plug-in never breaks compression
            PLUGIN_STATUS[n] = f"error:{type(e).__name__}:{e}"; continue
        try:
            if hasattr(mod, "register"):
                reg = _DictRegistry(); mod.register(reg)
                PLUGIN_STATUS[n] = "ok:" + (",".join(reg) or "(no binaries available)")
                if hasattr(mod, "decompress"):   # decode their containers even where the encoder binary is missing
                    for m in getattr(mod, "CODECS", {}):
                        DECODERS.setdefault(m, mod.decompress)
            else:
                PLUGIN_STATUS[n] = "ok"
        except Exception as e:
            PLUGIN_STATUS[n] = f"register_error:{type(e).__name__}:{e}"
    return PLUGIN_STATUS


# ------------------------------------------------------------------ type refinement (extends the engine's detection)
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"II*\x00", b"MM\x00*", b"8BPS",
                b"\x00\x00\x00\x0cjP  ", b"\xff\x0a")
_COMPRESSED_MAGIC = (b"\x1f\x8b", b"\xfd7zXZ\x00", b"PK\x03\x04", b"BZh", b"\x28\xb5\x2f\xfd", b"7z\xbc\xaf\x27\x1c",
                     b"Rar!", b'\x04"M\x18', b"%PDF", b"\x1a\x45\xdf\xa3", b"OggS", b"fLaC", b"ID3")
_LOG_RE = re.compile(r"^(\[?\d{4}[-/.]\d{2}[-/.]\d{2}[ T_]\d{1,2}:\d{2}|\[?\d{2}[-/.]\d{2}[-/.]?\d{0,4}[ T_]\d{1,2}:\d{2}|"
                     r"\[?(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s|\[?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d|"
                     r"\d{8}[-_ T]\d{2}:?\d{2}:?\d{2}|\d{10}(\.\d+)?\s|\S+\.log[\w.:-]*\s)")

def _sample_text(data, n=1 << 20):
    s = bytes(data[:n])
    if len(data) > n:
        cut = s.rfind(b"\n")
        s = s[:cut + 1] if cut > 0 else s
    return s.decode("utf-8", errors="ignore")

def refine_type(engine, data, data_type):
    """Map the engine's data_type (text | medical_imaging | binary) to a router type. Returns (type, reason)."""
    head = bytes(data[:16])
    if data_type == "medical_imaging":
        return "medical", "engine: NIfTI/DICOM"
    if head.startswith(_IMAGE_MAGIC) or (head[:4] == b"RIFF" and data[8:12] == b"WEBP") or \
            (head[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"avif", b"mif1")):
        return "image", "image magic"
    if head.startswith(_COMPRESSED_MAGIC):
        return "compressed", "compressed-format magic"
    if data_type != "text":
        return "binary", "engine: binary"
    text = _sample_text(data)
    lines = [l for l in text.split("\n")[:2000] if l.strip()]
    # (the engine's own _detect_repetitive_pattern 'is_structured_log' flag is not used for routing: it is true for
    #  any input with consistent line lengths or one repeated line, i.e. most CSVs and much prose)
    logish = sum(1 for l in lines if _LOG_RE.match(l.lstrip()))
    is_log = len(lines) >= 20 and logish >= 0.6 * len(lines)
    # 2) the engine's CSV test (csv.reader: >100 rows and >1 column), on a sample, with a consistent-width check
    if not is_log and len(lines) > 100:
        import csv
        try:
            rows = list(csv.reader(io.StringIO("\n".join(lines[:1000]))))
            w = max(set(len(r) for r in rows), key=[len(r) for r in rows].count)
            if w > 1 and sum(1 for r in rows if len(r) == w) >= 0.9 * len(rows):
                return "csv", f"engine CSV test ({w} columns)"
        except Exception:
            pass
        for d in ("\t", "|", ";"):
            c = [l.count(d) for l in lines[:1000]]
            c0 = max(set(c), key=c.count)
            if c0 >= 1 and sum(1 for x in c if x == c0) >= 0.9 * len(c):
                return "csv", f"delimited ({repr(d)}, {c0 + 1} columns)"
    if is_log:
        for sep in (" - ", " | ", "|", "\t"):
            c = [l.count(sep) for l in lines]
            c0 = max(set(c), key=c.count)
            if c0 >= 2 and sum(1 for x in c if x == c0) >= 0.8 * len(c):
                return "log_structured", f"log with fixed {sep.strip() or 'tab'!r} fields"
        return "log", f"free-form log ({logish}/{len(lines)} timestamped lines)"
    return "text", "text/markup"


# ------------------------------------------------------------------ the routed encode (one method)
def _available(name):
    c = _REGISTRY.get(name)
    if c is None:
        return "not_installed"
    if c["available"] is None:
        return True
    try:
        return c["available"]()
    except Exception as e:
        return f"unavailable({type(e).__name__}: {e})"

def choose(engine, data, data_type, level):
    """Return (type, reason, chain, chosen_name, skipped[(name, why)])."""
    load_plugins()
    rtype, why = refine_type(engine, data, data_type)
    chain = list(ROUTES[rtype][level])
    skipped = []
    text_min = int(os.environ.get("QZIP_TEXT_MIN", str(64 << 10)))
    xz_max = int(os.environ.get("QZIP_XZ_MAX", str(32 * MB)))
    for name in chain:
        if name in (OLD, "stored"):
            return rtype, why, chain, name, skipped
        if name == "xz_or_zstd":
            name = "zstd" if len(data) > xz_max and zstd_available() is True else "xz"
        if name.startswith("text_") and len(data) < text_min:
            skipped.append((name, f"input < {text_min} B")); continue
        av = _available(name)
        if av is True:
            return rtype, why, chain, name, skipped
        skipped.append((name, av))
    return rtype, why, chain, OLD, skipped

def route_compress(engine, data, data_type):
    """Called at the top of the Oct-2025 `_hybrid_quantum_compress`. Returns a result dict in the engine's format,
    or None to keep the engine's own route for this type."""
    level = current_level()
    _CTX.route = None
    if level is None or not isinstance(data, (bytes, bytearray)):
        return None
    rtype, why, chain, name, skipped = choose(engine, data, data_type, level)
    route = {"detected_type": rtype, "type_reason": why, "engine_data_type": data_type, "level": level,
             "chain": chain, "chosen": name, "skipped": skipped}
    _CTX.route = route
    if name == OLD:
        route["chosen"] = "oct2025_route"
        return None
    t0 = time.time()
    if name == "stored":
        method, blob = "stored", bytes(data)
    else:
        c = _REGISTRY[name]
        blob = c["compress"](bytes(data)); method = c["method"]
    route["encode_seconds"] = round(time.time() - t0, 2)
    qp = {"qzip_route": route}
    if method == NATIVE_TEXT_METHOD:
        qp["qzip_route"]["text_mode"] = blob[4:5].decode("latin-1")
    return {
        "compressed_data": base64.b64encode(blob).decode("utf-8"),
        "compression_method": method,
        "original_size": len(data),
        "compressed_size": len(blob),
        "compression_ratio": len(data) / max(1, len(blob)),
        "reconstruction_metadata": None,
        "quantum_parameters": qp,
        "achieves_target": False,
        "timestamp": datetime.now().isoformat(),
    }

def decode(method, blob):
    """Decode a routed container (method written by route_compress). KeyError if the method is not a routed one."""
    load_plugins()
    if method == "stored":
        return bytes(blob)
    if method not in DECODERS:
        if method in ("qtyp", "lpaq", "mcm"):
            raise RuntimeError(f"this .qc uses the {method!r} codec (src/qzip_fast_codecs.py), which is not installed")
        raise KeyError(method)
    return DECODERS[method](blob)

def is_routed_method(method):
    return method in DECODERS or method in ("qtyp", "lpaq", "mcm") or (load_plugins() is not None and method in DECODERS)
