"""QZip router verifier: check the router's pick before the full encode, in the spirit of JEV-style typed decisions.

Clean-room QZip code, inspired only by the verify / abstain pattern (typed choice -> confidence -> gate; deterministic
evidence owns the final branch). No model calls and nothing learned online.

  1. Typed choice. The router's pick and its known rival get probabilities from how close the deciding features
     are to the rule thresholds; confidence = 0.55 * (1 - H / log K) + 0.45 * (p1 - p2).
  2. Gate. "act" if confidence >= 0.70 and the pick is not a known confusable pair; otherwise "verify". An input
     whose features are far from every file the rules were fitted on (out of distribution) is also verified, against
     plain qzc2.
  3. Evidence. Both candidates encode the same stratified sample (whole lines for text / CSV / logs, with the CSV
     header; a slab of axial slices for NIfTI). The sample is sized for about 2.5% of the predicted full compress
     time (clamped to 64 KB - 1 MB), and a trial that overruns its time cap is abandoned.
  4. Commit. Switch only if the rival's sample is smaller by more than DELTA. A smaller gap is ambiguous, and the
     router's rule pick (the safe default) stands. Every step goes into the route info.
"""
import math, os, struct, time

DELTA = 0.01            # the sample must favour the rival by > 1% to overturn the rule pick
TIME_SHARE = 0.025      # evidence budget, share of the predicted full compress time
MIN_SAMPLE, MAX_SAMPLE = 64 << 10, 1 << 20
# compress throughput in MB/s: medians over the >= 4 MB files of bench/router (PC2, loaded)
# qzmf (GPL mcm fork -q11): enwik8 100 MB in 44.5 s on PC2 (2.25 MB/s), CSVs ~1.2x slower -> 2.0
MBS = {"qzmf": 2.0, "qzc2": 0.66, "qzc2_text": 0.66, "qzc2_lean": 0.79, "qzc2_text_lean": 0.79, "qzs": 0.22, "qzm": 0.69,
       "qzb": 42.5, "qzb_w0": 82.1, "zstd": 4.98, "xz": 4.26, "stored": 1e4}

# feature centroids of the fitting corpus, by kind: letters, digits, spaces, h0, h1, trial (bench/router, 53 files)
_REF = None

def _sig(x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, x))))

def confidence(probs):
    k = len(probs)
    h = -sum(p * math.log(p) for p in probs if p > 1e-12)
    peaked = 1.0 - h / math.log(k) if k > 1 else 1.0
    s = sorted(probs, reverse=True)
    return max(0.0, min(1.0, 0.55 * peaked + 0.45 * (s[0] - (s[1] if k > 1 else 0.0))))

def _lr(x, t):
    """signed log-distance of x from threshold t (0 at the threshold)."""
    return math.log(max(x, 1e-9) / max(t, 1e-9))

def rival(f, objective, pick):
    """(rival codec or None, margin z >= 0 meaning 'how far the deciding feature sits from its threshold', pair tag)."""
    k, n = f["kind"], f["size"]
    MB = 1 << 20
    if objective == "balanced":
        # qzmf is the default engine and is not second-guessed; a predicted wide-CSV exception is checked against it
        if k == "csv" and pick != "qzmf" and pick != "stored":
            return "qzmf", 0.0, "wide CSV: max codec vs mcm fork"
        return None, 9.0, ""
    if objective == "fast":
        if pick in ("qzb", "qzb_w0") and k in ("text", "csv", "log") and f["letters"] > 0.05:
            other = "qzb_w0" if pick == "qzb" else "qzb"
            return other, min(abs(_lr(n, 16 * MB)), abs(_lr(f["letters"], 0.1)) * 2), "word transform on/off"
        if pick == "stored" and k == "incompressible":
            return "qzb_w0", 0.0, "stored vs BWT"
        return None, 9.0, ""
    if k == "medical" and pick == "qzm":
        return "qzc2", 0.0, "qzm vs qzc2 (medical: 16 vs 9 wins measured)"
    if pick == "qzs":
        return "qzc2", 0.0, "qzs vs qzc2 (CSV / field logs)"
    if k in ("csv", "log") and pick.startswith("qzc2"):
        return "qzs", 0.0, "qzc2 vs qzs (CSV / logs)"
    if pick in ("qzc2", "qzc2_text") and k == "text":
        other = "qzc2" if pick == "qzc2_text" else "qzc2_text"
        z = min(abs(_lr(f["letters"], 0.6)) * 4, abs(_lr(f["spaces"], 0.1)) * 2, abs(_lr(n, MB)) * 2)
        return other, z, "prose vs code (word dictionary)"
    if pick == "stored" and k == "incompressible":
        return "zstd", 0.0, "stored vs zstd"
    return None, 9.0, ""

def ood(f):
    """True if the feature vector is far from every reference vector of the same kind."""
    global _REF
    if _REF is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qzcore", "bench", "router", "features.tsv")
        _REF = []
        if os.path.exists(p):
            for l in open(p):
                r = l.rstrip("\n").split("\t")
                if len(r) == 8 and r[0] != "file":
                    _REF.append((r[1], [float(x) for x in r[2:]]))
    if not _REF:
        return False
    v = [f["letters"], f["digits"], f["spaces"], f["h0"] / 8, f["h1"] / 8, min(f["trial"], 1.2)]
    same = [w for kind, w in _REF if kind == f["kind"]]
    if not same:
        return True
    return min(math.sqrt(sum((a - b) ** 2 for a, b in zip(v, w))) for w in same) > 0.15

def _nifti_slab(data, nbytes):
    """Header + a run of axial slices around 40% depth as a smaller valid NIfTI-1 (dim[3] patched)."""
    h = bytes(data[:352])
    if struct.unpack("<i", h[:4])[0] != 348:
        return None
    dim = list(struct.unpack("<8h", h[40:56])); bitpix = struct.unpack("<h", h[72:74])[0]
    vo = int(struct.unpack("<f", h[108:112])[0]); nx, ny, nz = dim[1], dim[2], dim[3]
    sl = nx * ny * max(1, bitpix // 8)
    if sl <= 0 or nz < 2 or vo < 352:
        return None
    k = max(3, min(nz, nbytes // sl)); k = min(k, nz); z0 = max(0, min(nz - k, int(nz * 0.4)))   # >= 3 slices: 3D context
    dim[3] = k
    hdr = bytearray(bytes(data[:vo])); hdr[40:56] = struct.pack("<8h", *dim)
    return bytes(hdr) + bytes(data[vo + z0 * sl: vo + (z0 + k) * sl])

def sample(data, f, nbytes):
    n = len(data)
    if f.get("magic") == "nifti":
        s = _nifti_slab(data, nbytes)
        if s:
            return s
    if n <= nbytes:
        return bytes(data)
    lines = f["kind"] in ("text", "csv", "log")
    head = b""
    if f["kind"] == "csv":
        e = bytes(data[:65536]).find(b"\n")
        head = bytes(data[:e + 1]) if 0 <= e < 65536 else b""
    blocks = 8 if nbytes >= 512 << 10 else 4
    b = nbytes // blocks; out = [head]
    for i in range(blocks):
        s = (n - b) * i // max(1, blocks - 1)
        chunk = bytes(data[s:s + b + (4096 if lines else 0)])
        if lines:
            a = chunk.find(b"\n") + 1 if s else 0
            z = chunk.rfind(b"\n", 0, b + 4096) + 1
            chunk = chunk[a:z] if z > a else chunk[:b]
        out.append(chunk[:b + 4096])
    return b"".join(out)

MIN_FULL_SECONDS = 10.0  # below this the whole encode is cheap; evidence would cost more than it can save
# pairs where the sample was measured NOT to predict the full-file winner: qzs's column model gains with input size,
# so short samples favour qzc2 (bench/router ver_c: 2 of 4 sample switches wrong, by up to 10% on the sample)
UNRELIABLE = ("qzs vs qzc2 (CSV / field logs)", "qzc2 vs qzs (CSV / logs)")

def skip_reason(objective, pair, full_seconds):
    """Why evidence is not worth gathering (None = gather it). Measured on bench/router (ver_c / ver_h)."""
    if objective == "fast":
        return "fast objective: trials cost 9-38% of a qzb encode for <= 0.2% size"
    if pair in UNRELIABLE:
        return "sample evidence unreliable for this pair"
    if full_seconds < MIN_FULL_SECONDS:
        return f"predicted encode {full_seconds:.1f} s < {MIN_FULL_SECONDS:.0f} s"
    return None

def verify(data, f, objective, pick, encode, ram_mb=None, force=False):
    """Return (codec, info). encode(codec, bytes) -> bytes is the router's encoder."""
    t0 = time.perf_counter()
    riv, z, pair = rival(f, objective, pick)
    far = ood(f)
    if riv is None and far and pick not in ("qzc2", "stored") and objective == "max":
        riv, z, pair = "qzc2", 0.0, "out of distribution -> check against qzc2"
    p1 = _sig(1.5 * z + 0.4) if riv else 1.0          # z = 0 (no separating feature) -> p1 = 0.6, conf ~ 0.11
    conf = confidence([p1, 1 - p1]) if riv else 1.0
    info = {"pick": pick, "rival": riv, "pair": pair, "confidence": round(conf, 3), "ood": far}
    if riv is None or (conf >= 0.70 and not far and not force):
        info.update(gate="act", final=pick, verify_seconds=round(time.perf_counter() - t0, 4))
        return pick, info
    n = len(data)
    full = n / 1e6 / MBS.get(pick, 1.0)                 # predicted full compress seconds
    why = None if force else skip_reason(objective, pair, full)
    if why:
        info.update(gate="act", final=pick, skip=why, predicted_full_seconds=round(full, 2),
                    verify_seconds=round(time.perf_counter() - t0, 4))
        return pick, info
    per_mb = 1 / MBS.get(pick, 1.0) + 1 / MBS.get(riv, 1.0)
    nb = int(max(MIN_SAMPLE, min(MAX_SAMPLE, TIME_SHARE * full / per_mb * 1e6)))
    s = sample(data, f, nb)
    cap = max(1.0, 4 * TIME_SHARE * full)               # hard cap per trial: never let evidence cost more than ~10%
    sizes = {}
    for c in (pick, riv):
        ts = time.perf_counter()
        try:
            sizes[c] = len(encode(c, s))
        except Exception as e:                           # a codec that cannot take the sample loses the trial
            sizes[c] = None; info["trial_error_" + c] = str(e)[:120]
        if time.perf_counter() - ts > cap:
            info["timeout"] = c
            break
    a, b = sizes.get(pick), sizes.get(riv)
    if a and b:
        rel = b / a - 1.0
        final = riv if rel < -DELTA else pick
        verdict = "switch" if final == riv else ("keep" if rel > DELTA else "ambiguous -> rule pick")
    else:
        rel, final, verdict = None, pick, "no evidence -> rule pick"
    info.update(gate="verify", sample_bytes=len(s), trial_sizes=sizes, rival_vs_pick=None if rel is None else round(rel, 4),
                verdict=verdict, final=final, verify_seconds=round(time.perf_counter() - t0, 3),
                predicted_full_seconds=round(full, 2))
    return final, info
