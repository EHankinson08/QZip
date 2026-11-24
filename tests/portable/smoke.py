#!/usr/bin/env python3
"""Installed-QZip smoke test (wheels / CI): every tier round-trips a generated CSV and text through the Python API
and the `qzip` CLI, sha256-checked."""
import hashlib, os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from golden import make_inputs

def main():
    from qzip.core import compress_bytes, dump_container, load_container, decompress_container
    bad = 0
    ins = make_inputs()
    for name in ("table.csv", "words.txt"):
        data = ins[name][:400000]
        for mode in ("quick", "classic", "deep", "tier-fast", "tier-balanced", "smart", "auto"):
            c, info = compress_bytes(data, mode=mode)
            blob = dump_container(c)
            out = decompress_container(load_container(blob))
            ok = hashlib.sha256(out).digest() == hashlib.sha256(data).digest()
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'} api {mode:14s} {name}: {len(data):,} -> {len(blob):,} B via {info.get('route_choice') or info.get('chosen')}")
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "t.csv"); open(f, "wb").write(ins["table.csv"][:300000])
        for flag in ("--quick", "--classic", "--deep", "--fast", "--balanced", "--strong"):
            subprocess.check_call([sys.executable, "-m", "qzip", "compress", f, "-o", f + ".qz", "-f", flag])
            subprocess.check_call([sys.executable, "-m", "qzip", "decompress", f + ".qz", "-o", f + ".out", "-f"])
            ok = open(f, "rb").read() == open(f + ".out", "rb").read()
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'} cli {flag}")
    print(f"{bad} failure(s)")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
