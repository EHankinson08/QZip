#!/usr/bin/env python3
"""Everyday Pareto chart (PNG + SVG) from bench JSONL. Every point is a measured, byte-exact round trip.

  python3 docs/img/make_everyday_pareto.py [pareto_t8.jsonl]

Hardware: Linux x86-64 development box, 8 threads, 2026-11-24, America/Chicago, nice 19 + ionice -c3.
"""
from pathlib import Path
import json, sys
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mt

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
DEFAULT = Path("/workspace/compression_review/everyday/pareto_t8.jsonl")
if not DEFAULT.exists():
    DEFAULT = ROOT / "docs" / "everyday_pareto.jsonl"

# display name, color, marker, emphasize
STYLE = {
    "qzip-balanced": ("QZip Quick", "#059669", "*", 320),
    "qzip-fast":     ("QZip Fast", "#34d399", "P", 180),
    "qzip-max":      ("QZip Deep", "#0f766e", "p", 160),
    "7z-9":          ("7z -9", "#7c3aed", "o", 90),
    "xz-9":          ("xz -9", "#dc2626", "o", 90),
    "brotli-11":     ("brotli -11", "#2563eb", "o", 90),
    "xz-6-T":        ("xz -6 -T", "#f43f5e", "o", 70),
    "7z-5":          ("7z -5", "#a78bfa", "o", 70),
    "zstd-19-T":     ("zstd -19 -T", "#ea580c", "o", 90),
    "brotli-9":      ("brotli -9", "#60a5fa", "o", 70),
    "brotli-5":      ("brotli -5", "#93c5fd", "o", 70),
    "zstd-3-T":      ("zstd -3 -T", "#f97316", "s", 90),
    "zstd-3":        ("zstd -3", "#fdba74", "s", 70),
    "lz4":           ("lz4", "#64748b", "D", 80),
    "xz-6":          ("xz -6", "#fb7185", "o", 70),
}

# slightly jittered annotation offsets so labels don't sit on points
OFF = {
    "qzip-balanced": (10, 12),
    "qzip-fast": (8, -16),
    "qzip-max": (18, -28),
    "7z-9": (8, 8),
    "xz-9": (8, -14),
    "brotli-11": (8, 2),
    "xz-6-T": (-62, 10),
    "7z-5": (8, -16),
    "zstd-19-T": (8, 12),
    "brotli-9": (8, 10),
    "brotli-5": (8, 8),
    "zstd-3-T": (8, 10),
    "zstd-3": (8, 10),
    "lz4": (10, 0),
    "xz-6": (-48, -16),
}

def load(path):
    rows = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("threads", 8) != 8:
            continue
        if r.get("roundtrip") != "ok":
            continue
        rows[(r["file"], r["codec"])] = r
    return rows

def agg(rows):
    by = defaultdict(list)
    for (f, c), r in rows.items():
        by[c].append(r)
    out = {}
    for c, rs in by.items():
        n = sum(r["bytes"] for r in rs)
        s = sum(r["size"] for r in rs)
        cs = sum(r["comp_s"] for r in rs)
        ds = sum(r["decomp_s"] for r in rs)
        out[c] = dict(n=n, size=s, ratio=n / s, c=n / 1e6 / cs, d=n / 1e6 / ds,
                      ram=max(max(r["comp_rss_MB"], r["decomp_rss_MB"]) for r in rs),
                      cs=cs, files=len(rs))
    return out

def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    rows = load(path)
    A = agg(rows)
    nfiles = len({f for f, c in rows})
    n = next(iter(A.values()))["n"]

    plt.rcParams.update({
        "figure.facecolor": "white", "axes.grid": True, "grid.color": "#e5e7eb",
        "axes.spines.top": False, "axes.spines.right": False, "font.size": 10.5,
        "axes.titleweight": "bold", "axes.titlesize": 14,
    })
    fig, ax = plt.subplots(figsize=(9.2, 5.6))
    for c, a in A.items():
        if c not in STYLE or a["files"] < nfiles:
            continue
        lab, color, mk, sz = STYLE[c]
        ax.scatter([a["cs"]], [a["size"] / 1e6], s=sz, marker=mk, color=color, zorder=4 if c.startswith("qzip") else 3,
                   edgecolors="white", linewidths=0.8, label=lab)
        ax.annotate(lab, (a["cs"], a["size"] / 1e6), textcoords="offset points", xytext=OFF.get(c, (8, 6)),
                    fontsize=9, fontweight="bold" if c == "qzip-balanced" else "normal", color="#0f172a")
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:.0f}s" if v >= 10 else f"{v:g}s"))
    ax.set_xlabel("compress time for the 719 MB mix (wall, log scale) — lower-left is better")
    ax.set_ylabel("compressed size (MB)")
    ax.set_title("Everyday QZip Quick vs zstd, xz, 7z and brotli", loc="left")
    q = A["qzip-balanced"]
    fig.text(
        0.01, -0.06,
        f"14 files, {n/1e6:.0f} MB, Linux x86-64, 8 threads, 2026-11-24 America/Chicago. Every point is a byte-exact round trip. "
        f"Quick: {q['size']/1e6:.2f} MB ({q['ratio']:.2f}:1) in {q['cs']:.0f}s ({q['c']:.1f} MB/s), {q['ram']:.0f} MB RAM. "
        "Ties count as losses; dominance = strictly smaller and strictly faster.",
        fontsize=8, color="#475569",
    )
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"everyday_pareto.{ext}", dpi=170 if ext == "png" else None, bbox_inches="tight")
    print("wrote", OUT / "everyday_pareto.png", OUT / "everyday_pareto.svg")

if __name__ == "__main__":
    main()
