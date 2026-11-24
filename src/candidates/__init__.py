"""Drop-in compression codecs for the QZip router (src/qzip_routes.py).

Each module here is imported once by qzip_routes.load_plugins() and either calls
`src.qzip_routes.register_candidate(name, compress, decompress, method=None, available=None)` or defines
`register(registry)` that does `registry[name] = (compress, decompress)`. ROUTES in src/qzip_routes.py decides
which file types / levels use a name. src/safe_compress.py verifies every result by decoding it.
A module that fails to import is recorded in qzip_routes.PLUGIN_STATUS and otherwise ignored.
"""
