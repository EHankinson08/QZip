#!/usr/bin/env python3
"""README charts from measured qzcore / reference numbers only.

Every plotted point is a byte-exact sha256 round trip with a measured
size, wall-clock compress time, and (when recorded) peak RSS.
Pending codecs are annotated, never given invented coordinates.
Times are wall seconds on a loaded PC2 (upper bounds). MB = 10^6 B.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker
from matplotlib.lines import Line2D

OUT = Path(__file__).resolve().parent

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.grid": True,
        "grid.color": "#e2e8f0",
        "grid.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.edgecolor": "#64748b",
        "axes.labelcolor": "#0f172a",
        "xtick.color": "#334155",
        "ytick.color": "#334155",
        "font.size": 10,
        "axes.titlesize": 13,
        "axes.titleweight": "semibold",
        "axes.labelsize": 11,
        "legend.frameon": False,
        "figure.dpi": 140,
    }
)

# codec -> (label, color, marker)
STYLE = {
    "qzc": ("qzc (max)", "#1d4ed8", "o"),
    "qzc2": ("qzc2 prose default (x18, 1 thread)", "#1d4ed8", "o"),
    "qzmf": ("QZip-mcm fork -q11 (default engine, GPL)", "#059669", "*"),
    "modal": ("Modal 4-vCPU container (other CPU, not comparable)", "#cbd5e1", "o"),
    "qzc2t": ("qzc2 segment threads QZ_THREADS=T (fast modes)", "#60a5fa", "h"),
    "fast_old": ("qzb / libbsc / xz / zstd (21 Nov session)", "#94a3b8", "D"),
    "qzb": ("qzb (fast)", "#2563eb", "D"),
    "qzs": ("qzs (logs/CSV)", "#1d4ed8", "o"),
    "qzm": ("qzm (medical)", "#1d4ed8", "o"),
    "mcm": ("mcm -x11 (GPL, ref only)", "#7c3aed", "s"),
    "mcmh": ("mcm -h11 (GPL, ref only)", "#a78bfa", "s"),
    "bsc": ("libbsc -b1024e2", "#0d9488", "^"),
    "xz": ("xz -9e / xz -9", "#dc2626", "v"),
    "zstd": ("zstd -19 --long", "#ea580c", "P"),
    "qmed": ("QMED (bsc/xz/zstd backend)", "#7c3aed", "s"),
    "oct": ("Oct-2025 web path (shipped stored_xz)", "#64748b", "X"),
    "prior_fast": ("prior QZip fast (qwrt+libbsc)", "#38bdf8", "D"),
    "prior_max": ("prior QZip max (stock mcm -h11 x4)", "#c084fc", "s"),
}


def save(fig, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    fig.savefig(dest, dpi=160, bbox_inches="tight")
    fig.savefig(dest.with_suffix(".svg"), bbox_inches="tight")
    plt.close(fig)
    print("wrote", dest, "and", dest.with_suffix(".svg"))


def scatter(ax, points, xkey, ykey, annotate=True, logx=False, logy=False):
    """points: list of dicts with keys codec, label, size, c_s, ram_mb, optional extra."""
    for p in points:
        name, color, marker = STYLE[p["codec"]]
        x, y = p[xkey], p[ykey]
        if x is None or y is None:
            continue
        ax.scatter(
            [x],
            [y],
            s=260 if p["codec"] == "qzmf" else 70,
            color=color,
            marker=marker,
            zorder=3,
            edgecolors="white",
            linewidths=0.6,
        )
        if annotate and p.get("short"):
            off = p.get("off_ram", p.get("off", (6, 4))) if xkey == "ram_mb" else p.get("off", (6, 4))
            ax.annotate(
                p["short"],
                (x, y),
                textcoords="offset points",
                xytext=off,
                fontsize=7.5,
                color="#0f172a",
            )
    if logx:
        ax.set_xscale("log")
    if logy:
        ax.set_yscale("log")


def legend_for(ax, codecs, loc="best"):
    handles = []
    for c in codecs:
        label, color, marker = STYLE[c]
        handles.append(
            Line2D(
                [0],
                [0],
                color=color,
                marker=marker,
                linestyle="None",
                markersize=8,
                label=label,
            )
        )
    ax.legend(handles=handles, loc=loc, fontsize=8)


FOOT = (
    "PC2 (i9-14900KF, shared, load 6–19), nice 19 + ionice -c3, America/Denver. "
    "Sizes include headers. Every point is sha256 byte-exact. Wall times are upper bounds."
)


FOOT8 = (
    "PC2 (i9-14900KF, shared machine, other jobs running), nice 19 + ionice -c3, one benchmark at a time, 22 Nov 2026. "
    "Sizes include headers; every point is a byte-exact round trip (cmp). Timing noise is a few %. "
    "T2/T8 measured on x16 (pre mixer-skip); grey = 21 Nov session."
)


# ---------------------------------------------------------------------------
# Measured data (bytes, compress wall s, peak RSS MB)
# ---------------------------------------------------------------------------

# enwik8: re-measured 22 Nov 2026 (PC2, one benchmark at a time, ~2-4 load from other users' jobs).
# qzc2 = src/qzcore/qzc2.cpp (x18 prose default, `qzc2 c in out auto 2 -m 12288`); T rows = QZ_THREADS=T.
# T2/T8 were measured on x16 (same model minus the mixer-skip; x16 T1 = 17,281,177) - marked "x16".
# The fast codecs come from the 21 Nov session (heavier load), shown in grey for scale only.
H28_SIZE, H28_C, H28_RAM = 17_310_999, 139.37, 5_840  # x18 q24, 22 Nov
T4_SIZE, T4_C, T4_RAM = 18_405_363, 41.29, 11_642  # x18 q24
T16_SIZE, T16_C, T16_RAM = 19_794_486, 20.36, 8_719  # x18 q24
ENWIK8 = [
    dict(codec="mcm", short="mcm -x11", size=17_543_254, c_s=39.17, ram_mb=6_148, off=(8, 6)),
    # QZip-mcm fork -q11, PC2 23 Nov (src/engine_mcmfork/bench/pc2_results.tsv); RSS = mcm's (same hash)
    dict(codec="qzmf", short="fork -q11 (default)", size=17_361_988, c_s=44.5, ram_mb=6_100, off=(8, -12)),
    dict(codec="qzc2", short="qzc2 h29 (default)", size=17_257_456, c_s=143.91, ram_mb=9_936, off=(-40, -14), off_ram=(8, -12)),
    dict(codec="qzc2", short="qzc2 h28 (low RAM)", size=H28_SIZE, c_s=H28_C, ram_mb=H28_RAM, off=(8, 6), off_ram=(8, 6)),
    dict(codec="qzc2t", short="T2 (x16)", size=17_840_509, c_s=77.99, ram_mb=11_445, off=(8, -4)),
    dict(codec="qzc2t", short="T4", size=T4_SIZE, c_s=T4_C, ram_mb=T4_RAM, off=(8, -4)),
    dict(codec="qzc2t", short="T8 (x16)", size=19_071_890, c_s=30.61, ram_mb=12_034, off=(8, -4)),
    dict(codec="qzc2t", short="T16", size=T16_SIZE, c_s=T16_C, ram_mb=T16_RAM, off=(8, -4)),
    dict(codec="fast_old", short="qzb", size=20_660_391, c_s=4.14, ram_mb=454, off=(-8, 10)),
    dict(codec="fast_old", short="libbsc", size=20_803_016, c_s=3.99, ram_mb=481, off=(8, -12)),
    dict(codec="fast_old", short="xz -9e", size=24_831_648, c_s=169.68, ram_mb=670),
    dict(codec="fast_old", short="zstd -19", size=26_504_540, c_s=128.51, ram_mb=209),
]

ENWIK9_DONE = [
    dict(codec="qzb", short="qzb", size=162_445_705, c_s=21.97, ram_mb=4_532, off=(8, 8)),
    dict(codec="bsc", short="libbsc", size=164_205_798, c_s=17.28, ram_mb=4_565, off=(-44, -12)),
    dict(
        codec="prior_fast",
        short="prior fast",
        size=162_473_695,
        c_s=126.7,
        ram_mb=6_816,
        off=(8, 8),
    ),
    dict(
        codec="prior_max",
        short="prior max P4-h11",
        size=146_200_704,
        c_s=235.7,
        ram_mb=24_240,  # 4 x 6,060 MB concurrent
        off=(6, 6),
    ),
    dict(
        codec="oct",
        short="Oct-2025 shipped xz",
        size=213_370_900,
        c_s=4390.7,  # whole web path: engine + verify + xz
        ram_mb=38_711,
        off=(6, 6),
    ),
]
ENWIK9_DONE += [
    # 23 Nov 2026, PC2, nice 19 + ionice -c3, one job at a time, byte-exact
    dict(codec="qzmf", short="fork -q11 (default)", size=136_854_237, c_s=373.4, ram_mb=6_200, off=(8, -12)),
    dict(codec="qzc2", short="x18 (--max)", size=137_076_090, c_s=1296.2, ram_mb=11_968, off=(-14, -16), off_ram=(8, 6)),
    dict(codec="mcm", short="mcm -x11", size=138_569_352, c_s=331.4, ram_mb=6_171, off=(-50, 8)),
    # Modal 4-vCPU container: a different, slower CPU; shown for the record only
    dict(codec="modal", short="x18 on Modal", size=137_076_090, c_s=2746.5, ram_mb=11_976, off=(6, -12), off_ram=(6, 8)),
    dict(codec="modal", short="mcm on Modal", size=138_569_352, c_s=639.3, ram_mb=6_177, off=(6, 6), off_ram=(-60, -12)),
]
ENWIK9_PENDING = ["xz -9e", "zstd -19 --long"]


# logs / CSVs: (file, orig, codec, size, c_s, ram_mb)
# qzs c-time includes the self-check decode of the structured container.
LOGCSV = [
    # Apache
    ("Apache.log", 5_135_876, "qzs", 71_316, 24.1, 612),  # whole-file qzc L3; wall from RESULTS
    # HealthApp — qzs only; refs pending
    ("HealthApp.log", 23_529_931, "qzs", 468_865, 138.93, 2_183),
    # SSH
    ("SSH.log", 73_417_506, "qzs", 716_603, 313.38, 8_424),
    # Android
    ("Android.log", 192_270_829, "qzs", 3_003_705, 433.60, 16_843),
    ("Android.log", 192_270_829, "mcm", 3_081_546, 87.44, 5_966),
    ("Android.log", 192_270_829, "bsc", 7_182_224, 4.06, 615),
    ("Android.log", 192_270_829, "xz", 7_093_892, 377.22, 671),
    ("Android.log", 192_270_829, "zstd", 7_997_164, 92.55, 278),
    # fndds / hctsa — qzs only; refs pending
    ("fndds.csv", 24_057_371, "qzs", 230_391, 127.22, 2_185),
    ("hctsa.csv", 33_059_330, "qzs", 7_107_262, 169.29, 2_208),
    # youth
    ("youth.csv", 55_547_651, "qzs", 1_408_094, 138.78, 4_294),
    ("youth.csv", 55_547_651, "mcm", 1_798_464, 9.19, 3_255),
    ("youth.csv", 55_547_651, "bsc", 3_103_426, 0.56, 239),
    ("youth.csv", 55_547_651, "xz", 3_482_548, 37.41, 484),
    ("youth.csv", 55_547_651, "zstd", 3_743_251, 20.39, 144),
    # TRI
    ("TRI_By_Name.csv", 68_403_261, "qzs", 4_775_793, 204.44, 8_419),
    ("TRI_By_Name.csv", 68_403_261, "mcm", 5_418_813, 33.16, 5_600),
    ("TRI_By_Name.csv", 68_403_261, "bsc", 6_655_472, 1.60, 321),
    ("TRI_By_Name.csv", 68_403_261, "xz", 7_182_196, 70.87, 614),
    ("TRI_By_Name.csv", 68_403_261, "zstd", 7_618_709, 51.77, 163),
    # Panel
    ("Panel_2000_2022.csv", 58_192_381, "qzs", 8_230_962, 296.74, 4_313),
    ("Panel_2000_2022.csv", 58_192_381, "mcm", 10_522_226, 33.94, 6_267),
    ("Panel_2000_2022.csv", 58_192_381, "bsc", 11_317_634, 1.07, 230),
    ("Panel_2000_2022.csv", 58_192_381, "xz", 12_846_924, 195.83, 502),
    ("Panel_2000_2022.csv", 58_192_381, "zstd", 13_691_107, 68.03, 154),
    # products2m — qzs + bsc done; mcm/xz/zstd pending at chart time
    ("products2m.csv", 326_108_495, "qzs", 47_643_116, 386.97, 8_437),
    ("products2m.csv", 326_108_495, "mcm", 54_780_824, 188.95, 6_137),
    ("products2m.csv", 326_108_495, "bsc", 55_599_448, 5.22, 1_614),
]
LOGCSV_PENDING = [
    "refs (bsc/mcm/xz/zstd) for Apache, HealthApp, SSH, fndds, hctsa",
    "products2m.csv xz/zstd (xz running)",
    "Data8277.csv qzs + refs (queued)",
]


def logcsv_points():
    out = []
    for fn, orig, codec, size, c_s, ram in LOGCSV:
        out.append(
            dict(
                codec=codec,
                short="",  # avoid unreadable overlap; legend + file colour not used
                file=fn,
                size=size,
                c_s=c_s,
                ram_mb=ram,
                orig=orig,
                ratio=orig / size,
            )
        )
    return out


# Medical qzm (finished). orig 71,424,352 unless noted.
# QMED pass 2 sizes + compress s (from medical/RESULTS.md) are in QMED.
QZM = [
    ("100001_flair_subtraction", 1_856_589, 113.72, 2_174),
    ("100001_t1ce_subtraction", 2_381_306, 87.45, 2_175),
    ("100001_time1_flair", 1_483_123, 82.30, 2_175),
    ("100001_time1_t1ce-t1", 2_185_190, 94.56, 2_175),
    ("100001_time2_t1ce", 1_840_340, 137.93, 8_420),
    ("100001_time2_t2", 1_714_115, 103.22, 2_175),
    ("100002_t1ce_subtraction", 10_780_836, 331.94, 8_434),
    ("100002_time2_t1ce", 2_065_832, 290.81, 8_421),
    ("100002_time2_t2", 1_900_237, 187.24, 8_420),
    ("100003_flair_subtraction", 1_858_801, 81.50, 2_175),
    ("100004_flair_subtraction", 1_998_322, 75.12, 2_175),
    ("100005_time2_t1ce-t1", 1_975_088, 87.48, 2_175),
    ("100008_time1_flair", 1_707_280, 105.03, 2_176),
    ("100011_time2_t1ce-t1", 2_515_885, 130.67, 2_177),
    ("100013_time1_t1", 4_464_881, 294.34, 8_426),
    ("100015_time2_t1ce-t1", 2_954_694, 263.99, 8_422),
    ("100018_flair_subtraction", 8_386_641, 234.27, 8_432),
    ("100018_time2_t2", 1_247_441, 72.20, 2_175),
    ("100020_time1_t1", 1_981_159, 95.87, 2_175),
    ("100023_time2_t1", 1_343_846, 104.05, 2_175),
    ("100026_time2_seg", 5_457, 20.86, 1_132),  # orig 35,712,352
]
# QMED pass 2 (best of bsc/xz/zstd after the QMED transform). No peak RAM recorded.
QMED = {
    "100001_flair_subtraction": (2_023_970, 23.625),
    "100001_t1ce_subtraction": (2_471_142, 25.705),
    "100001_time1_flair": (1_687_424, 18.622),
    "100001_time1_t1ce-t1": (2_290_094, 20.892),
    "100001_time2_t1ce": (1_962_360, 20.912),
    "100001_time2_t2": (1_768_558, 24.164),
    "100002_t1ce_subtraction": (10_970_930, 58.699),
    "100002_time2_t1ce": (2_168_940, 23.077),
    "100002_time2_t2": (1_947_354, 25.317),
    "100003_flair_subtraction": (1_985_780, 27.246),
    "100004_flair_subtraction": (2_101_674, 26.972),
    "100005_time2_t1ce-t1": (2_021_850, 23.143),
    "100008_time1_flair": (1_910_840, 20.388),
    "100011_time2_t1ce-t1": (2_564_990, 24.157),
    "100013_time1_t1": (4_479_087, 49.493),
    "100015_time2_t1ce-t1": (3_078_018, 30.6),
    "100018_flair_subtraction": (8_587_706, 55.5),
    "100018_time2_t2": (1_364_114, 25.927),
    "100020_time1_t1": (2_160_206, 29.248),
    "100023_time2_t1": (1_513_474, 25.558),
    "100026_time2_seg": (4_106, 5.802),
}
# xz-9 / zstd-19-long from the medical baseline (same originals, CPU, byte-exact).
# Only volumes that already have a qzm result are plotted, so the comparison is paired.
XZ_MED = {
    "100001_flair_subtraction": (2_217_244, 8.957),
    "100001_t1ce_subtraction": (2_754_616, 7.71),
    "100001_time1_flair": (1_979_608, None),  # filled below if present; placeholder
}
MED_PENDING = [
    "100026_time2_t1ce (compress running)",
    "100031_time2_flair",
    "100038_time2_t1ce-t1",
    "100296_flair_subtraction",
    "100302_flair_subtraction",
]


def load_med_baselines():
    """Read xz-9 and zstd-19-long sizes/times from the earlier medical RESULTS."""
    p = Path("/workspace/compression_review/medical/RESULTS.md")
    xz, zstd = {}, {}
    if not p.exists():
        return xz, zstd

    def parse_int(s):
        return int(s.replace(",", ""))

    def parse_f(s):
        return float(s)

    for line in p.read_text().splitlines():
        if not line.startswith("| 100"):
            continue
        parts = [x.strip() for x in line.strip("|").split("|")]
        if len(parts) < 7:
            continue
        # artifact table uses .nii.qc.gz in col 0; skip
        name = parts[0].replace(".nii", "")
        if "qc" in parts[0]:
            continue
        codec = parts[2] if len(parts) > 2 else ""
        try:
            size = parse_int(parts[3])
            cs = parse_f(parts[5])
        except ValueError:
            continue
        if codec == "xz-9":
            xz[name] = (size, cs)
        elif codec == "zstd-19-long":
            zstd[name] = (size, cs)
    return xz, zstd


def pending_box(ax, lines, loc="lower right"):
    text = "Pending (not plotted):\n" + "\n".join(f"• {x}" for x in lines)
    x = 0.98 if "right" in loc else 0.02
    if "center" in loc and "right" in loc or "center" in loc and "left" in loc:
        y, va = 0.52, "center"
    elif "lower" in loc:
        y, va = 0.02, "bottom"
    else:
        y, va = 0.98, "top"
    ax.text(
        x,
        y,
        text,
        transform=ax.transAxes,
        ha="right" if "right" in loc else "left",
        va=va,
        fontsize=7.5,
        color="#334155",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#f8fafc", edgecolor="#cbd5e1"),
    )


def main():
    # ----- enwik8 size vs time -----
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    scatter(ax, ENWIK8, "c_s", "size")
    legend_for(ax, ["qzmf", "mcm", "qzc2", "qzc2t", "fast_old"], loc="upper left")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v/1e6:.0f} M"))
    ax.set_xlabel("Compress wall time (s, log scale)")
    ax.set_ylabel("Compressed size (MB = 10^6 B)")
    ax.set_title("enwik8 (100,000,000 B) — size vs compress time")
    ax.set_xscale("log")
    ax.set_ylim(16_500_000, 28_000_000)
    fig.text(0.01, 0.01, FOOT8, fontsize=7, color="#475569", wrap=True, transform=fig.transFigure)
    save(fig, OUT / "enwik8_size_vs_time.png")

    # ----- enwik8 RAM bars -----
    pts = sorted(ENWIK8, key=lambda p: p["size"])
    fig, ax = plt.subplots(figsize=(8.6, 5.6))
    ys = list(range(len(pts)))[::-1]
    for y, p in zip(ys, pts):
        _, color, _ = STYLE[p["codec"]]
        ax.barh(y, p["ram_mb"] / 1000, color=color, height=0.65, zorder=3)
        ax.text(p["ram_mb"] / 1000 + 0.15, y, f"{p['ram_mb']/1000:.1f} GB  ·  {p['size']:,} B", va="center", fontsize=7.5, color="#0f172a")
    ax.set_yticks(ys)
    ax.set_yticklabels([p["short"] for p in pts], fontsize=8)
    ax.set_xlabel("Peak RSS, compress (GB = 10^9 B)")
    ax.set_title("enwik8 — peak RAM per setting (sorted by output size, smallest at top)")
    ax.set_xlim(0, 17)
    ax.grid(axis="y", visible=False)
    legend_for(ax, ["qzmf", "mcm", "qzc2", "qzc2t", "fast_old"], loc="lower right")
    fig.text(0.01, 0.01, FOOT8, fontsize=7, color="#475569", wrap=True, transform=fig.transFigure)
    save(fig, OUT / "enwik8_size_vs_ram.png")

    # ----- enwik9 -----
    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    scatter(ax, ENWIK9_DONE, "c_s", "size")
    legend_for(ax, ["qzmf", "qzc2", "mcm", "modal", "qzb", "bsc", "prior_fast", "prior_max", "oct"], loc="upper left")
    pending_box(ax, ENWIK9_PENDING, "center right")
    ax.set_xlabel("Compress wall time (s, log scale)")
    ax.set_ylabel("Compressed size (bytes)")
    ax.set_title("enwik9 (1,000,000,000 B) — size vs compress time")
    ax.set_xscale("log")
    fig.text(
        0.01,
        0.005,
        FOOT
        + " Oct-2025 time is the whole web path (engine + verify + xz), not xz alone. "
        "Grey 'Modal' points ran on a different, slower CPU (same bytes); compare times on PC2 only. "
        "prior max RAM is 4 concurrent mcm processes (~24 GB).",
        fontsize=7,
        color="#475569",
        wrap=True,
        transform=fig.transFigure,
    )
    save(fig, OUT / "enwik9_size_vs_time.png")

    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    scatter(ax, [p for p in ENWIK9_DONE if p["codec"] != "modal"], "ram_mb", "size")   # Modal RAM = PC2 RAM
    legend_for(ax, ["qzmf", "qzc2", "mcm", "qzb", "bsc", "prior_fast", "prior_max", "oct"], loc="upper left")
    pending_box(ax, ENWIK9_PENDING, "center right")
    ax.set_xlabel("Peak RSS (MB)")
    ax.set_ylabel("Compressed size (bytes)")
    ax.set_title("enwik9 (1,000,000,000 B) — size vs peak RAM")
    fig.text(0.01, 0.01, FOOT + " Modal runs not shown (same RAM as PC2).", fontsize=7, color="#475569", wrap=True, transform=fig.transFigure)
    save(fig, OUT / "enwik9_size_vs_ram.png")

    # ----- logs/CSVs: compressed size vs time (log-log), colour by codec -----
    pts = logcsv_points()
    fig, ax = plt.subplots(figsize=(9.2, 6.0))
    scatter(ax, pts, "c_s", "size", annotate=False, logx=True, logy=True)
    # label only qzs points (our results) with the file name
    for p in pts:
        if p["codec"] == "qzs":
            ax.annotate(
                p["file"].replace(".csv", "").replace(".log", ""),
                (p["c_s"], p["size"]),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=7,
                color="#1e3a8a",
            )
    legend_for(ax, ["qzs", "mcm", "bsc", "xz", "zstd"])
    pending_box(ax, LOGCSV_PENDING, "lower left")
    ax.set_xlabel("Compress wall time (s, log scale)")
    ax.set_ylabel("Compressed size (bytes, log scale)")
    ax.set_title("Logs and CSVs — size vs compress time")
    fig.text(
        0.01,
        0.005,
        FOOT + " qzs compress time includes a self-check decode of the structured container.",
        fontsize=7,
        color="#475569",
        transform=fig.transFigure,
    )
    save(fig, OUT / "logs_csv_size_vs_time.png")

    fig, ax = plt.subplots(figsize=(9.2, 6.0))
    scatter(ax, pts, "ram_mb", "size", annotate=False, logx=True, logy=True)
    for p in pts:
        if p["codec"] == "qzs":
            ax.annotate(
                p["file"].replace(".csv", "").replace(".log", ""),
                (p["ram_mb"], p["size"]),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=7,
                color="#1e3a8a",
            )
    legend_for(ax, ["qzs", "mcm", "bsc", "xz", "zstd"])
    pending_box(ax, LOGCSV_PENDING, "lower left")
    ax.set_xlabel("Peak RSS (MB, log scale)")
    ax.set_ylabel("Compressed size (bytes, log scale)")
    ax.set_title("Logs and CSVs — size vs peak RAM")
    fig.text(0.01, 0.01, FOOT, fontsize=7, color="#475569", transform=fig.transFigure)
    save(fig, OUT / "logs_csv_size_vs_ram.png")

    # ----- medical -----
    xz, zstd = load_med_baselines()
    med_pts = []
    for name, size, c_s, ram in QZM:
        med_pts.append(dict(codec="qzm", short="", size=size, c_s=c_s, ram_mb=ram, file=name))
        if name in QMED:
            qs, qt = QMED[name]
            med_pts.append(dict(codec="qmed", short="", size=qs, c_s=qt, ram_mb=None, file=name))
        if name in xz:
            med_pts.append(dict(codec="xz", short="", size=xz[name][0], c_s=xz[name][1], ram_mb=None, file=name))
        if name in zstd:
            med_pts.append(
                dict(codec="zstd", short="", size=zstd[name][0], c_s=zstd[name][1], ram_mb=None, file=name)
            )

    fig, ax = plt.subplots(figsize=(9.2, 6.0))
    scatter(ax, med_pts, "c_s", "size", annotate=False, logx=True, logy=True)
    # label the largest / most interesting qzm points only
    label_names = {
        "100002_t1ce_subtraction",
        "100018_flair_subtraction",
        "100013_time1_t1",
        "100018_time2_t2",
        "100001_time1_flair",
        "100026_time2_seg",
        "100023_time2_t1",
    }
    for p in med_pts:
        if p["codec"] == "qzm" and p["file"] in label_names:
            ax.annotate(
                p["file"].replace("10000", "").replace("1000", ""),
                (p["c_s"], p["size"]),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=7,
                color="#1e3a8a",
            )
    legend_for(ax, ["qzm", "qmed", "xz", "zstd"])
    pending_box(ax, MED_PENDING, "center right")
    ax.set_xlabel("Compress wall time (s, log scale)")
    ax.set_ylabel("Compressed size (bytes, log scale)")
    ax.set_title("Medical NIfTI — size vs compress time")
    fig.text(
        0.01,
        0.005,
        FOOT + " Most volumes are 71,424,352 B; 100026_time2_seg is 35,712,352 B. QMED / xz / zstd times are from the earlier paired medical benchmark on the same originals. "
        "QMED has no peak-RAM record, so it is omitted from the RAM chart.",
        fontsize=7,
        color="#475569",
        transform=fig.transFigure,
    )
    save(fig, OUT / "medical_size_vs_time.png")

    fig, ax = plt.subplots(figsize=(8.6, 5.4))
    qzm_only = [p for p in med_pts if p["codec"] == "qzm"]
    scatter(ax, qzm_only, "ram_mb", "size", annotate=False, logy=True)
    for p in qzm_only:
        if p["file"] in label_names:
            ax.annotate(
                p["file"].replace("10000", "").replace("1000", ""),
                (p["ram_mb"], p["size"]),
                textcoords="offset points",
                xytext=(6, 4),
                fontsize=7,
                color="#1e3a8a",
            )
    legend_for(ax, ["qzm"])
    pending_box(ax, MED_PENDING + ["QMED/xz/zstd peak RAM not measured"], "lower right")
    ax.set_xlabel("Peak RSS (MB)")
    ax.set_ylabel("Compressed size (bytes)")
    ax.set_title("Medical NIfTI — qzm size vs peak RAM")
    fig.text(0.01, 0.01, FOOT, fontsize=7, color="#475569", transform=fig.transFigure)
    save(fig, OUT / "medical_size_vs_ram.png")


if __name__ == "__main__":
    main()
