#!/usr/bin/env python3
"""README medical chart: total compressed size vs total compress time over the 20 finished 71.4 MB NIfTI volumes
(the 35.7 MB segmentation volume is left out of the totals and listed separately in the README).

Every number is a measured, sha256 byte-exact round trip on PC2 (i9-14900KF, shared, nice 19 + ionice -c3), 21 Nov 2026
(baselines) and 21-23 Nov 2026 (qzm). Sources: src/qzcore/RESULTS.md (qzm) and the medical benchmark's
out/results.csv baseline rows (gzip -9, xz -9, zstd -19 --long=27, bzip2 -9, brotli -11).
Run: python3 docs/img/make_medical_chart.py  (needs matplotlib)."""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mt

OUT = Path(__file__).resolve().parent
# volume: ((qzm bytes, qzm compress s, qzm peak RSS MB), {codec: (bytes, compress s)})
DATA = {
    '100001_flair_subtraction': ((1856589, 113.72, 2174), {'xz-9': (2217244, 8.957), 'zstd-19-long': (2664872, 5.278), 'gzip-9': (4914532, 8.778), 'bzip2-9': (2535543, 2.112), 'brotli-11': (2463212, 14.764)}),
    '100001_t1ce_subtraction': ((2381306, 87.45, 2175), {'xz-9': (2754616, 7.71), 'zstd-19-long': (3118399, 5.487), 'gzip-9': (5498340, 8.145), 'bzip2-9': (3059023, 1.925), 'brotli-11': (2921289, 16.627)}),
    '100001_time1_flair': ((1483123, 82.3, 2175), {'xz-9': (1980384, 7.676), 'zstd-19-long': (2433399, 5.206), 'gzip-9': (4335634, 7.826), 'bzip2-9': (2088065, 1.879), 'brotli-11': (2061351, 13.825)}),
    '100001_time1_t1ce-t1': ((2185190, 94.56, 2175), {'xz-9': (2606912, 7.474), 'zstd-19-long': (2824433, 5.361), 'gzip-9': (5409350, 7.942), 'bzip2-9': (2889605, 1.899), 'brotli-11': (2697268, 13.678)}),
    '100001_time2_t1ce': ((1840340, 137.93, 8420), {'xz-9': (2228328, 7.221), 'zstd-19-long': (2360794, 5.032), 'gzip-9': (4431242, 7.926), 'bzip2-9': (2264784, 2.418), 'brotli-11': (2233318, 14.103)}),
    '100001_time2_t2': ((1714115, 103.22, 2175), {'xz-9': (2249568, 7.132), 'zstd-19-long': (2395700, 5.001), 'gzip-9': (4566642, 8.055), 'bzip2-9': (2300506, 2.239), 'brotli-11': (2261982, 14.033)}),
    '100002_t1ce_subtraction': ((10780836, 331.94, 8434), {'xz-9': (11551320, 4.226), 'zstd-19-long': (11692739, 2.95), 'gzip-9': (11911478, 0.71), 'bzip2-9': (11872152, 2.733), 'brotli-11': (11504791, 28.512)}),
    '100002_time2_t1ce': ((2065832, 290.81, 8421), {'xz-9': (2479932, 11.656), 'zstd-19-long': (2740932, 8.14), 'gzip-9': (4658998, 7.972), 'bzip2-9': (2629318, 3.151), 'brotli-11': (2509875, 18.133)}),
    '100002_time2_t2': ((1900237, 187.24, 8420), {'xz-9': (2383864, 12.444), 'zstd-19-long': (2586909, 11.115), 'gzip-9': (4587456, 7.659), 'bzip2-9': (2478836, 4.814), 'brotli-11': (2436227, 25.11)}),
    '100003_flair_subtraction': ((1858801, 81.5, 2175), {'xz-9': (2391220, 18.798), 'zstd-19-long': (2849947, 13.805), 'gzip-9': (4371786, 10.13), 'bzip2-9': (2300569, 2.213), 'brotli-11': (2587355, 30.871)}),
    '100004_flair_subtraction': ((1998322, 75.12, 2175), {'xz-9': (2581068, 21.396), 'zstd-19-long': (3069470, 13.067), 'gzip-9': (4547702, 10.953), 'bzip2-9': (2392439, 2.466), 'brotli-11': (2761705, 23.614)}),
    '100005_time2_t1ce-t1': ((1975088, 87.48, 2175), {'xz-9': (2467872, 11.583), 'zstd-19-long': (2724644, 7.935), 'gzip-9': (4596189, 8.903), 'bzip2-9': (2418446, 3.667), 'brotli-11': (2552452, 19.241)}),
    '100008_time1_flair': ((1707280, 105.03, 2176), {'xz-9': (2481264, 15.991), 'zstd-19-long': (2763292, 13.552), 'gzip-9': (4341881, 7.041), 'bzip2-9': (2202986, 1.67), 'brotli-11': (2641414, 22.876)}),
    '100011_time2_t1ce-t1': ((2515885, 130.67, 2177), {'xz-9': (3210832, 20.178), 'zstd-19-long': (3545276, 14.657), 'gzip-9': (5583867, 10.86), 'bzip2-9': (3052335, 2.045), 'brotli-11': (3340911, 34.292)}),
    '100013_time1_t1': ((4464881, 294.34, 8426), {'xz-9': (5381832, 15.603), 'zstd-19-long': (5812046, 10.882), 'gzip-9': (6645923, 6.215), 'bzip2-9': (5106422, 2.17), 'brotli-11': (5348687, 25.937)}),
    '100015_time2_t1ce-t1': ((2954694, 263.99, 8422), {'xz-9': (3001840, 10.47), 'zstd-19-long': (3191891, 7.975), 'gzip-9': (7677062, 7.328), 'bzip2-9': (4207348, 2.849), 'brotli-11': (3100113, 19.162)}),
    '100018_flair_subtraction': ((8386641, 234.27, 8432), {'xz-9': (9035780, 4.597), 'zstd-19-long': (9325358, 2.751), 'gzip-9': (9535763, 0.727), 'bzip2-9': (9270896, 2.717), 'brotli-11': (9026439, 29.669)}),
    '100018_time2_t2': ((1247441, 72.2, 2175), {'xz-9': (1845312, 8.289), 'zstd-19-long': (2016084, 6.655), 'gzip-9': (3297168, 6.271), 'bzip2-9': (1643135, 2.745), 'brotli-11': (1905283, 18.255)}),
    '100020_time1_t1': ((1981159, 95.87, 2175), {'xz-9': (2882124, 13.633), 'zstd-19-long': (3167527, 10.777), 'gzip-9': (5119897, 9.831), 'bzip2-9': (2609564, 1.723), 'brotli-11': (2976629, 19.892)}),
    '100023_time2_t1': ((1343846, 104.05, 2175), {'xz-9': (1899304, 9.358), 'zstd-19-long': (2182847, 7.985), 'gzip-9': (3579576, 6.673), 'bzip2-9': (1736263, 3.998), 'brotli-11': (2026792, 18.528)}),
    '100026_time2_seg': ((5457, 20.86, 1132), {'xz-9': (13108, 0.578), 'zstd-19-long': (11648, 0.17), 'gzip-9': (43000, 0.217), 'bzip2-9': (4567, 0.269), 'brotli-11': (8751, 1.185)}),
}

LABEL = {"qzm": "QZip (qzm)", "xz-9": "xz -9", "zstd-19-long": "zstd -19 --long", "gzip-9": "gzip -9",
         "bzip2-9": "bzip2 -9", "brotli-11": "brotli -11"}
COLOR = {"qzm": "#059669", "xz-9": "#dc2626", "zstd-19-long": "#ea580c", "gzip-9": "#64748b", "bzip2-9": "#a16207",
         "brotli-11": "#7c3aed"}
OFF = {"qzm": (-12, 10, "right"), "xz-9": (0, -18, "center"), "zstd-19-long": (-8, 8, "right"),
       "gzip-9": (10, -4, "left"), "bzip2-9": (0, -18, "center"), "brotli-11": (8, 8, "left")}


def totals():
    vols = [v for k, v in DATA.items() if not k.endswith("_seg")]
    t = {"qzm": [sum(q[0] for q, _ in vols), sum(q[1] for q, _ in vols)]}
    for codec in LABEL:
        if codec != "qzm":
            t[codec] = [sum(b[codec][0] for _, b in vols), sum(b[codec][1] for _, b in vols)]
    return len(vols), t


def main():
    n, t = totals()
    plt.rcParams.update({"figure.facecolor": "white", "axes.grid": True, "grid.color": "#e5e7eb",
                         "axes.spines.top": False, "axes.spines.right": False, "font.size": 10.5,
                         "axes.titleweight": "bold", "axes.titlesize": 13})
    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    for codec, (size, sec) in t.items():
        big = codec == "qzm"
        ax.scatter([sec], [size / 1e6], s=320 if big else 90, marker="*" if big else "o", color=COLOR[codec],
                   edgecolors="white", linewidths=0.8, zorder=3)
        ax.annotate(f"{LABEL[codec]}  {size / 1e6:.1f} MB", (sec, size / 1e6), textcoords="offset points",
                    xytext=OFF[codec][:2], fontsize=9.5, fontweight="bold" if big else "normal", color="#0f172a",
                    ha=OFF[codec][2])
    ax.set_xscale("log")
    ax.xaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"{v:g} s"))
    ax.set_xlim(20, 9000)
    ax.set_ylim(0, 120)
    ax.set_xlabel(f"total compress time for the {n} volumes (wall, log scale)")
    ax.set_ylabel("total compressed size (MB)")
    ax.set_title(f"Brain MRI (NIfTI), {n} volumes / 1,428 MB: size vs compress time", loc="left")
    fig.text(0.01, -0.05, "PC2 · i9-14900KF · shared machine, nice 19 · every volume a sha256 byte-exact round trip · "
             "sizes include headers.\nThe segmentation volume (35.7 MB) is excluded; see the README table. "
             "Sources: src/qzcore/RESULTS.md, medical benchmark results.csv.", fontsize=8, color="#475569")
    for ext in ("png", "svg"):
        fig.savefig(OUT / f"medical_size_vs_time.{ext}", dpi=170 if ext == "png" else None, bbox_inches="tight")
    plt.close(fig)
    print("wrote medical_size_vs_time.png / .svg")


if __name__ == "__main__":
    main()
