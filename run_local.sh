#!/usr/bin/env bash
# One-command local launch of the QZip drag-and-drop web UI (no API key needed).
# First run: creates .venv (small default install, no torch) and builds the native codecs (~1 min with g++).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "[qzip] first run: creating .venv and installing the Python dependencies..."
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -e .
fi
# native codecs: default engine qzmf (mcm fork), x18 qzc2 / qzs / qzb, libbsc helpers
if [ ! -f src/native/libqznative.so ] || [ ! -x src/qzcore/qzc2 ] || [ ! -x src/native/qzmf ]; then
  echo "[qzip] building the native codecs (about 1 minute)..."
  make native >/dev/null 2>&1 || { echo "[qzip] native build failed (need g++/gcc, make, an x86-64 AVX2 CPU): run 'make native' to see why" >&2; exit 1; }
fi
exec .venv/bin/python -m qzip.web "$@"
