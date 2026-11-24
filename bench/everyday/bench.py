#!/usr/bin/env python3
"""QZip everyday benchmark: QZip tiers vs zstd / xz / brotli / lz4, byte-exact checked.

  python3 bench/everyday/bench.py [--codecs a,b,...] [--threads 8] [--out results.jsonl] FILE...

Every codec is run as its own process (CLI), so the numbers are what a user sees: wall-clock compress and
decompress time (MB/s = input MB / s, 1 MB = 10^6 B), peak RSS of each process (wait4 ru_maxrss), compressed
size, and a sha256 check of the round trip. Files are read once before timing so the page cache is warm.
Needs: zstd, xz, brotli, lz4 on PATH (apt/brew install zstd xz-utils brotli lz4) and `make native` (QZip
binaries); missing tools are skipped. Results append as JSON lines; bench/everyday/table.py renders them.
"""
import argparse, hashlib, json, os, platform, shlex, shutil, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
QZE = os.environ.get("QZE_BIN", os.path.join(ROOT, "src", "native", "everyday", "qze"))
QZMF = os.environ.get("QZMF_BIN", os.path.join(ROOT, "src", "native", "qzmf"))
PY = sys.executable

def codecs(T):
    q = shlex.quote
    return {
        # QZip tiers
        "qzip-fast":      ([QZE, "c", "-1", "-T", str(T), "{i}", "{o}"], [QZE, "d", "-T", str(T), "{o}", "{r}"]),
        "qzip-balanced":  ([QZE, "c", "-2", "-T", str(T), "{i}", "{o}"], [QZE, "d", "-T", str(T), "{o}", "{r}"]),
        "qzip-strong":    ([QZMF, "-q11", "{i}", "{o}"], [QZMF, "d", "{o}", "{r}"]),
        "qzip-strong-l10": ([QZMF, "-q10", "{i}", "{o}"], [QZMF, "d", "{o}", "{r}"]),
        "qzip-strong-par": ([PY, os.path.join(ROOT, "src", "qzpar.py"), "c", "-T", str(T), "{i}", "{o}"],
                            [PY, os.path.join(ROOT, "src", "qzpar.py"), "d", "-T", str(T), "{o}", "{r}"]),
        "qzip-lowmem":    ([PY, os.path.join(ROOT, "src", "qzpar.py"), "c", "-T", "1", "--ram-mb", "512", "{i}", "{o}"],
                           [PY, os.path.join(ROOT, "src", "qzpar.py"), "d", "-T", "1", "{o}", "{r}"]),
        # references (single-thread unless -T)
        "zstd-3":         (["zstd", "-q", "-f", "-3", "-T1", "{i}", "-o", "{o}"], ["zstd", "-q", "-f", "-d", "{o}", "-o", "{r}"]),
        "zstd-3-T":       (["zstd", "-q", "-f", "-3", f"-T{T}", "{i}", "-o", "{o}"], ["zstd", "-q", "-f", "-d", "{o}", "-o", "{r}"]),
        "zstd-19":        (["zstd", "-q", "-f", "-19", "-T1", "{i}", "-o", "{o}"], ["zstd", "-q", "-f", "-d", "{o}", "-o", "{r}"]),
        "zstd-19-T":      (["zstd", "-q", "-f", "-19", f"-T{T}", "{i}", "-o", "{o}"], ["zstd", "-q", "-f", "-d", "{o}", "-o", "{r}"]),
        "zstd-19-long":   (["zstd", "-q", "-f", "-19", "--long=27", f"-T{T}", "{i}", "-o", "{o}"], ["zstd", "-q", "-f", "-d", "--long=27", "{o}", "-o", "{r}"]),
        "xz-9":           (["sh", "-c", "xz -9 -T1 -c {i} > {o}"], ["sh", "-c", "xz -d -c {o} > {r}"]),
        "xz-9-T":         (["sh", "-c", f"xz -9 -T{T} -c {{i}} > {{o}}"], ["sh", "-c", f"xz -d -T{T} -c {{o}} > {{r}}"]),
        "brotli-11":      (["brotli", "-f", "-q", "11", "-w", "24", "{i}", "-o", "{o}"], ["brotli", "-f", "-d", "{o}", "-o", "{r}"]),
        "brotli-5":       (["brotli", "-f", "-q", "5", "{i}", "-o", "{o}"], ["brotli", "-f", "-d", "{o}", "-o", "{r}"]),
        "lz4":            (["lz4", "-q", "-f", "{i}", "{o}"], ["lz4", "-q", "-f", "-d", "{o}", "{r}"]),
    }

def _tree_rss_mb(pid):
    """sum of RSS over pid and all its descendants (parallel workers count together)."""
    try:
        import psutil
        p = psutil.Process(pid)
        return sum(x.memory_info().rss for x in [p] + p.children(recursive=True)) / 2**20
    except ImportError:
        pass
    except Exception:
        return 0.0
    kids, tot = {}, 0
    try:
        for d in os.listdir("/proc"):
            if d.isdigit():
                try:
                    st = open(f"/proc/{d}/stat").read().rsplit(")", 1)[1].split()
                    kids.setdefault(int(st[1]), []).append(int(d))
                except OSError:
                    pass
        todo = [pid]
        while todo:
            q = todo.pop()
            try:
                tot += int(open(f"/proc/{q}/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
            except OSError:
                pass
            todo += kids.get(q, [])
    except OSError:
        return 0.0
    return tot / 2**20

def run(cmd, i, o, r):
    import threading
    cmd = [c.replace("{i}", shlex.quote(i) if cmd[0] == "sh" else i).replace("{o}", o).replace("{r}", r) for c in cmd]
    t = time.perf_counter()
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    peak, done = [0.0], threading.Event()
    def sample():
        while not done.wait(0.05):
            peak[0] = max(peak[0], _tree_rss_mb(p.pid))
    th = threading.Thread(target=sample, daemon=True); th.start()
    err = p.stderr.read().decode(errors="replace")[-300:]
    if hasattr(os, "wait4"):
        _, status, ru = os.wait4(p.pid, 0)
        rc = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
        ru_mb = ru.ru_maxrss / (2**20 if sys.platform == "darwin" else 1024)
    else:                                   # Windows: sampled tree RSS only
        rc, ru_mb = p.wait(), 0.0
    dt = time.perf_counter() - t
    done.set(); th.join()
    p.stderr.close()
    return dt, max(ru_mb, peak[0]), rc, err

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--codecs", default="qzip-fast,qzip-balanced,zstd-3,zstd-3-T,zstd-19-T,xz-9,brotli-11,lz4")
    ap.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--out", default="bench_everyday.jsonl")
    ap.add_argument("--tmp", default=None)
    ap.add_argument("--host", default=platform.node())
    a = ap.parse_args()
    C = codecs(a.threads)
    tmp = tempfile.mkdtemp(prefix="qzbench_", dir=a.tmp)
    try:
        for f in a.files:
            n = os.path.getsize(f)
            ref = sha(f)
            for name in a.codecs.split(","):
                if name not in C:
                    print(f"unknown codec {name}", file=sys.stderr); continue
                c, d = C[name]
                if not shutil.which(c[0]) and not os.path.exists(c[0]):
                    print(f"skip {name}: {c[0]} not found", file=sys.stderr); continue
                o, r = os.path.join(tmp, "c.bin"), os.path.join(tmp, "r.bin")
                for x in (o, r):
                    if os.path.exists(x): os.remove(x)
                ct, cr, ce, cerr = run(c, f, o, r)
                if ce != 0:
                    print(f"{name} {f}: compress failed {cerr}", file=sys.stderr); continue
                size = os.path.getsize(o)
                dtm, dr, de, derr = run(d, f, o, r)
                ok = de == 0 and os.path.exists(r) and sha(r) == ref
                rec = dict(file=os.path.basename(f), bytes=n, codec=name, size=size, ratio=round(n / max(size, 1), 3),
                           comp_s=round(ct, 3), decomp_s=round(dtm, 3), comp_MBps=round(n / 1e6 / ct, 2),
                           decomp_MBps=round(n / 1e6 / dtm, 2), comp_rss_MB=round(cr, 1), decomp_rss_MB=round(dr, 1),
                           roundtrip="ok" if ok else "FAIL", threads=a.threads, host=a.host,
                           platform=f"{platform.system()}-{platform.machine()}", time=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                print(json.dumps(rec), flush=True)
                with open(a.out, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
