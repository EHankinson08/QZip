#!/usr/bin/env python3
"""Score src/qzrouter.py predictions against measured ground truth.

  router_eval.py CORPUS_DIR GT_TSV [EXTRA_TSV...] [--ram-mb N]
GT_TSV rows (bench/router/gt.py): file codec n size c_wall c_user d_wall d_user c_rssMB d_rssMB OK|FAIL|ERR
EXTRA rows use the same columns (earlier qzcore/opt and qzcore rt.tsv runs mapped to corpus names).
Only byte-exact (OK) rows count. Prints per-file tables and the accuracy / size-loss summary for both objectives.
"""
import mmap, os, sys, time
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [_here, os.path.join(_here, "..", "..", "..")]
import qzrouter as Q

NAME = {"qzb1": "qzb", "qzb0": "qzb_w0", "zstd19": "zstd", "xz9": "xz", "qzc2": "qzc2", "qzc2l1": "qzc2_lean",
        "qzs": "qzs", "qzm": "qzm"}
FAST = ("qzb", "qzb_w0", "stored")   # qzrouter.FAST: zstd -19 / xz -9 are 10-100x slower

def load(paths):
    gt = {}
    for p in paths:
        for l in open(p):
            r = l.rstrip("\n").split("\t")
            if len(r) < 11 or r[10] != "OK" or not r[3]:
                continue
            c = NAME.get(r[1], r[1])
            gt.setdefault(r[0], {})
            # keep the first measurement of a (file, codec)
            gt[r[0]].setdefault(c, (int(r[3]), float(r[4] or 0), float(r[6] or 0), int(r[8] or 0)))
    return gt

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ram = int(sys.argv[sys.argv.index("--ram-mb") + 1]) if "--ram-mb" in sys.argv else 98304
    if "--ram-mb" in sys.argv: args.remove(str(ram))
    corpus, tsvs = args[0], args[1:]
    gt = load(tsvs)
    res = {"max": [], "fast": []}
    print("file\tsize\tkind\tpred_max\toracle_max\tloss_max%\tpred_fast\toracle_fast\tloss_fast%\tanalysis_ms\tcomp_s(pred_max)")
    for f in sorted(os.listdir(corpus)):
        p = os.path.join(corpus, f)
        with open(p, "rb") as fh:
            n = os.fstat(fh.fileno()).st_size
            mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) if n else b""
            t = time.perf_counter(); feat = Q.analyze(mm); ms = (time.perf_counter() - t) * 1000
            pm, _ = Q.predict(feat, "max", ram); pf, _ = Q.predict(feat, "fast", ram)
        m = dict(gt.get(f, {})); m["stored"] = (n, 0.0, 0.0, 0)
        row = [f, n, feat["kind"]]
        for obj, pred in (("max", pm), ("fast", pf)):
            cand = {c: v for c, v in m.items() if obj == "max" or c in FAST}
            orc = min(cand, key=lambda c: cand[c][0])
            if pred in cand:
                loss = cand[pred][0] / cand[orc][0] - 1
                res[obj].append((f, pred, orc, loss, ms, cand[pred][1], n))
                row += [pred, orc, f"{loss*100:.2f}"]
            else:
                row += [pred + "(unmeasured)", orc, "-"]
        row += [f"{ms:.1f}", f"{m[pm][1]:.1f}" if pm in m else "-"]
        print("\t".join(map(str, row)))
    for obj in ("max", "fast"):
        r = res[obj]
        if not r: continue
        exact = sum(1 for x in r if x[3] <= 0)
        within1 = sum(1 for x in r if x[3] <= 0.01)
        tot_pred = sum(x[6] * (1 + 0) for x in r)
        print(f"\n[{obj}] scored files {len(r)}: exact-best {exact} ({exact/len(r)*100:.0f}%), within 1% {within1} "
              f"({within1/len(r)*100:.0f}%), mean size loss {sum(x[3] for x in r)/len(r)*100:.3f}%, "
              f"max loss {max(x[3] for x in r)*100:.2f}% ({max(r, key=lambda x: x[3])[0]})")
        over = [x[4] / 1000 / x[5] * 100 for x in r if x[5] > 0]
        if over:
            print(f"[{obj}] analysis time / predicted codec compress time: median {sorted(over)[len(over)//2]:.3f}%, max {max(over):.3f}%")

if __name__ == "__main__":
    main()
