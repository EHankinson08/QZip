"""QZip predictive router: pick ONE qzcore codec per file from a fast analysis, then encode only that codec.

No try-all. The analysis reads at most ~160 KB of the file (head, middle, tail) and costs milliseconds:
  * magic bytes: NIfTI-1/2 (also .nii.gz), DICOM, compressed / media containers, ELF/PE, safetensors
  * text vs binary (printable/UTF-8 share), letters / digits / punctuation shares, line structure
  * CSV / TSV / log structure (same tests as src/qzip_routes.refine_type)
  * order-0 and order-1 entropy of the sample, plus one tiny zlib trial on <= 3 x 2 KB blocks
The decision rules below were fitted on a measured $0 corpus (bench/router/: ground truth = every codec actually
run, byte-exact checked); see src/qzcore/ROUTER.md for the accuracy tables.

Objectives
  balanced (mode auto, the default)
                         qzmf, the GPL mcm fork (src/engine_mcmfork, mcm -q11: CSV/table + text contexts), for
                         text, CSV, logs, binaries; ~0.2-0.6 MB/s, about 3x faster than qzc2 (x18) on enwik8/enwik9
                         and 1.0-1.2% larger than x18 on enwik8 / 0.16% SMALLER on enwik9 (PC2, bench in
                         src/engine_mcmfork/bench/RESULTS.md). Predicted exceptions: CSVs wider than the fork's
                         256-field table model (e.g. hctsa, ~10,000 columns) go to x18 (qzc2, 17.6% smaller
                         there; never qzs, which is slower than x18); NIfTI / label maps keep qzm / qzb; incompressible / tiny inputs are stored.
  max  (mode auto-max, CLI --max)
                         smallest output: qzc2 (CM; prose gets the sorted word dictionary), qzs (CSV / field
                         logs), qzm (NIfTI), qzb (label maps), stored
  fast (mode auto-fast)  smallest among the fast codecs: qzb (word transform on/off), stored
RAM: qzmf uses ~3 x 2^level MB (level 11: ~6.1 GB RSS on enwik8); the router drops the level until it fits the
budget below (level 10 ~3 GB, 9 ~1.5 GB). qzc2 sizes its tables to the input. If the RAM budget (QZIP_RAM_MB or ram_mb=, default 3/4 of physical RAM)
cannot hold the default tables, the router picks qzc2 lean (-l 1: 16 contexts, ~4x less RAM, ~20% faster, ~1.4%
larger on enwik8) at that budget.
Override: codec=NAME (CLI --codec, web mode "codec:NAME") skips the prediction.
"""
import math, os, re, shutil, subprocess, sys, tempfile, time, zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.environ.get("QZCORE_BIN", os.path.join(HERE, "qzcore"))
MB = 1 << 20
QZMF_BIN = os.environ.get("QZMF_BIN", os.path.join(HERE, "native", "qzmf"))   # GPL mcm fork (src/engine_mcmfork)
_EXE = ".exe" if os.name == "nt" else ""
QZE_BIN = os.environ.get("QZE_BIN", os.path.join(HERE, "native", "qze" + _EXE))   # everyday tiers (src/native/everyday)
CODECS = ("qzmf", "qzc2", "qzc2_text", "qzc2_lean", "qzc2_text_lean", "qzs", "qzm", "qzb", "qzb_w0", "zstd", "xz", "stored",
          "qze_fast", "qze_balanced", "qzpar")
# Everyday tiers (bench/everyday/RESULTS.md):
#   qze_fast      CSV column transform + zstd, block-parallel: zstd-class speed, smaller than zstd -3 on every file tested
#   qze_balanced  CSV column transform + libbsc BWT (text) / zstd -19 (binary), block-parallel: xz-class or better size,
#                 50-100x xz's compression speed
#   qzpar         the strong engine (qzmf) on T chunks in parallel, memory level sized to the RAM budget
TIERS = ("fast", "balanced", "strong")
FAST = ("qzb", "qzb_w0", "stored")   # zstd -19 / xz -9 are 10-100x slower than qzb: override only

_COMPRESSED = (b"\x1f\x8b", b"\xfd7zXZ\x00", b"PK\x03\x04", b"BZh", b"\x28\xb5\x2f\xfd", b"7z\xbc\xaf\x27\x1c",
               b"Rar!", b'\x04"M\x18', b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF8", b"OggS", b"fLaC", b"ID3",
               b"\x1a\x45\xdf\xa3")
_LOG_RE = re.compile(rb"^(\[?\d{4}[-/.]\d{2}[-/.]\d{2}[ T_]\d{1,2}:\d{2}|\[?\d{2}[-/.]\d{2}[-/.]?\d{0,4}[ T_]\d{1,2}:\d{2}|"
                     rb"\[?(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s|\[?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d|"
                     rb"\d{8}[-_ T]\d{2}:?\d{2}:?\d{2}|\d{10}(\.\d+)?\s|\S+\.log[\w.:-]*\s)")

# ------------------------------------------------------------------ analysis
def _chunks(data, k=64 << 10):
    """head 64 KB + middle 32 KB + tail 32 KB (the whole input if it is small)."""
    n = len(data)
    if n <= 3 * k:
        return [bytes(data)]
    m = n // 2
    return [bytes(data[:k]), bytes(data[m:m + k // 2]), bytes(data[n - k // 2:])]

_QUOTED = re.compile(rb'"[^"\n]*"')

def _lines(chunks):
    """complete lines only (a middle / tail chunk starts and ends inside a line)."""
    out = []
    for i, c in enumerate(chunks):
        ls = c.split(b"\n")
        ls = ls[(1 if i else 0):-1] if len(ls) > 2 else ls[(1 if i else 0):]
        out += [l for l in ls if l.strip()]
    return out

def _entropies(s):
    """order-0 and order-1 conditional entropy (bits/byte) of the sample."""
    n = len(s)
    if n < 2:
        return 8.0, 8.0
    try:
        import numpy as np
        a = np.frombuffer(s, dtype=np.uint8)
        c0 = np.bincount(a, minlength=256).astype(np.float64)
        p = c0[c0 > 0] / n
        h0 = float(-(p * np.log2(p)).sum())
        pair = a[:-1].astype(np.int32) * 256 + a[1:]
        c1 = np.bincount(pair, minlength=65536).astype(np.float64).reshape(256, 256)
        tot = c1.sum(1, keepdims=True)
        with np.errstate(divide="ignore", invalid="ignore"):
            q = np.where(c1 > 0, c1 / tot, 1.0)
            h1 = float(-(c1 * np.log2(q)).sum() / (n - 1))
        return h0, h1
    except ImportError:
        c0 = [0] * 256
        for b in s:
            c0[b] += 1
        h0 = -sum(c / n * math.log2(c / n) for c in c0 if c)
        c1 = {}
        for x, y in zip(s, s[1:]):
            c1[(x << 8) | y] = c1.get((x << 8) | y, 0) + 1
        h1 = -sum(c * math.log2(c / c0[k >> 8]) for k, c in c1.items()) / (n - 1)
        return h0, h1

_PRINT = bytes(range(32, 127)) + b"\t\n\r"
_HI = bytes(range(128, 256))
_LET = bytes(range(65, 91)) + bytes(range(97, 123))
_DIG = b"0123456789"

def _cnt(s, chars):
    """number of bytes of s that are in chars (C speed: bytes.translate)."""
    return len(s) - len(s.translate(None, chars))

def _is_nifti(d):
    if len(d) >= 352 and int.from_bytes(d[0:4], "little") in (348, 540) and d[344:347] in (b"n+1", b"ni1"):
        return True
    if len(d) >= 352 and int.from_bytes(d[0:4], "big") == 348 and d[344:347] in (b"n+1", b"ni1"):
        return True
    return len(d) >= 544 and int.from_bytes(d[0:4], "little") == 540 and d[4:8] in (b"n+2\0", b"ni2\0")

def analyze(data):
    """Cheap features of `data` (bytes). Never reads more than ~160 KB."""
    t0 = time.perf_counter()
    n = len(data); head = bytes(data[:600])
    f = {"size": n, "magic": None}
    if _is_nifti(head):
        f["magic"] = "nifti"
        bo = "little" if int.from_bytes(head[0:4], "little") in (348, 540) else "big"
        f["nifti_dtype"] = int.from_bytes(head[70:72] if int.from_bytes(head[0:4], bo) == 348 else head[12:14], bo)
    elif len(head) >= 132 and head[128:132] == b"DICM":
        f["magic"] = "dicom"
    elif head.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        f["magic"] = "image"        # JPEG / PNG: entropy coded, but headers / metadata / filler still shrink
    elif head.startswith(_COMPRESSED) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP") or \
            (head[4:8] == b"ftyp"):
        f["magic"] = "compressed"
    elif head.startswith(b"\x7fELF") or head.startswith(b"MZ"):
        f["magic"] = "executable"
    elif n > 8 and head[8:9] == b"{" and int.from_bytes(head[:8], "little") < min(n, 1 << 28):
        f["magic"] = "safetensors"
    ch = _chunks(data)
    s = b"".join(ch)
    m = max(1, len(s))
    printable = _cnt(s, _PRINT)
    hi = _cnt(s, _HI)
    try:
        s.decode("utf-8"); utf8 = True
    except UnicodeDecodeError as e:
        utf8 = e.start > len(s) - 4     # a sample cut inside a multibyte char still counts
    f["text_frac"] = (printable + (hi if utf8 else 0)) / m
    f["is_text"] = f["text_frac"] > 0.97 and s.count(b"\0") == 0
    f["letters"] = _cnt(s, _LET) / m
    f["digits"] = _cnt(s, _DIG) / m
    f["spaces"] = s.count(b" ") / m
    f["h0"], f["h1"] = _entropies(s)
    blk = [s[i:i + 2048] for i in (0, len(s) // 2, max(0, len(s) - 2048))] if len(s) > 6144 else [s]
    raw = sum(len(b) for b in blk)
    f["trial"] = sum(len(zlib.compress(b, 6)) for b in blk) / max(1, raw)   # zlib on <= 6 KB
    lines = _lines(ch)
    f["kind"] = _kind(f, lines, ch)
    f["fields"] = _fields(lines, ch) if f["kind"] == "csv" else 0
    f["analysis_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return f

def _kind(f, all_lines, chunks):
    if f["magic"] in ("nifti", "dicom"):
        return "medical"
    if f["magic"] == "image":
        return "image"
    if f["magic"] == "compressed" or (f["h0"] > 7.9 and f["trial"] > 0.97):
        return "incompressible"
    if not f["is_text"]:
        return "binary"
    lines = all_lines[:3000]
    if len(lines) >= 20:
        logish = sum(1 for l in lines if _LOG_RE.match(l.lstrip()))
        if logish >= 0.6 * len(lines):
            return "log"
    if len(lines) >= 4:     # wide tables have few lines per sample
        body = [_QUOTED.sub(b"Q", l) for l in lines]     # commas inside "quoted fields" do not count
        for d in (b",", b"\t", b"|", b";"):
            c = [l.count(d) for l in body]
            c0 = max(set(c), key=c.count)
            # >= 2 delimiters per row: JSON / prose lines ending in a single comma are not tables
            if c0 >= (1 if d == b"\t" else 2) and sum(1 for x in c if x == c0) >= 0.85 * len(c):
                return "csv"
    if len(lines) < 20 and len(chunks[0]) >= 4096:   # rows longer than the sample chunks (very wide tables)
        for d in (b",", b"\t", b";"):
            dens = [c.count(d) / max(1, len(c)) for c in chunks]
            if min(dens) >= 0.04 and min(dens) >= 0.7 * max(dens):
                return "csv"
    return "text"

def _fields(lines, chunks):
    """Estimated fields per row of a CSV (modal delimiter count + 1). Rows longer than the sample chunks (fewer than
    4 complete lines) are estimated from the delimiter density x the head chunk length, i.e. at least that many."""
    best = 0
    if len(lines) >= 4:
        body = [_QUOTED.sub(b"Q", l) for l in lines[:3000]]
        for d in (b",", b"\t", b"|", b";"):
            c = [l.count(d) for l in body]
            best = max(best, max(set(c), key=c.count) + 1)
    if len(lines) < 20:
        for d in (b",", b"\t", b";"):
            best = max(best, max(c.count(d) for c in chunks) + 1)
    return best

# ------------------------------------------------------------------ prediction
QZMF_FIELDS_MAX = 256   # QzCsvTracker::kMaxFields: wider rows saturate the field index, the table contexts stop helping

def qzmf_available():
    """The fork binary is built by `make native` (src/native/qzmf; QZMF_BIN overrides)."""
    return os.path.exists(QZMF_BIN)

def qzmf_level(ram_mb=None):
    """Highest mcm-fork level (<= 11, the benchmarked one) whose ~3 x 2^level MB hash fits the RAM budget."""
    budget = ram_mb or int(os.environ.get("QZIP_RAM_MB", "0")) or phys_ram_mb() * 3 // 4
    lvl = int(os.environ.get("QZIP_QZMF_LEVEL", "11"))
    while lvl > 4 and 3 * (1 << lvl) + 256 > budget:
        lvl -= 1
    return lvl

def phys_ram_mb():
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // MB
    except (ValueError, OSError, AttributeError):
        return 8192

def qzc2_ram_mb(n, cap_mb=12288, prose=False):
    """RAM qzc2 -l 2 uses for an n-byte input with the router's default -m cap (12288 MB): 16 B x 2^hbits hash
    table (hbits = ceil(log2 n) + 5, capped by the budget) + fixed tables (as in qzc2.cpp auto_sizes; prose also
    allocates the 4 B x 2^tbits bigram-successor table and the 72 MB direct low-order tables)."""
    L = max(1, (max(n, 2) - 1).bit_length()); mb = tb = max(18, min(26, L + 1))
    fixed = n + 8 + (8 << mb) + ((14 if prose else 10) << tb) + 3 * 65536 * 33 * 2 + (10 << 20) + \
        ((1024 + 4 * 65536) * 17 * 16 if prose else 0)
    hb = max(16, min(32, L + 5))
    while hb > 16 and (16 << hb) + fixed > cap_mb * MB:
        hb -= 1
    return ((16 << hb) + fixed) // MB

# measured MB/s per tier on the box (8 threads; bench/everyday/RESULTS.md), (compress, decompress), conservative
TIER_SPEED = {"fast": {"csv": (200, 400), "text": (250, 400), "binary": (250, 500)},
              "balanced": {"csv": (40, 80), "text": (60, 80), "binary": (8, 300)},
              "strong": {"csv": (2.5, 2.5), "text": (1.5, 1.5), "binary": (1.5, 1.5)}}
GOALS = ("everyday", "open-often", "archive")

def smart_tier(f, goal=None, budget_s=None, ram_mb=None):
    """Pick a tier from the file analysis and a user goal (CLI --goal, env QZIP_GOAL):
      open-often  files read or sent often: the fast tier (decompression >= 400 MB/s), whatever the size
      archive     smallest output: strong (the default engine) unless its estimated time is above 10x the budget
      everyday    (default) the smallest tier whose estimated compress + decompress time fits the budget
                  (QZIP_TIME_BUDGET seconds, default 10): small files get strong, mid-size balanced, huge fast.
    Returns (tier, reason)."""
    goal = goal or os.environ.get("QZIP_GOAL", "everyday")
    budget = float(budget_s or os.environ.get("QZIP_TIME_BUDGET", "10"))
    k, mb = f["kind"], f["size"] / 1e6
    cls = "csv" if k in ("csv", "log") else ("text" if k == "text" else "binary")
    est = {t: mb / TIER_SPEED[t][cls][0] + mb / TIER_SPEED[t][cls][1] for t in TIERS}
    if goal == "open-often":
        return "fast", f"goal open-often: fast tier (est. {est['fast']:.2f} s round trip)"
    lim = budget * (10 if goal == "archive" else 1)
    for t in ("strong", "balanced", "fast"):
        if est[t] <= lim:
            return t, f"goal {goal}: {t} tier fits {lim:g} s (est. {est[t]:.1f} s for {mb:.1f} MB {cls})"
    return "fast", f"goal {goal}: nothing fits {lim:g} s, fast tier (est. {est['fast']:.1f} s)"

def predict(f, objective="max", ram_mb=None):
    """Return (codec, reason). objective: 'balanced' (default = strong tier), 'max' (smallest), 'fast',
    'tier-fast' / 'tier-balanced' (everyday tiers) or 'smart' (picks a tier, see smart_tier)."""
    k, n = f["kind"], f["size"]
    if n < 64:
        return "stored", "tiny input"
    if k == "incompressible":
        return "stored", f"already compressed ({f['magic'] or 'entropy %.2f b/B, zlib trial %.3f' % (f['h0'], f['trial'])})"
    if objective == "tier-fast":
        return "qze_fast", f"{k}: fast tier (CSV column transform + zstd, parallel blocks)"
    if objective == "tier-balanced":
        return "qze_balanced", f"{k}: balanced tier (CSV column transform + BWT / zstd -19, parallel blocks)"
    if objective == "smart":
        tier, why = smart_tier(f, ram_mb=ram_mb)
        if tier == "strong":
            codec, why2 = predict(f, "balanced", ram_mb)
            return codec, f"{why} -> {why2}"
        return predict(f, "tier-" + tier, ram_mb)[0], why
    if objective == "fast":
        if k in ("medical", "binary", "image"):
            return "qzb_w0", f"{k}: BWT without word transform"
        # the word transform pays for its dictionary only on large inputs. Measured on 26 text/CSV/log files with
        # letters: below 16 MB it lost on 13 of 16 (by 0.03-19%); at >= 16 MB it won on 8 of 10 (by 0.08-2.6%)
        if f["letters"] > 0.1 and n >= 16 * MB:
            return "qzb", f"{k}, letters {f['letters']:.2f}, {n / MB:.1f} MB: word transform + BWT"
        return "qzb_w0", f"{k}, letters {f['letters']:.2f}, {n / MB:.1f} MB: BWT without word transform"
    if objective == "balanced":
        if not qzmf_available():
            codec, why = predict(f, "max", ram_mb)
            return codec, f"mcm fork not built -> max objective: {why}"
        if k == "medical" or k == "image":
            return predict(f, "max", ram_mb)
        if k == "csv" and f.get("fields", 0) > QZMF_FIELDS_MAX:
            # hctsa (33 MB, ~10,000 columns), PC2: qzmf 8,510,953 B 13.3 s; x18 qzc2 7,009,669 B 44.7 s (-17.6%);
            # qzs 7,107,262 B 169 s. qzs is slower than x18, so the default never picks it: x18 (or its lean preset)
            codec, why = predict(f, "max", ram_mb)
            if codec == "qzs":
                codec, why = predict(dict(f, letters=0.0), "max", ram_mb)
            return codec, (f"CSV ~{f['fields']} fields per row > {QZMF_FIELDS_MAX} (fork table model cannot engage; "
                           f"hctsa: x18 17.6% smaller than the fork) -> {why}")
        return "qzmf", f"{k}: GPL mcm fork -q{qzmf_level(ram_mb)} (CSV + text contexts)"
    # objective max
    if k == "medical" and f["h0"] < 0.1:
        return "qzb_w0", f"near-constant volume (segmentation / label map, entropy {f['h0']:.3f} b/B): BWT beats CM"
    if k == "medical" and f["magic"] == "nifti":
        return "qzm", "NIfTI volume: medical codec"
    if k == "csv" and n >= MB and f["letters"] >= 0.01:
        codec, why = "qzs", "CSV with text/categorical fields: column transform + CM"
    elif k == "log" and f["spaces"] < 0.05:
        codec, why = "qzs", "field-delimited log (few spaces): field transform + CM"
    elif k == "image":
        codec, why = "qzc2", "JPEG/PNG: CM squeezes headers / metadata (stored if not smaller)"
    elif k == "text" and f["letters"] > 0.6 and f["spaces"] > 0.1 and n >= MB:
        codec, why = "qzc2_text", "prose: word dictionary (sorted codes) + context mixing"
    else:
        codec, why = "qzc2", {"csv": "small or purely numeric CSV", "log": "free-text log"}.get(k, k) + ": context mixing"
    if codec in ("qzc2", "qzc2_text"):
        budget = ram_mb or int(os.environ.get("QZIP_RAM_MB", "0")) or phys_ram_mb() * 3 // 4
        need = qzc2_ram_mb(n, 8192 if codec == "qzc2_text" else 12288, prose=codec == "qzc2_text")
        if need > budget:
            return ("qzc2_text_lean" if codec == "qzc2_text" else "qzc2_lean"), \
                f"{why}; RAM budget {budget} MB < {need} MB needed -> lean preset"
    return codec, why

# ------------------------------------------------------------------ codecs (subprocess wrappers)
def free_cores():
    """Idle logical CPUs right now: CPU count minus the 1-minute load average (at least 1)."""
    n = os.cpu_count() or 1
    try:
        n -= int(os.getloadavg()[0] + 0.5)
    except (OSError, AttributeError):
        pass
    return max(1, n)

def qzc2_threads(n_bytes):
    """Segment threads for qzc2 (QZ_THREADS). Default 1: a single model is the smallest output, and every split
    measured >= 3% larger on enwik8 (2 segments +3.3%, 4 +7%, 8 +11%), so the 'max' objective never splits on its own.
    QZIP_QZC2_THREADS=N forces N; QZIP_QZC2_THREADS=auto uses the idle cores (capped at 16 and at one segment per 2 MB).
    QZIP_THREADS is NOT read here: it is the libbsc thread count in src/native/qz_native.py (default 8), and sharing it
    would silently split qzc2 into 8 segments (about +10% size on enwik8)."""
    v = os.environ.get("QZIP_QZC2_THREADS", "1").strip().lower()
    t = min(16, free_cores()) if v == "auto" else (int(v) if v.isdigit() else 1)
    return max(1, min(t, n_bytes // (2 * MB) or 1))

def _run(args, env=None):
    r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    if r.returncode:
        raise RuntimeError(f"{os.path.basename(args[0])} failed: {r.stderr.decode(errors='replace')[-300:]}")

def _bin(name):
    p = os.path.join(BIN, name)
    if not os.path.exists(p):
        raise RuntimeError(f"qzcore binary {p} missing: run src/qzcore/build.sh")
    return p

def _with_files(data, fn):
    d = tempfile.mkdtemp(prefix="qzr_")
    try:
        i, o = os.path.join(d, "in"), os.path.join(d, "out")
        with open(i, "wb") as fh:
            fh.write(data)
        fn(i, o)
        with open(o, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _env():
    e = dict(os.environ); e.setdefault("QZPY", sys.executable); e.setdefault("QZC_BIN", os.path.join(BIN, "qzc"))
    return e

def _qzmf_bin():
    if not os.path.exists(QZMF_BIN):
        raise RuntimeError(f"mcm fork binary {QZMF_BIN} missing: run make -C src/native qzmf")
    return QZMF_BIN

def _qzmf_run(args, cwd):
    # mcm prints progress to stdout; it stores the input name ("in", relative) and restores it into the cwd
    # QZF_* are the fork's dev A/B knobs: encoder and decoder must agree, so they never leak in from the caller's env
    env = {k: v for k, v in os.environ.items() if not k.startswith("QZF_")}
    r = subprocess.run([_qzmf_bin()] + args, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError(f"qzmf failed: {r.stderr.decode(errors='replace')[-300:]}")

def qzmf_encode(data, ram_mb=None):
    d = tempfile.mkdtemp(prefix="qzmf_")
    try:
        with open(os.path.join(d, "in"), "wb") as fh:
            fh.write(data)
        _qzmf_run([f"-q{qzmf_level(ram_mb)}", "in", "out"], d)
        with open(os.path.join(d, "out"), "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)

def qzmf_decode(blob):
    d = tempfile.mkdtemp(prefix="qzmf_")
    try:
        with open(os.path.join(d, "blob"), "wb") as fh:
            fh.write(blob)
        x = os.path.join(d, "x"); os.mkdir(x)
        _qzmf_run(["d", "../blob", "restored"], x)
        with open(os.path.join(x, "in"), "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)

def _qze(args, data):
    if not os.path.exists(QZE_BIN):
        raise RuntimeError(f"qze binary {QZE_BIN} missing: run make native")
    r = subprocess.run([QZE_BIN] + args + ["-", "-"], input=bytes(data), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError("qze failed: " + r.stderr.decode(errors="replace")[-300:])
    return r.stdout

def _tier_threads():
    v = os.environ.get("QZIP_TIER_THREADS", "").strip()
    return int(v) if v.isdigit() and int(v) > 0 else min(8, free_cores())

def encode(codec, data, ram_mb=None):
    if codec in ("qze_fast", "qze_balanced"):
        return _qze(["c", "-1" if codec == "qze_fast" else "-2", "-T", str(_tier_threads())], data)
    if codec == "qzpar":
        try:
            from src import qzpar
        except ImportError:
            import qzpar
        budget = ram_mb or int(os.environ.get("QZIP_RAM_MB", "0")) or phys_ram_mb() * 3 // 4
        return qzpar.compress(bytes(data), _tier_threads(), budget)[0]
    if codec == "qzmf":
        return qzmf_encode(bytes(data), ram_mb)
    if codec == "stored":
        return bytes(data)
    if codec == "xz":
        import lzma; return lzma.compress(data, preset=9)
    if codec == "zstd":
        import zstandard as zs
        p = zs.ZstdCompressionParameters.from_level(19, window_log=min(27, max(10, (len(data) - 1).bit_length())), enable_ldm=True)
        return zs.ZstdCompressor(compression_params=p).compress(data)
    if codec.startswith("qzc2"):
        m = ram_mb or int(os.environ.get("QZIP_RAM_MB", "0")) or 12288
        if codec == "qzc2_text":
            # the dictionary shrinks the CM input, so the hash table can be smaller: enwik8 at -m 8192 (5.5 GB RSS)
            # was 17,835,480 B vs 17,833,950 B for raw text at -m 12288 (9.6 GB), and 17,781,803 B at -m 12288
            m = min(m, 8192)
        extra = {"qzc2_lean": ["-l", "1"], "qzc2_text": ["auto", "2"], "qzc2_text_lean": ["-l", "1", "auto", "2"]}.get(codec, [])
        m = str(m)
        t = qzc2_threads(len(data))
        env = dict(os.environ, QZ_THREADS=str(t))
        return _with_files(data, lambda i, o: _run([_bin("qzc2"), "c", i, o, "-m", m] + extra, env))
    if codec in ("qzb", "qzb_w0"):
        return _with_files(data, lambda i, o: _run([_bin("qzb"), "c", i, o, "1" if codec == "qzb" else "0"]))
    if codec == "qzs":
        return _with_files(data, lambda i, o: _run([_bin("qzs"), "c", i, o], _env()))
    if codec == "qzm":
        return _with_files(data, lambda i, o: _run([_bin("qzm"), "c", i, o], _env()))
    raise ValueError(f"unknown codec {codec!r}; choose from {CODECS}")

METHOD = {"qzmf": "qzmf", "qzc2": "qzc2", "qzc2_text": "qzc2", "qzc2_lean": "qzc2", "qzc2_text_lean": "qzc2", "qzs": "qzs", "qzm": "qzm", "qzb": "qzb", "qzb_w0": "qzb",
          "zstd": "stored_zstd", "xz": "stored_xz", "stored": "stored",
          "qze_fast": "qze", "qze_balanced": "qze", "qzpar": "qzpar"}

def decode(method, blob):
    if method == "qze":
        return _qze(["d", "-T", str(_tier_threads())], blob)
    if method == "qzpar":
        try:
            from src import qzpar
        except ImportError:
            import qzpar
        return qzpar.decompress(bytes(blob), _tier_threads())
    if method == "qzmf":
        return qzmf_decode(blob)
    if method == "qzc2":
        return _with_files(blob, lambda i, o: _run([_bin("qzc2"), "d", i, o]))
    if method == "qzb":
        return _with_files(blob, lambda i, o: _run([_bin("qzb"), "d", i, o]))
    if method in ("qzs", "qzm"):
        return _with_files(blob, lambda i, o: _run([_bin(method), "d", i, o], _env()))
    raise KeyError(method)

def register(R):
    """Hook the qzcore decoders into src/qzip_routes' registry (stored/xz/zstd decoders already exist there)."""
    for m in ("qzmf", "qzc2", "qzb", "qzs", "qzm", "qze", "qzpar"):
        R.register_decoder(m, lambda b, m=m: decode(m, b))

def compress(data, objective="balanced", codec=None, ram_mb=None, verify=None):
    """Predict (unless codec is given), optionally verify the pick on a sample (src/qzverify.py), encode once.
    verify: None = env QZIP_VERIFY (default on; "0" turns it off), True / False. Returns (method, blob, route_info)."""
    t0 = time.perf_counter()
    f = analyze(data)
    vinfo = None
    if codec:
        if codec not in CODECS:
            raise ValueError(f"unknown codec {codec!r}; choose from {CODECS}")
        why = "override"
    else:
        codec, why = predict(f, objective, ram_mb)
        if verify is None:
            verify = os.environ.get("QZIP_VERIFY", "1") not in ("", "0") and codec not in ("qze_fast", "qze_balanced", "qzpar")
        if verify:
            try:
                from src import qzverify as V
            except ImportError:
                import qzverify as V
            pick = codec
            codec, vinfo = V.verify(data, f, objective, pick, lambda c, b: encode(c, b, ram_mb), ram_mb)
            if codec != pick:
                why = f"verifier: {vinfo['verdict']} ({vinfo['pair']}, sample {vinfo['rival_vs_pick']:+.2%})"
    t1 = time.perf_counter()
    blob = encode(codec, data, ram_mb)
    info = {"detected_type": f["kind"], "type_reason": why, "chosen": codec, "objective": objective,
            "features": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.items()},
            "predict_seconds": round(t1 - t0, 4), "encode_seconds": round(time.perf_counter() - t1, 2)}
    if vinfo:
        info["verifier"] = vinfo
    return METHOD[codec], blob, info

if __name__ == "__main__":   # python -m src.qzrouter FILE [balanced|max|fast] : print the prediction only
    d = open(sys.argv[1], "rb").read()
    f = analyze(d); print(predict(f, sys.argv[2] if len(sys.argv) > 2 else "balanced"), f)
