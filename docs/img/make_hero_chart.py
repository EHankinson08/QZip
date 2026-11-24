#!/usr/bin/env python3
"""README hero chart: compressed size relative to xz -9e (lower is better).

Every number is a measured, byte-exact round trip copied from bench/LAUNCH_FACTS.md (sources:
src/engine_mcmfork/bench/RESULTS.md, src/qzcore/RESULTS.md). Sizes in bytes, headers included.
Hardware: PC2, Intel i9-14900KF, shared machine, nice 19 + ionice -c3, single-threaded coders.
Run: python3 docs/img/make_hero_chart.py  (needs matplotlib)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
# file label, QZip default, mcm 0.84 -x11, xz -9e, zstd -19 --long
ROWS = [
    ("enwik8\n(Wikipedia, 100 MB)", 17_361_988, 17_543_256, 24_831_648, 26_504_540),
    ("records100k.csv\n(27.8 MB)", 5_098_228, 5_261_158, 8_018_604, 8_427_879),
    ("TRI_By_Name.csv\n(68.4 MB)", 4_254_555, 5_418_808, 7_182_196, 7_618_709),
    ("Panel_2000_2022.csv\n(58.2 MB)", 7_783_320, 10_522_222, 12_846_924, 13_691_107),
    ("youth.csv\n(55.5 MB)", 1_435_856, 1_798_461, 3_482_548, 3_743_251),
    ("fndds (3).csv\n(17.0 MB)*", 193_851, 246_276, 728_608, 768_113),
]
SERIES = [("QZip (default)", "#059669", 1), ("mcm 0.84 -x11", "#7c3aed", 2), ("xz -9e", "#dc2626", 3),
          ("zstd -19 --long", "#ea580c", 4)]

plt.rcParams.update({"figure.facecolor": "white", "font.size": 10.5, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.titleweight": "bold", "axes.titlesize": 14})
fig, ax = plt.subplots(figsize=(10, 5.6))
h = 0.2
for i, (lab, color, col) in enumerate(SERIES):
    ys = [r - 1.5 * h + i * h for r in range(len(ROWS))]
    vals = [100 * row[col] / row[3] for row in ROWS]
    ax.barh(ys, vals, height=h * 0.92, color=color, label=lab, zorder=3)
    if col == 1:
        for y, v in zip(ys, vals):
            ax.text(v + 1.2, y, f"{v:.0f}%", va="center", fontsize=9, fontweight="bold", color=color)
ax.set_yticks(range(len(ROWS)))
ax.set_yticklabels([r[0] for r in ROWS])
ax.invert_yaxis()
ax.axvline(100, color="#94a3b8", lw=1, ls="--", zorder=2)
ax.set_xlim(0, 118)
ax.xaxis.grid(True, color="#e5e7eb", zorder=0)
ax.set_xlabel("compressed size as % of xz -9e  (lower is better)")
ax.set_title("QZip vs mcm, xz and zstd on text and real CSVs", loc="left", pad=34)
ax.legend(loc="lower left", bbox_to_anchor=(-0.01, 1.0), frameon=False, ncol=4, fontsize=9.5)
fig.text(0.01, -0.03, "Byte-exact round trips, headers included. PC2 (i9-14900KF, shared, nice 19), single-threaded. "
         "* fndds: xz -9 (not -9e). records100k xz/zstd sizes from an 8-vCPU VM (sizes are machine-independent).\nData: bench/LAUNCH_FACTS.md. Speed and RAM: QZip default takes 1.10–1.26x mcm's time, "
         "~6 GB RAM; it decompresses as slowly as it compresses.", fontsize=8, color="#475569")
fig.tight_layout()
for ext in ("png", "svg"):
    fig.savefig(OUT / f"hero.{ext}", dpi=170 if ext == "png" else None, bbox_inches="tight")
print("wrote hero.png / hero.svg")
