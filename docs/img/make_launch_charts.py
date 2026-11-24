#!/usr/bin/env python3
"""Launch charts (PNG + SVG) for the README and posts. Every number is a measured, byte-exact round trip; sources:
src/engine_mcmfork/bench/RESULTS.md + pc2_results.tsv (fork, mcm), src/qzcore/RESULTS.md (x18, qzs, xz, zstd, qzb,
libbsc). Hardware: PC2, Intel i9-14900KF, shared machine, nice 19 + ionice -c3, one job at a time.
Run: python3 docs/img/make_launch_charts.py  (needs matplotlib)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mt

OUT = Path(__file__).resolve().parent
plt.rcParams.update({"figure.facecolor": "white", "axes.grid": True, "grid.color": "#e5e7eb", "axes.spines.top": False,
                     "axes.spines.right": False, "font.size": 10.5, "axes.titleweight": "bold", "axes.titlesize": 13})
C = {"fork": "#059669", "x18": "#2563eb", "mcm": "#7c3aed", "qzs": "#0ea5e9", "xz": "#dc2626", "zstd": "#ea580c",
     "bwt": "#64748b"}
NOTE = "PC2 · i9-14900KF · shared machine, nice 19 · every point a byte-exact round trip · sizes include headers"

def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=170, bbox_inches="tight")
    fig.savefig(OUT / f"{name}.svg", bbox_inches="tight")
    plt.close(fig); print("wrote", name)

def size_vs_time(name, title, pts, ylim, note):
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    for lab, size, sec, key, off in pts:
        big = key == "fork"
        ax.scatter([sec], [size / 1e6], s=300 if big else 90, marker="*" if big else "o", color=C[key], zorder=3,
                   edgecolors="white", linewidths=0.8)
        ax.annotate(lab, (sec, size / 1e6), textcoords="offset points", xytext=off, fontsize=9.5,
                    fontweight="bold" if big else "normal", color="#0f172a")
    ax.set_xscale("log"); ax.xaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:g} s"))
    ax.set_ylim(*ylim)
    ax.set_xlabel("compress time (wall, log scale) — lower-left is better")
    ax.set_ylabel("compressed size (MB)")
    ax.set_title(title, loc="left")
    fig.text(0.01, -0.04, note, fontsize=8, color="#475569", wrap=True)
    save(fig, name)

def main():
    size_vs_time("enwik8_size_vs_speed", "enwik8 (100 MB of Wikipedia): size vs compress time", [
        ("QZip default (mcm fork)  17.36 MB · 44 s", 17_361_988, 44.5, "fork", (-150, -24)),
        ("mcm 0.84 -x11  17.54 MB · 41 s", 17_543_256, 40.5, "mcm", (-150, 12)),
        ("QZip --max (x18)  17.26 MB · 144 s", 17_257_456, 143.9, "x18", (-105, 14)),
        ("zstd -19 --long  26.50 MB", 26_504_540, 128.5, "zstd", (-60, 10)),
        ("xz -9e  24.83 MB", 24_831_648, 169.7, "xz", (-50, 10)),
        ("libbsc (BWT)  20.80 MB · 4 s", 20_803_016, 3.99, "bwt", (8, 6)),
    ], (16.3, 27.5), NOTE + ". zstd/xz/libbsc times are from an earlier, more loaded session (21 Nov); zstd and xz decode in "
                    "under 3 s, the context-mixing coders take about as long to decode as to encode.")
    size_vs_time("enwik9_size_vs_speed", "enwik9 (1 GB of Wikipedia): size vs compress time", [
        ("QZip default (mcm fork)  136.85 MB · 6.2 min", 136_854_237, 373.4, "fork", (10, -18)),
        ("mcm 0.84 -x11  138.57 MB · 5.6 min", 138_569_355, 336.0, "mcm", (-215, 2)),
        ("QZip --max (x18)  137.08 MB · 21.6 min", 137_076_090, 1296.2, "x18", (-105, 14)),
        ("QZip fast (qzb, BWT)  162.45 MB · 22 s", 162_445_705, 21.97, "bwt", (8, 6)),
    ], (133, 165), NOTE + ". 23 Nov 2026 (qzb 21 Nov). The fork's 6.2 GB RAM equals mcm's; x18 uses 12.0 GB.")

    # CSV wins: compression ratio per file, higher is better
    files = ["youth\n55.5 MB", "TRI_By_Name\n68.4 MB", "Panel 2000-22\n58.2 MB", "fndds (3)\n17.0 MB"]
    orig = [55_547_651, 68_403_261, 58_192_381, 17_017_713]
    series = [("QZip default (mcm fork)", "fork", [1_435_856, 4_254_555, 7_783_320, 193_851]),
              ("QZip --max (qzs)", "qzs", [1_408_094, 4_775_793, 8_230_962, 205_478]),
              ("mcm 0.84 -x11", "mcm", [1_798_461, 5_418_808, 10_522_222, 246_276]),
              ("xz -9e", "xz", [3_482_548, 7_182_196, 12_846_924, 728_608]),
              ("zstd -19", "zstd", [3_743_251, 7_618_709, 13_691_107, 768_113])]
    fig, ax = plt.subplots(figsize=(9.2, 5.0))
    w = 0.16
    for i, (lab, key, sizes) in enumerate(series):
        xs = [j + (i - 2) * w for j in range(len(files))]
        r = [o / s for o, s in zip(orig, sizes)]
        ax.bar(xs, r, w, label=lab, color=C[key], zorder=3)
        if key == "fork":
            for x, v in zip(xs, r):
                ax.text(x, v + 1, f"{v:.0f}:1", ha="center", fontsize=8.5, fontweight="bold", color=C[key])
    ax.set_xticks(range(len(files))); ax.set_xticklabels(files)
    ax.set_ylabel("compression ratio (higher is better)")
    ax.set_title("Real-world CSVs: compression ratio", loc="left")
    ax.legend(fontsize=8.5, ncol=2, frameon=False)
    fig.text(0.01, -0.06, NOTE + ". Fork vs mcm: −20% to −26% size at 1.2× mcm's time. qzs is smaller on youth (−1.9%) "
             "but 11–15× slower; xz -9 on fndds. Sources: src/engine_mcmfork/bench/RESULTS.md, src/qzcore/RESULTS.md.",
             fontsize=8, color="#475569", wrap=True)
    save(fig, "csv_ratio")

if __name__ == "__main__":
    main()
