"""Thin library API used by the CLI and tests: compress_bytes / decompress_bytes / read/write .qz containers (and older .qc/.qc.gz)."""
import gzip, json, logging
import qzip  # noqa: F401  (sets env defaults before the engine is imported)

_ENGINE = {}

def engine():
    if 'e' not in _ENGINE:
        logging.disable(logging.CRITICAL)
        from src.elliott_quantum_compression_enterprise import ElliottQuantumCompressionEnterprise, CompressionConfig
        _ENGINE['e'] = ElliottQuantumCompressionEnterprise(CompressionConfig())
    return _ENGINE['e']

class _LazyEngine:
    """Builds the enterprise engine on first use: the auto router (qzcore codecs) and stored/xz/zstd containers
    never touch it, so they skip its ~10 s import."""
    def __getattr__(self, name):
        return getattr(engine(), name)

def compress_bytes(data, mode=None):
    """Return (container_dict, info). The container is guaranteed to decode byte-exact (verified here)."""
    from src.safe_compress import safe_compress
    return safe_compress(_LazyEngine(), data, mode=mode)

def decompress_container(container):
    from src.safe_compress import safe_decompress
    return safe_decompress(_LazyEngine(), container)

def dump_container(container, gz=None):
    """Container dict -> `.qz` bytes (format qzip-2: small JSON header + raw payload; see qzip/container.py).
    `gz` is ignored (kept for API compatibility: the payload is already compressed, base64+gzip only added ~1%)."""
    from qzip.container import pack
    return pack(container)

def load_container(raw):
    """`.qz` bytes, or an older `.qc.gz` / `.qc` JSON container -> container dict."""
    from qzip.container import unpack
    return unpack(raw)
