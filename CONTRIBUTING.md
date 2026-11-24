# Contributing to QZip

Thanks for helping. QZip's one hard rule: **nothing unverified ships.** Every change keeps every result byte-exact,
and every number in the docs comes from a byte-exact round trip.

## Setup
```bash
git clone https://github.com/EHankinson08/QZip.git && cd QZip
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"      # add ,legacy for the 2025 engines (torch, numba, ...)
make native                   # needs gcc/g++ 13+, make, an x86-64 AVX2 CPU
python -m pytest -q
```
Data-dependent tests skip unless you set `QZIP_DATA_DIR` (folder with the showcase CSVs/logs) and `QZIP_ENWIK8`.

## Changes to codecs or the container
* A change that alters compressed output must keep old files decodable: add a new format/level id instead of
  changing an existing one, and add a round-trip test.
* The default engine lives in `src/engine_mcmfork/` (a fork of mcm, GPL-3.0-or-later). Keep every original mcm
  header; log your change in `src/engine_mcmfork/QZIP_FORK.md`.
* `src/qzrouter.py` must not route any default (`auto`) input to a codec slower than x18 (`qzc2`).

## Benchmarks
Report file, size in bytes, compress/decompress wall time, peak RAM, machine and command line, and confirm the
round trip (`qzip verify file.qz file`). Compare tools on the same machine in the same session.

## Pull requests
Keep them focused, run `make native && python -m pytest -q`, and describe what you measured. CI runs the same on
Ubuntu 24.04.

## License
QZip is GPL-3.0-or-later. By contributing you agree that your contribution is licensed under GPL-3.0-or-later.
