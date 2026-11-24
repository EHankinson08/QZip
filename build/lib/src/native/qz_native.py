"""QZip native text codecs (BWT / CM text modes), used by src/safe_compress.py.

In-process (ctypes, libqznative.so built from vendored sources by `make native`):
  - QWRT word-replacing transform (QZip, GPL-3.0-or-later)
  - libbsc 3.4.1 BWT + QLFC entropy coder and libsais (Apache-2.0, Ilya Grebnov), parallel blocks via OpenMP
Vendored helper binary (built by `make native`):
  - qzcm: mcm 0.84 context-mixing compressor (GPL-3.0-or-later, Mathieu Chartier), patched to build on GCC 13

Levels (QZip modes):  fast  = W (QWRT + libbsc)
                      max   = P, 4 parallel segments x mcm -h11
                      ultra = P, 2 parallel segments x mcm -x11 (slowest, smallest)

Blob format (stored in compressed_data, reconstruction_metadata=None):
  b'\\x04QZT' + mode(1) + body
    mode b'B': body = QZB1 container (libbsc)
    mode b'W': body = QZB1 container of QWRT(data)
    mode b'M': body = mcm archive
    mode b'N': body = mcm archive of QWRT(data)
    mode b'P': body = parallel-segment container of mcm archives

Environment: QZIP_AUTOBUILD=0 disables the one-time `make` when the library is missing, QZIP_THREADS (8), QZIP_TMP (temp dir for qzcm), QZIP_CM_LEVEL /
QZIP_CM_SEGMENTS override the max/ultra mcm level and segment count.
"""
import ctypes, os, subprocess, tempfile, time, threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB = None
_BUILD = {'tried': False, 'error': None}
_LOCK = threading.Lock()
MAGIC = b'\x04QZT'


class NativeUnavailable(RuntimeError):
    """The native library / helper is not built."""


def edition():
    """Kept for API compatibility: QZip has a single GPL-3.0-or-later build."""
    return 'gpl'


def _try_build():
    """One `make` per process if the library is missing (QZIP_AUTOBUILD=0 disables). Never raises."""
    with _LOCK:
        if _BUILD['tried']:
            return
        _BUILD['tried'] = True
        if os.environ.get('QZIP_AUTOBUILD', '1') == '0':
            _BUILD['error'] = 'autobuild disabled'; return
        try:
            subprocess.run(['make', '-C', _HERE, '-j%d' % max(1, os.cpu_count() or 1)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=900)
        except Exception as e:
            _BUILD['error'] = f'{type(e).__name__}: {getattr(e, "stderr", b"")[-300:] if hasattr(e, "stderr") else e}'


def _lib():
    global _LIB
    if _LIB is None:
        path = os.path.join(_HERE, 'libqznative.so')
        if not os.path.exists(path):
            _try_build()
        if not os.path.exists(path):
            raise NativeUnavailable('libqznative.so not built (run `make native`)' +
                                    (f'; autobuild: {_BUILD["error"]}' if _BUILD['error'] else ''))
        L = ctypes.CDLL(path)
        P = ctypes.POINTER
        u8p = ctypes.c_char_p
        outp = P(ctypes.c_void_p)
        szp = P(ctypes.c_size_t)
        L.qz_wrt_encode.argtypes = [u8p, ctypes.c_size_t, outp, szp, ctypes.c_long, ctypes.c_long, ctypes.c_int]
        L.qz_wrt_decode.argtypes = [u8p, ctypes.c_size_t, outp, szp]
        L.qz_bsc_compress.argtypes = [u8p, ctypes.c_size_t, outp, szp, ctypes.c_size_t, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        L.qz_bsc_decompress.argtypes = [u8p, ctypes.c_size_t, outp, szp, ctypes.c_int, ctypes.c_int]
        L.qz_free.argtypes = [ctypes.c_void_p]
        _LIB = L
    return _LIB


def lib_available():
    try:
        _lib(); return True
    except Exception:
        return False


def cm_available():
    path = os.path.join(_HERE, 'qzcm')
    if not os.path.exists(path):
        _try_build()
    return os.path.exists(path)


def _take(L, ptr, n):
    try:
        return ctypes.string_at(ptr.value, n.value)
    finally:
        L.qz_free(ptr)


def wrt_encode(data, maxd=20000, minf=4, sp=0):
    L = _lib(); p = ctypes.c_void_p(); n = ctypes.c_size_t()
    if L.qz_wrt_encode(bytes(data), len(data), ctypes.byref(p), ctypes.byref(n), maxd, minf, sp) != 0:
        raise RuntimeError('qwrt encode failed')
    return _take(L, p, n)


def wrt_decode(data):
    L = _lib(); p = ctypes.c_void_p(); n = ctypes.c_size_t()
    if L.qz_wrt_decode(bytes(data), len(data), ctypes.byref(p), ctypes.byref(n)) != 0:
        raise RuntimeError('qwrt decode failed')
    return _take(L, p, n)


def _threads():
    return int(os.environ.get('QZIP_THREADS', '8'))


def bsc_compress(data, block=1 << 30, lzp_hash=0, lzp_min=0, sorter=1, coder=2, features=None, threads=None):
    """coder 2 = QLFC adaptive (bsc -e2). features: 1 fast mode | 2 multithreading."""
    L = _lib(); p = ctypes.c_void_p(); n = ctypes.c_size_t()
    if features is None:
        features = 1 | 2
    r = L.qz_bsc_compress(bytes(data), len(data), ctypes.byref(p), ctypes.byref(n), block, lzp_hash, lzp_min,
                          sorter, coder, features, threads or _threads())
    if r != 0:
        raise RuntimeError('libbsc compress error %d' % r)
    return _take(L, p, n)


def bsc_decompress(data, features=None, threads=None):
    L = _lib(); p = ctypes.c_void_p(); n = ctypes.c_size_t()
    if features is None:
        features = 1 | 2
    r = L.qz_bsc_decompress(bytes(data), len(data), ctypes.byref(p), ctypes.byref(n), features, threads or _threads())
    if r != 0:
        raise RuntimeError('libbsc decompress error %d' % r)
    return _take(L, p, n)


def _qzcm():
    if not cm_available():
        raise NativeUnavailable('qzcm not built (run `make native`)')
    return os.path.join(_HERE, 'qzcm')


def _workdir():
    d = os.environ.get('QZIP_TMP') or None
    return tempfile.TemporaryDirectory(dir=d)


def cm_compress(data, level='-x11'):
    with _workdir() as td:
        i = os.path.join(td, 'in'); open(i, 'wb').write(data)
        subprocess.check_call([_qzcm(), level, i, 'arc.mcm'], cwd=td, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return open(os.path.join(td, 'arc.mcm'), 'rb').read()


def cm_decompress(blob):
    with _workdir() as td:
        open(os.path.join(td, 'arc.mcm'), 'wb').write(blob)
        # mcm restores the stored member name ('in'); output name argument is ignored by mcm
        subprocess.check_call([_qzcm(), 'd', 'arc.mcm'], cwd=td, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return open(os.path.join(td, 'in'), 'rb').read()


def _split(data, k):
    """k contiguous segments, each boundary moved to just after the next newline (if one is near)."""
    n = len(data); cuts = [0]
    for j in range(1, k):
        t = n * j // k
        nl = data.find(b'\n', t, t + (1 << 20))
        c = nl + 1 if nl >= 0 else t
        if c <= cuts[-1]: c = cuts[-1]
        cuts.append(c)
    cuts.append(n)
    return [data[cuts[j]:cuts[j + 1]] for j in range(k)]


def pcm_compress(data, level='-x11', k=2, wrt=None):
    """Parallel CM: k independent segments compressed concurrently by k qzcm processes.
    Container: u8 flags(bit0=QWRT per segment) u16 k, k x u64 seglen, then segment archives."""
    from concurrent.futures import ThreadPoolExecutor
    segs = _split(data, k)
    def one(seg):
        if wrt: seg = wrt_encode(seg, *wrt)
        return cm_compress(seg, level)
    with ThreadPoolExecutor(max_workers=k) as ex:
        arcs = list(ex.map(one, segs))
    hdr = bytes([1 if wrt else 0]) + len(arcs).to_bytes(2, 'little') + b''.join(len(a).to_bytes(8, 'little') for a in arcs)
    return hdr + b''.join(arcs)


def pcm_decompress(body):
    from concurrent.futures import ThreadPoolExecutor
    flags = body[0]; k = int.from_bytes(body[1:3], 'little'); p = 3; lens = []
    for _ in range(k):
        lens.append(int.from_bytes(body[p:p + 8], 'little')); p += 8
    arcs = []
    for L in lens:
        arcs.append(body[p:p + L]); p += L
    if p != len(body): raise ValueError('pcm container length mismatch')
    def one(a):
        out = cm_decompress(a)
        return wrt_decode(out) if flags & 1 else out
    with ThreadPoolExecutor(max_workers=k) as ex:
        return b''.join(ex.map(one, arcs))


# ---- candidate modes ----
def _wrt_opts(o): return o.get('maxd', 20000), o.get('minf', 4), o.get('sp', 0)
def _enc_B(d, o): return bsc_compress(d, block=o.get('block', 1 << 30))
def _enc_W(d, o): return bsc_compress(wrt_encode(d, *_wrt_opts(o)), block=o.get('block', 1 << 30))
def _enc_M(d, o): return cm_compress(d, o.get('cm', '-x11'))
def _enc_N(d, o): return cm_compress(wrt_encode(d, *_wrt_opts(o)), o.get('cm', '-x11'))

def _enc_P(d, o):
    k = int(o.get('k', 2))
    k = max(1, min(k, len(d) // (64 << 20)))  # no splitting below 64 MiB per segment
    return pcm_compress(d, o.get('cm', '-x11'), k)

ENC = {b'B': _enc_B, b'W': _enc_W, b'M': _enc_M, b'N': _enc_N, b'P': _enc_P}
# level -> [(mode, opts)]; smallest wins. Measured on enwik9 through the web path (PC2, Nov 2026):
#   fast  W            162,473,695 B  127 s / 40 s
#   max   P k=4 -h11   146,200,704 B  236 s / 141 s (incl. verify)
#   ultra P k=2 -x11   141,839,139 B  957 s / 260 s (incl. verify)
LEVELS = {
    'fast':  [(b'W', {})],
    'max':   [(b'P', {'k': 4, 'cm': '-h11'})],
    'ultra': [(b'P', {'k': 2, 'cm': '-x11'})],
    'all':   [(b'B', {}), (b'W', {}), (b'M', {}), (b'N', {})],
}
CM_LEVELS = ('max', 'ultra')


def level_opts(level):
    out = []
    for m, o in LEVELS[level]:
        o = dict(o)
        if m in (b'P', b'M', b'N') and level in CM_LEVELS:
            if os.environ.get('QZIP_CM_LEVEL'): o['cm'] = os.environ['QZIP_CM_LEVEL']
            if os.environ.get('QZIP_CM_SEGMENTS'): o['k'] = int(os.environ['QZIP_CM_SEGMENTS'])
        out.append((m, o))
    return out


def compress(data, level='fast', log=None):
    """Return the smallest blob over the level's modes. Raises NativeUnavailable if none can run."""
    if level in CM_LEVELS and not cm_available():
        raise NativeUnavailable('CM modes need the qzcm helper (run `make native`)')
    best, err = None, None
    for m, o in level_opts(level):
        t = time.time()
        try:
            body = ENC[m](data, o)
        except NativeUnavailable:
            raise
        except Exception as e:  # a failing candidate is skipped, never shipped
            err = e
            if log: log(f'qz_native mode {m!r} failed: {e}')
            continue
        blob = MAGIC + m + body
        if log: log(f'qz_native mode {m.decode()} -> {len(blob):,} B in {time.time()-t:.1f}s')
        if best is None or len(blob) < len(best):
            best = blob
    if best is None:
        raise RuntimeError(f'all native modes failed: {err}')
    return best


def decompress(blob):
    if blob[:4] != MAGIC:
        raise ValueError('not a qz_native blob')
    m = blob[4:5]; body = blob[5:]
    if m == b'B': return bsc_decompress(body)
    if m == b'W': return wrt_decode(bsc_decompress(body))
    if m == b'M': return cm_decompress(body)
    if m == b'N': return wrt_decode(cm_decompress(body))
    if m == b'P': return pcm_decompress(body)
    raise ValueError('unknown qz_native mode %r' % m)


_PRINT = bytes(range(32, 127)) + b'\t\n\r'


def is_text_like(data, min_size=64 << 10):
    """Letter-heavy text (>=45% ASCII letters in the first 1 MiB): prose, markup, logs."""
    if len(data) < min_size:
        return False
    s = data[:1 << 20]
    letters = sum(s.count(bytes([c])) for c in range(65, 91)) + sum(s.count(bytes([c])) for c in range(97, 123))
    return letters >= 0.45 * len(s)


def is_textish(data, min_size=64 << 10):
    """Any mostly-printable input (text, logs, CSV/TSV, JSON, XML, source): >=85% printable ASCII/UTF-8-ish bytes."""
    if len(data) < min_size:
        return False
    s = bytes(data[:1 << 20])
    bad = len(s.translate(None, _PRINT))
    hi = sum(s.count(bytes([c])) for c in range(128, 256))  # UTF-8 multibyte text counts as text
    return (bad - hi) <= 0.02 * len(s) and len(s) - bad + hi >= 0.85 * len(s)
