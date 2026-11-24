#!/usr/bin/env python3
"""Aggregate bench.py JSON lines into one Pareto table per category and overall.

  python3 bench/everyday/pareto.py results.jsonl [...]

Per codec: total compressed / total input (ratio), total input MB / total wall seconds (compress, decompress),
max peak RSS. A codec is on the Pareto front when no other codec is at least as good on all of ratio,
compress MB/s and decompress MB/s and strictly better on one (RAM is shown, not used for dominance).
Rows that failed the sha256 round trip are excluded and listed.
"""
import json, sys
from collections import defaultdict

CATS = {"csv": ("youth.csv", "TRI_By_Name_1987_2014.csv", "Panel_2000_2022.csv"),
        "logs": ("ssh.log", "openstack.log", "healthapp.log"),
        "json": ("gh_events.json",),
        "source": ("cpython-3.12.7.tar",),
        "text": ("enwik8",),
        "mixed/binary": ("mixed_pystdlib.tar", "samba", "mozilla", "northwind.db", "paper.pdf")}

def load(paths):
    rows = {}
    for p in paths:
        for line in open(p):
            if line.strip():
                r = json.loads(line); rows[(r["file"], r["codec"])] = r
    return rows

def table(rows, files, title):
    codecs = sorted({c for (f, c) in rows if f in files})
    agg, bad = {}, []
    for c in codecs:
        rs = [rows.get((f, c)) for f in files]
        if any(r is None for r in rs):
            continue
        if any(r["roundtrip"] != "ok" for r in rs):
            bad.append(c); continue
        n = sum(r["bytes"] for r in rs); s = sum(r["size"] for r in rs)
        agg[c] = dict(ratio=n / s, size=s, c=n / 1e6 / sum(r["comp_s"] for r in rs), d=n / 1e6 / sum(r["decomp_s"] for r in rs),
                      ram=max(max(r["comp_rss_MB"], r["decomp_rss_MB"]) for r in rs), n=n)
    def dom(a, b):
        return a["ratio"] >= b["ratio"] and a["c"] >= b["c"] and a["d"] >= b["d"] and (a["ratio"] > b["ratio"] or a["c"] > b["c"] or a["d"] > b["d"])
    print(f"\n**{title}** ({len(files)} file(s), {next(iter(agg.values()))['n'] / 1e6:.0f} MB)\n" if agg else f"\n**{title}**: no data\n")
    print("| codec | ratio | size (MB) | compress MB/s | decompress MB/s | peak RAM (MB) | Pareto | dominated by |")
    print("|---|---:|---:|---:|---:|---:|:-:|---|")
    for c, a in sorted(agg.items(), key=lambda kv: -kv[1]["ratio"]):
        by = [o for o, b in agg.items() if o != c and dom(b, a)]
        print(f"| {c} | {a['ratio']:.2f} | {a['size'] / 1e6:.2f} | {a['c']:.1f} | {a['d']:.1f} | {a['ram']:.0f} | {'**yes**' if not by else ''} | {', '.join(by[:4])} |")
    if bad:
        print(f"\nround-trip failures (excluded): {', '.join(bad)}")
    return agg

def main(paths):
    rows = load(paths)
    allf = tuple(f for fs in CATS.values() for f in fs)
    table(rows, allf, "all everyday files")
    for k, fs in CATS.items():
        table(rows, fs, k)

if __name__ == "__main__":
    main(sys.argv[1:])
