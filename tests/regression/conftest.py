import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("QZIP_NO_RAY", "1")
os.environ.setdefault("QZIP_LOG_FILE", "")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import importlib.util
import pytest

# Tests of the 2025 engines (modes fast/max/ultra/legacy/hybrid) need the optional `[legacy]` extra (torch, numba, ...).
HAS_LEGACY = all(importlib.util.find_spec(m) is not None for m in ("torch", "numba", "scipy", "psutil"))


def pytest_configure(config):
    config.addinivalue_line("markers", "legacy: needs the optional 2025 engines (pip install -e .[legacy])")


def pytest_collection_modifyitems(config, items):
    if HAS_LEGACY:
        return
    skip = pytest.mark.skip(reason="2025 engines not installed (pip install -e .[legacy])")
    for item in items:
        if "legacy" in item.keywords:
            item.add_marker(skip)
