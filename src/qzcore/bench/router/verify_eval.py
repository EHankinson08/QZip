#!/usr/bin/env python3
"""Run the router + verifier (src/qzverify.py) on a corpus, with real sample trials, and score it against ground truth.

  verify_eval.py --features CORPUS_DIR > features.tsv          reference feature vectors for the OOD test
  verify_eval.py CORPUS_DIR OUT.jsonl GT_TSV... [--workers 2]  trials; then scores both routers
Needs QZCORE_BIN with qzc2 qzc qzb qzs qzm (src/qzcore after build.sh).
"""
import json, mmap, os, sys, time
from multiprocessing import Pool
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "..", "..")]
import qzrouter as Q, qzverify as V
from router_eval import load, FAST

def _open(p):
    fh = open(p, "rb"); n = os.fstat(fh.fileno()).st_size
    return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) if n else b""

def features(d):
    print("file\tkind\tletters\tdigits\tspaces\th0n\th1n\ttrial")
    for f in sorted(os.listdir(d)):
        a = Q.analyze(_open(os.path.join(d, f)))
        print("\t".join([f, a["kind"]] + [f"{x:.4f}" for x in (a["letters"], a["digits"], a["spaces"], a["h0"] / 8,
                                                             a["h1"] / 8, min(a["trial"], 1.2))]))

def one(p):
    data = _open(p); f = Q.analyze(data); rec = {"file": os.path.basename(p), "n": len(data), "kind": f["kind"]}
    for obj in ("max", "fast"):
        pick, _ = Q.predict(f, obj)
        t = time.perf_counter()
        final, info = V.verify(data, f, obj, pick, lambda c, b: Q.encode(c, b))
        info["wall"] = round(time.perf_counter() - t, 3)
        rec[obj] = info
    return rec

def replay(recs):
    """Apply the current skip policy (qzverify.skip_reason) to recorded trials: a skipped trial means final = pick,
    zero added time. Trials themselves are not re-run."""
    out = []
    for r in recs:
        r = json.loads(json.dumps(r))
        for obj in ("max", "fast"):
            i = r[obj]
            if i.get("gate") == "verify":
                full = i.get("predicted_full_seconds") or r["n"] / 1e6 / V.MBS.get(i["pick"], 1.0)
                why = V.skip_reason(obj, i.get("pair", ""), full)
                if why:
                    r[obj] = {"pick": i["pick"], "final": i["pick"], "gate": "act", "skip": why, "verify_seconds": 0.0}
        out.append(r)
    return out

def score(recs, gt):
    for obj in ("max", "fast"):
        rows = {"router": [], "verified": []}; vt = []; nv = 0; unm = 0
        for r in recs:
            m = dict(gt.get(r["file"], {})); m["stored"] = (r["n"], 0.0, 0.0, 0)
            cand = {c: v for c, v in m.items() if obj == "max" or c in FAST}
            best = min(cand, key=lambda c: cand[c][0])
            i = r[obj]
            if i.get("gate") == "verify":
                nv += 1
            for tag, c in (("router", i["pick"]), ("verified", i["final"])):
                if c not in cand:
                    unm += tag == "verified"; continue
                rows[tag].append((r["file"], cand[c][0] / cand[best][0] - 1))
            fin = i["final"]
            full = cand[fin][1] if fin in cand and cand[fin][1] > 0 else None
            if full and i.get("gate") == "verify":
                vt.append((i["verify_seconds"] / full * 100, r["file"]))
        for tag, L in rows.items():
            if not L: continue
            ex = sum(1 for _, x in L if x <= 0); w1 = sum(1 for _, x in L if x <= 0.01)
            worst = max(L, key=lambda t: t[1])
            print(f"[{obj}] {tag:9s} files {len(L)}: best {ex} ({ex/len(L)*100:.0f}%), within 1% {w1} ({w1/len(L)*100:.0f}%), "
                  f"mean loss {sum(x for _, x in L)/len(L)*100:.3f}%, worst {worst[1]*100:.2f}% ({worst[0]})")
        if vt:
            vs = sorted(x for x, _ in vt)
            print(f"[{obj}] verifier ran on {nv} files; added time / full compress of the final codec: median {vs[len(vs)//2]:.2f}%, "
                  f"max {vs[-1]:.2f}% ({max(vt)[1]}); verified pick unmeasured on {unm}")

def main():
    a = sys.argv[1:]
    if a[0] == "--features":
        return features(a[1])
    w = int(a[a.index("--workers") + 1]) if "--workers" in a else 2
    if "--workers" in a: del a[a.index("--workers"):a.index("--workers") + 2]
    a = [x for x in a if x != "--replay"]
    d, out, tsvs = a[0], a[1], a[2:]
    done = {}
    if os.path.exists(out):
        for l in open(out):
            r = json.loads(l); done[r["file"]] = r
    todo = [os.path.join(d, f) for f in sorted(os.listdir(d)) if f not in done]
    if todo:
        with open(out, "a") as fo, Pool(w) as pool:
            for r in pool.imap_unordered(one, todo):
                fo.write(json.dumps(r) + "\n"); fo.flush(); done[r["file"]] = r
    recs = list(done.values())
    if "--replay" in sys.argv:
        recs = replay(recs)
    score(recs, load(tsvs))

if __name__ == "__main__":
    main()
