#!/usr/bin/env python3
"""Per-file detail of verifier runs: sample verdict vs the full-file truth.  verify_detail.py OUT.jsonl GT_TSV... [--obj max]"""
import json, sys
from router_eval import load
a = sys.argv[1:]; obj = a[a.index("--obj") + 1] if "--obj" in a else "max"
if "--obj" in a: del a[a.index("--obj"):a.index("--obj") + 2]
gt = load(a[1:])
print("file\tn\tpick\trival\tsample_rival_vs_pick\ttrue_rival_vs_pick\tverdict\tfinal\tright\tverify_s\tfinal_full_s")
for l in open(a[0]):
    r = json.loads(l); i = r[obj]
    if i.get("gate") != "verify":
        continue
    m = gt.get(r["file"], {}); m.setdefault("stored", (r["n"], 0, 0, 0))
    p, q = m.get(i["pick"]), m.get(i["rival"])
    true = None if not (p and q) else q[0] / p[0] - 1
    right = None if true is None else ((true < 0) == (i["final"] == i["rival"]) or abs(true) < 1e-9)
    print("\t".join(map(str, [r["file"], r["n"], i["pick"], i["rival"], i.get("rival_vs_pick"),
                              None if true is None else round(true, 4), i.get("verdict"), i["final"], right,
                              i["verify_seconds"], m.get(i["final"], (0, 0))[1]])))
