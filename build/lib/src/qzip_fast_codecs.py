"""QZip fast-angle candidates for the router (src/qzip_routes.py loads this module and calls register()).
  qtyp : type-aware column transform + best backend per stream (CSV / TSV / structured logs). Ours.
  lpaq : block-parallel lpaq1 context mixing (free-form logs). lpaq1 is GPL-2.0-or-later (used under GPL-3.0 terms).
safe_compress verifies every result by decoding it (sha256) and falls back to xz if verification fails."""
try:
    from src import qzfast as Q
except ImportError:
    import qzfast as Q

def _dispatch(blob):
    if blob[:4] == Q.CMAGIC + b'\x02':
        return Q.qtyp_decompress(blob)
    if blob[:3] == Q.FMAGIC:
        return Q.lpaq_decompress(blob)
    raise ValueError('not a qtyp/lpaq blob')

CODECS = {'qtyp': Q.qtyp_compress, 'lpaq': Q.lpaq_compress}
AVAILABLE = {'qtyp': Q.qtyp_available, 'lpaq': Q.lpaq_available}
decompress = _dispatch

def _avail(name):
    def f():
        if not AVAILABLE[name]():
            try: Q._native()._try_build()      # one-time `make native` (QZIP_AUTOBUILD=0 disables)
            except Exception: pass
        if AVAILABLE[name]():
            return True
        return f'unavailable({name} helper not built: run `make native`)'
    return f

def register(registry):
    """Register qtyp and lpaq with availability checks evaluated per call (build state can change)."""
    try:
        from src.qzip_routes import register_candidate
    except ImportError:
        from qzip_routes import register_candidate
    for name, fn in CODECS.items():
        register_candidate(name, fn, _dispatch, method=name, available=_avail(name))
        if AVAILABLE[name]():
            dict.__setitem__(registry, name, (fn, _dispatch))   # status display only
    return registry
