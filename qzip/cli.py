"""QZip — Quick Zip command line.

  qzip compress <file> [-o out.qz] [--quick] [--classic] [--deep] [--fast] [--max]
                [--mode quick|classic|deep|auto|auto-max|auto-fast|fast|max|ultra|legacy|hybrid|text]
                [--codec NAME]
        --quick (default)  Quick: everyday qze -2. Beats xz -9 / 7z -9 / brotli -11 on a 719 MB mix at ~17 MB/s (8 threads)
        --classic          Classic: the launch method (mcm fork router / x18 exceptions)
        --deep             Deep: max tier (qze -3 for now; a stronger engine is in development)
        auto               alias of --classic
        --max              = --mode auto-max: smallest output from QZip's own codecs (x18 = qzc2 CM, qzs CSV/log
                        transform, qzm NIfTI); about 3x slower than classic on text, often slower still on CSV
        auto-fast       same analysis, among the fast codecs (qzb, zstd, stored)
        --codec NAME    override the prediction (qzmf, qzc2, qzc2_lean, qzs, qzm, qzb, qzb_w0, zstd, xz, stored)
        --ram-mb MB     RAM budget (default 3/4 of RAM): qzmf drops its level (11: ~6 GB, 10: ~3 GB), qzc2 -> lean
        --no-verify     skip the verifier (uncertain picks, e.g. qzm vs qzc2, are checked on a small sample)
        fast            routed by file type to the measured-best method (text: QWRT+libbsc; CSV: qtyp; ...)
        max / ultra     same router; text and logs use the CM text mode (4 x stock mcm -h11 / 2 x mcm -x11)
        legacy          original Oct-2025 routes only ("QZip today")
        --fast          everyday fast tier (qze -1): CSV column transform + zstd, parallel blocks; zstd-class speed
        --balanced      everyday balanced tier (qze -2): CSV transform + BWT / zstd -19; xz-class size or better, much faster
        --strong        = the default engine (mcm fork + QZip table/text models)
        --smart [--goal everyday|open-often|archive] [--time-budget S]   picks the tier from size / type / goal
  qzip decompress <file.qz|file.qc.gz|file.qc> [-o out]
  qzip verify <file.qz> [original]
  qzip info <file.qz>                     show header: method, size, sha256, original name, source path
  qzip bench <file> [--mode ...]          QZip vs xz / zstd / brotli / bzip2 (all byte-exact checked)
  qzip web [--host 127.0.0.1] [--port 5000]   drag-and-drop web UI + local API (no API key)
"""
import argparse, bz2, hashlib, lzma, os, sys, time

from src.safe_compress import MODES as _SAFE_MODES, DEFAULT_MODE
from src.qzrouter import CODECS
MODES = list(_SAFE_MODES)

def _mode(a):
    if getattr(a, "ram_mb", None):
        os.environ["QZIP_RAM_MB"] = str(a.ram_mb)
    if getattr(a, "no_verify", False):
        os.environ["QZIP_VERIFY"] = "0"
    if getattr(a, "codec", None):
        return f"codec:{a.codec}"
    if getattr(a, "goal", None):
        os.environ["QZIP_GOAL"] = a.goal
    if getattr(a, "time_budget", None):
        os.environ["QZIP_TIME_BUDGET"] = str(a.time_budget)
    if getattr(a, "threads", None):
        os.environ["QZIP_TIER_THREADS"] = str(a.threads)
    tiers = [t for t in ("quick", "classic", "deep", "fast", "balanced", "strong", "smart") if getattr(a, t, False)]
    if len(tiers) > 1 or (tiers and (getattr(a, "max", False) or a.mode != DEFAULT_MODE)):
        sys.exit("qzip: pick one of --quick / --classic / --deep / --fast / --balanced / --strong / --smart / --max / --mode")
    if tiers:
        return {"quick": "quick", "classic": "classic", "deep": "deep",
                "fast": "tier-fast", "balanced": "tier-balanced", "strong": "auto", "smart": "smart"}[tiers[0]]
    if getattr(a, "max", False):
        if a.mode not in (DEFAULT_MODE, "auto-max"):
            sys.exit("qzip: --max is --mode auto-max; do not combine it with another --mode")
        return "auto-max"
    return a.mode

def _out_name_decompress(path):
    from qzip.container import strip_ext
    return strip_ext(path) or path + ".out"

def _write(path, data, force):
    if os.path.exists(path) and not force:
        sys.exit(f"qzip: {path} exists (use -f to overwrite)")
    with open(path, "wb") as f:
        f.write(data)

def _tier_args(p):
    g = p.add_argument_group("modes")
    g.add_argument("--quick", action="store_true", help="Quick (default): everyday qze -2, xz-class or better size at tens of MB/s")
    g.add_argument("--classic", action="store_true", help="Classic: the launch method (mcm-fork router / x18 exceptions)")
    g.add_argument("--deep", action="store_true", help="Deep: max tier (qze -3 for now; a stronger engine is in development)")
    g.add_argument("--fast", action="store_true", help="fast tier: zstd-class speed, CSV column transform + zstd")
    g.add_argument("--balanced", action="store_true", help="same engine as --quick (qze -2)")
    g.add_argument("--strong", action="store_true", help="same engine as --classic (mcm fork + QZip models)")
    g.add_argument("--smart", action="store_true", help="pick the tier from file size/type and --goal")
    g.add_argument("--goal", choices=("everyday", "open-often", "archive"), help="for --smart (default everyday)")
    g.add_argument("--time-budget", type=float, help="--smart: seconds allowed for compress + decompress (default 10)")
    g.add_argument("--threads", type=int, help="threads for the tiers (default min(8, idle cores))")

def cmd_compress(a):
    from qzip.core import compress_bytes, dump_container
    from qzip.identity import attach_identity, relative_source_path
    data = open(a.file, "rb").read()
    t = time.time()
    container, info = compress_bytes(data, mode=_mode(a))
    attach_identity(container, source_path=relative_source_path(a.file),
                    original_filename=os.path.basename(a.file))
    blob = dump_container(container)
    out = a.output or a.file + ".qz"
    _write(out, blob, a.force)
    src = container.get("source_path")
    src_note = f" source={src}" if src else ""
    print(f"{a.file}: {len(data):,} -> {len(blob):,} B ({len(data)/max(len(blob),1):.2f}:1) "
          f"type={info.get('detected_type') or '-'} method={info.get('route_choice') or info['chosen']} "
          f"verified=yes{src_note} {time.time()-t:.1f}s -> {out}")
    if a.verbose:
        f = ((container.get("quantum_parameters") or {}).get("qzip") or {}).get("features")
        if f:
            print("  analysis: " + " ".join(f"{k}={v}" for k, v in f.items()))
        for m, r in info["tried"]:
            print(f"  tried {m}: {r}")
    return 0

def cmd_decompress(a):
    from qzip.core import load_container, decompress_container
    c = load_container(open(a.file, "rb").read())
    out_bytes = decompress_container(c)   # raises if sha256 recorded and mismatched
    out = a.output or _out_name_decompress(a.file)
    _write(out, out_bytes, a.force)
    from qzip.identity import identity_from_container
    ident = identity_from_container(c)
    sha_note = "verified" if c.get("orig_sha256") or (c.get("quantum_parameters") or {}).get("qzip") else "not recorded (old file)"
    extra = []
    if ident.get("original_filename"):
        extra.append(f"name={ident['original_filename']}")
    if ident.get("source_path"):
        extra.append(f"source={ident['source_path']}")
    if ident.get("orig_sha256"):
        extra.append(f"sha256={ident['orig_sha256']}")
    extra_s = (" " + " ".join(extra)) if extra else ""
    print(f"{a.file} -> {out} ({len(out_bytes):,} B, sha256 {sha_note}{extra_s})")
    return 0

def cmd_verify(a):
    from qzip.core import load_container, decompress_container
    c = load_container(open(a.file, "rb").read())
    try:
        out = decompress_container(c)
    except Exception as e:
        print(f"FAIL {a.file}: {e}"); return 1
    ok = True
    if a.original:
        ok = hashlib.sha256(out).digest() == hashlib.sha256(open(a.original, "rb").read()).digest()
    from qzip.identity import identity_from_container
    ident = identity_from_container(c)
    extra = []
    if ident.get("original_filename"):
        extra.append(f"name={ident['original_filename']}")
    if ident.get("source_path"):
        extra.append(f"source={ident['source_path']}")
    if ident.get("orig_sha256"):
        extra.append(f"sha256={ident['orig_sha256']}")
    extra_s = (" " + " ".join(extra)) if extra else ""
    print(f"{'OK' if ok else 'FAIL'} {a.file}: decodes to {len(out):,} B"
          + (f", {'matches' if ok else 'DIFFERS FROM'} {a.original}" if a.original else "")
          + extra_s)
    return 0 if ok else 1

def _baselines(data):
    res = []
    def run(name, c, d):
        t = time.time()
        try:
            z = c(data)
        except Exception as e:
            res.append((name, None, f"error {type(e).__name__}", 0)); return
        dt = time.time() - t
        ok = d(z) == data
        res.append((name, len(z), "ok" if ok else "ROUNDTRIP FAIL", dt))
    run("xz -9", lambda b: lzma.compress(b, preset=9), lzma.decompress)
    try:
        import zstandard as zs
        p = zs.ZstdCompressionParameters.from_level(19, window_log=min(27, max(10, (len(data) - 1).bit_length())), enable_ldm=True)
        run("zstd -19 --long", lambda b: zs.ZstdCompressor(compression_params=p).compress(b),
            lambda z: zs.ZstdDecompressor().decompress(z, max_output_size=len(data) + 1))
    except ImportError:
        res.append(("zstd -19 --long", None, "pip install zstandard", 0))
    try:
        import brotli
        run("brotli -11", lambda b: brotli.compress(b, quality=11, lgwin=24), brotli.decompress)
    except ImportError:
        res.append(("brotli -11", None, "pip install brotli", 0))
    run("bzip2 -9", lambda b: bz2.compress(b, 9), bz2.decompress)
    return res

def cmd_info(a):
    from qzip.core import load_container
    from qzip.identity import identity_from_container
    raw = open(a.file, "rb").read()
    c = load_container(raw)
    ident = identity_from_container(c)
    qp = (c.get("quantum_parameters") or {}).get("qzip") or {}
    rows = [
        ("file", a.file),
        ("format", c.get("format") or qp.get("format") or "unknown"),
        ("size", f"{len(raw):,} B"),
        ("method", c.get("method") or "-"),
        ("mode", qp.get("mode") or "-"),
        ("codec", qp.get("chosen") or "-"),
        ("orig_size", ident.get("orig_size") if ident.get("orig_size") is not None else c.get("orig_size") or "-"),
        ("orig_sha256", ident.get("orig_sha256") or "not recorded"),
        ("original_filename", ident.get("original_filename") or "not recorded"),
        ("source_path", ident.get("source_path") or "not recorded"),
        ("verified", c.get("verified") if "verified" in c else qp.get("verified", "-")),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print(f"{k:<{width}}  {v}")
    return 0

def cmd_bench(a):
    from qzip.core import compress_bytes, dump_container, load_container, decompress_container
    data = open(a.file, "rb").read()
    print(f"{a.file}: {len(data):,} B")
    t = time.time()
    container, info = compress_bytes(data, mode=_mode(a))
    blob = dump_container(container)
    ct = time.time() - t
    ok = decompress_container(load_container(blob)) == data
    rows = [(f"qzip --mode {_mode(a)} [{info['chosen']}]", len(blob), "ok" if ok else "ROUNDTRIP FAIL", ct)]
    rows += _baselines(data)
    print(f"{'codec':42} {'bytes':>14} {'ratio':>9} {'secs':>8}  check")
    for name, n, st, dt in rows:
        print(f"{name:42} {n if n is not None else '-':>14,} {(f'{len(data)/n:.2f}:1' if n else '-'):>9} {dt:8.1f}  {st}"
              if n is not None else f"{name:42} {'-':>14} {'-':>9} {'-':>8}  {st}")
    return 0

def cmd_native(a):
    import subprocess
    from src.native import qz_native as N
    here = os.path.dirname(os.path.abspath(N.__file__))
    r = subprocess.run(["make", "-C", os.path.dirname(os.path.dirname(here)), "native"])
    if r.returncode:
        return r.returncode
    print(f"libqznative={'ok' if N.lib_available() else 'MISSING'} "
          f"qzcm(max/ultra)={'ok' if N.cm_available() else 'not available'}")
    return 0

def cmd_web(a):
    from qzip.web import main as web_main
    return web_main(host=a.host, port=a.port, api_port=a.api_port)

def main(argv=None):
    ap = argparse.ArgumentParser(prog="qzip", description="QZip — Quick Zip (verified compression, GPL-3.0-or-later)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("compress"); p.add_argument("file"); p.add_argument("-o", "--output")
    p.add_argument("--mode", choices=MODES, default=DEFAULT_MODE); p.add_argument("-f", "--force", action="store_true")
    p.add_argument("--max", action="store_true", help="= --mode auto-max: x18 / qzs, smallest, slowest")
    _tier_args(p)
    p.add_argument("--codec", choices=CODECS, help="override the predicted codec"); p.add_argument("--ram-mb", type=int)
    p.add_argument("--no-verify", action="store_true", help="skip the sample check of uncertain picks (src/qzverify.py)")
    p.add_argument("-v", "--verbose", action="store_true"); p.set_defaults(fn=cmd_compress)
    p = sub.add_parser("decompress"); p.add_argument("file"); p.add_argument("-o", "--output")
    p.add_argument("-f", "--force", action="store_true"); p.set_defaults(fn=cmd_decompress)
    p = sub.add_parser("verify"); p.add_argument("file"); p.add_argument("original", nargs="?"); p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("info", help="show .qz header fields (name, source path, sha256)"); p.add_argument("file"); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("bench"); p.add_argument("file"); p.add_argument("--mode", choices=MODES, default=DEFAULT_MODE)
    p.add_argument("--max", action="store_true", help="= --mode auto-max")
    _tier_args(p)
    p.add_argument("--codec", choices=CODECS); p.add_argument("--ram-mb", type=int); p.add_argument("--no-verify", action="store_true")
    p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("web"); p.add_argument("--host", default=os.getenv("QZIP_WEB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("QZIP_WEB_PORT", "5000")))
    p.add_argument("--api-port", type=int, default=int(os.getenv("QZIP_API_PORT", "8000"))); p.set_defaults(fn=cmd_web)
    p = sub.add_parser("native", help="build/check the native text codecs (make native)")
    p.set_defaults(fn=cmd_native)
    a = ap.parse_args(argv)
    return a.fn(a)

if __name__ == "__main__":
    raise SystemExit(main())
