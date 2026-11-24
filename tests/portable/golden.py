#!/usr/bin/env python3
"""Cross-platform stream check: every QZip engine must (1) round-trip byte-exact and (2) write the SAME compressed
bytes on every OS / CPU (Linux x86-64, Linux arm64, macOS x86-64 / arm64, Windows x86-64), so a file made on one
machine decodes on any other. Inputs are generated deterministically here (no downloads).

  python3 tests/portable/golden.py            check against tests/portable/golden.json (exit 1 on any mismatch)
  python3 tests/portable/golden.py --write    regenerate golden.json (do this on the reference Linux x86-64 build)
  python3 tests/portable/golden.py --json out.json   also write per-engine size / seconds for the CI summary
"""
import hashlib, json, os, platform, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Overrides for emulated cross builds (e.g. QZ_RUNNER=qemu-aarch64 or QZ_RUNNER=wine, QZ_EXE=.exe, QZ_SKIP=qzs):
X = os.environ.get("QZ_EXE", ".exe" if os.name == "nt" else "")
N = os.environ.get("QZ_NATIVE_DIR", os.path.join(ROOT, "src", "native"))
C = os.environ.get("QZ_CORE_DIR", os.path.join(ROOT, "src", "qzcore"))
RUNNER = os.environ.get("QZ_RUNNER", "").split()
SKIP = set(filter(None, os.environ.get("QZ_SKIP", "").split(",")))
GOLD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden.json")

def lcg(seed):
    s = seed & 0xFFFFFFFF
    while True:
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        yield s >> 8

def make_inputs():
    r = lcg(1)
    words = ["the", "of", "and", "compression", "qzip", "data", "model", "table", "value", "context", "mixing", "fast",
             "balanced", "strong", "file", "byte", "stream", "column", "row", "text", "zstd", "brotli", "xz", "archive"]
    states = ["AL", "AK", "AZ", "CA", "CO", "NY", "TX", "WA"]
    rows = ["id,state,year,count,rate,label,note"]
    for i in range(60000):
        a, b, c = next(r), next(r), next(r)
        rows.append(f"{i + 1},{states[a % 8]},{2000 + (i // 3000)},{a % 500},{(b % 10000) / 100:.2f},"
                    f"\"{words[b % 24]}, {words[c % 24]}\",{'' if c % 7 == 0 else words[c % 24]}")
    csv = ("\n".join(rows) + "\n").encode()
    txt = " ".join(words[next(r) % 24] + ("." if next(r) % 13 == 0 else "") for _ in range(300000)).encode()
    binb = bytes((next(r) % 7) * 31 % 256 if i % 4 else (i >> 6) & 255 for i in range(600000))
    return {"table.csv": csv, "words.txt": txt, "struct.bin": binb}

ENGINES = {   # name: (compress argv, decompress argv); {i} {o} {r} are file names inside the work dir
    "qze-fast":     ([os.path.join(N, "qze" + X), "c", "-1", "-T", "2", "-b", "1", "{i}", "{o}"], [os.path.join(N, "qze" + X), "d", "{o}", "{r}"]),
    "qze-balanced": ([os.path.join(N, "qze" + X), "c", "-2", "-T", "2", "-b", "2", "{i}", "{o}"], [os.path.join(N, "qze" + X), "d", "{o}", "{r}"]),
    "qzmf-q6":      ([os.path.join(N, "qzmf" + X), "-q6", "{i}", "{o}"], None),   # decodes to the stored name
    "x18-qzc2":     ([os.path.join(C, "qzc2" + X), "c", "{i}", "{o}", "-m", "512"], [os.path.join(C, "qzc2" + X), "d", "{o}", "{r}"]),
    "qzc":          ([os.path.join(C, "qzc" + X), "c", "{i}", "{o}"], [os.path.join(C, "qzc" + X), "d", "{o}", "{r}"]),
    "qzb":          ([os.path.join(C, "qzb" + X), "c", "{i}", "{o}", "1"], [os.path.join(C, "qzb" + X), "d", "{o}", "{r}"]),
    "qzs":          ([sys.executable, os.path.join(C, "qzcsv.py"), "c", "{i}", "{o}"], [sys.executable, os.path.join(C, "qzcsv.py"), "d", "{o}", "{r}"]),
}

def run(argv, cwd):
    if RUNNER and argv[0] != sys.executable:
        argv = RUNNER + argv
    t = time.perf_counter()
    p = subprocess.run(argv, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(f"{os.path.basename(argv[0])} exit {p.returncode}: {p.stderr.decode(errors='replace')[-300:]}")
    return time.perf_counter() - t

def main():
    write = "--write" in sys.argv
    jout = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
    gold = {} if write or not os.path.exists(GOLD) else json.load(open(GOLD))
    inputs, res, bad = make_inputs(), {}, 0
    for fname, data in inputs.items():
        for eng, (c, d) in ENGINES.items():
            if eng in SKIP or (eng == "qzs" and not fname.endswith(".csv")):
                continue
            key = f"{eng}:{fname}"
            with tempfile.TemporaryDirectory(prefix="qzgold_") as w:
                open(os.path.join(w, "in"), "wb").write(data)
                sub = lambda a: [x.replace("{i}", "in").replace("{o}", "out.bin").replace("{r}", "back") for x in a]
                try:
                    tc = run(sub(c), w)
                    blob = open(os.path.join(w, "out.bin"), "rb").read()
                    if d is None:          # qzmf: extract into a subdir, file comes back as "in"
                        os.mkdir(os.path.join(w, "x")); td = run([c[0], "d", "../out.bin", "back"], os.path.join(w, "x"))
                        back = open(os.path.join(w, "x", "in"), "rb").read()
                    else:
                        td = run(sub(d), w); back = open(os.path.join(w, "back"), "rb").read()
                except Exception as e:
                    print(f"FAIL {key}: {e}"); bad += 1; res[key] = {"error": str(e)[:200]}; continue
                ok = back == data
                if eng.startswith("qzmf"):
                    # mcm archives store the input's file attributes (POSIX mode vs Windows attribute bits, a varint of
                    # 4-5 bytes at offset ~32) ahead of the compressed data: compare the stream after them, i.e. the
                    # last (reference size - 64) bytes.
                    ref = str(gold.get(key, ""))
                    tail = int(ref.split(":")[0]) if ":" in ref else len(blob) - 64
                    h = f"{tail}:" + hashlib.sha256(blob[-tail:]).hexdigest()
                else:
                    h = hashlib.sha256(blob).hexdigest()
                same = write or gold.get(key) == h
                res[key] = {"size": len(blob), "sha256": h, "roundtrip": ok, "identical_to_reference": same,
                            "comp_s": round(tc, 3), "decomp_s": round(td, 3)}
                if not ok or not same:
                    bad += 1
                print(f"{'OK  ' if ok and same else 'FAIL'} {key}: {len(data):,} -> {len(blob):,} B  roundtrip={'ok' if ok else 'FAIL'}"
                      f"  stream={'identical' if same else 'DIFFERS from reference ' + str(gold.get(key))[:12]}  {tc:.2f}s/{td:.2f}s")
    if write:
        json.dump({k: v["sha256"] for k, v in res.items() if "sha256" in v}, open(GOLD, "w"), indent=1, sort_keys=True)
        print(f"wrote {GOLD}")
    if jout:
        json.dump({"platform": f"{platform.system()}-{platform.machine()}", "python": platform.python_version(),
                   "results": res, "failures": bad}, open(jout, "w"), indent=1)
    print(f"{platform.system()}-{platform.machine()}: {bad} failure(s)")
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
