#!/usr/bin/env python3
"""Render bench/everyday/bench.py JSON lines as a markdown table (one table per file, smallest first)."""
import json, sys
from collections import defaultdict

def main(paths):
    rows = defaultdict(list)
    for p in paths:
        for line in open(p):
            line = line.strip()
            if line:
                r = json.loads(line); rows[(r.get("platform", "?"), r["file"], r["bytes"])].append(r)
    for (plat, f, n), rs in rows.items():
        print(f"\n**{f}** ({n / 1e6:.1f} MB, {plat}, {rs[0].get('threads')} threads)\n")
        print("| codec | size (B) | ratio | compress MB/s | decompress MB/s | peak RAM c / d (MB) | round trip |")
        print("|---|---:|---:|---:|---:|---:|---|")
        for r in sorted(rs, key=lambda r: r["size"]):
            print(f"| {r['codec']} | {r['size']:,} | {r['ratio']:.2f} | {r['comp_MBps']:.1f} | {r['decomp_MBps']:.1f} | "
                  f"{r['comp_rss_MB']:.0f} / {r['decomp_rss_MB']:.0f} | {r['roundtrip']} |")

if __name__ == "__main__":
    main(sys.argv[1:])
