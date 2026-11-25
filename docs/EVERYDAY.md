# Everyday QZip (Quick / Fast / Deep)

Recommended go-to compressor: **`qzip compress`** or **`--quick`** (`qze -2`, the Quick default).
Speed-first alternative: **`qzip compress --fast`** (`qze -1`). Classic: **`--classic`**. Deep: **`--deep`** (`qze -3` until the looped transformer lands).

Quick (`qze -2`) and Deep (`qze -3`) are the qze tiers: block-parallel zstd (fast) or libbsc BWT + static QLFC / zstd, with a CSV column transform when a delimiter is detected. Quick also routes SQLite through a page-type grouping transform plus LZMA, and uses LZMA (vendored LZMA SDK 26.06, public domain, Igor Pavlov) on small binaries, PDFs, tarballs, and logs where a sample says it wins. Solid lzma-9 is size-capped at 32 MB so the 719 MB mix stays ≥20 MB/s. Classic (`--classic`) is the launch mcm-fork router. Format is QZE1 (backend 3 = LZMA, transform 2 = SQLite pages). Streams are identical across Linux x86-64, Linux arm64, and Windows x86-64 (same `T` and flags; block split can change with `-T` unless `-b` is pinned).

Chart: [docs/img/everyday_pareto.png](img/everyday_pareto.png) (size vs compress time for the mix). Raw rows: [docs/everyday_pareto.jsonl](everyday_pareto.jsonl).

## Pareto (14 files, 719 MB, 8 threads)

Measured on Linux x86-64 (this development box) on 2026-11-24, America/Chicago. Every row is a byte-exact round trip. Throughput is input MB (10^6 B) / wall seconds. RAM is peak RSS. Ties count as losses. A codec *dominates* another when it is strictly smaller and strictly faster.

Corpus: enwik8; Silesia `mozilla` and `samba`; mixed real-world set (3 CSVs, 3 Loghub logs, GitHub events JSON, CPython 3.12.7 tarball, a Python stdlib mix tarball, Northwind SQLite, a PDF). Full Silesia is in the tree; the other 10 Silesia files were not added to this table.

| codec | ratio | size (MB) | compress MB/s | decompress MB/s | peak RAM (MB) | Pareto |
|---|---:|---:|---:|---:|---:|:-:|
| **qzip-balanced / Quick (`qze -2`)** | **6.88** | **104.40** | **21.0** | **126.5** | **562** | **yes** |
| qzip-max (`qze -3`) | 6.71 | 107.13 | 19.2 | 147.6 | 637 | yes |
| 7z -9 | 6.52 | 110.22 | 3.1 | 143.7 | 1048 |  |
| xz -9 | 6.51 | 110.39 | 2.0 | 146.2 | 677 |  |
| brotli -11 | 6.49 | 110.70 | 0.6 | 380.5 | 244 | yes |
| xz -6 | 6.33 | 113.47 | 2.6 | 167.9 | 97 |  |
| xz -6 -T | 6.29 | 114.31 | 6.4 | 301.6 | 557 | yes |
| 7z -5 | 6.27 | 114.61 | 4.0 | 144.4 | 384 |  |
| zstd -19 -T | 6.07 | 118.48 | 3.4 | 877.6 | 450 | yes |
| brotli -9 | 5.60 | 128.44 | 7.2 | 464.9 | 116 | yes |
| brotli -5 | 5.24 | 137.28 | 45.4 | 443.1 | 94 | yes |
| **qzip-fast (`qze -1`)** | **4.98** | **144.46** | **338.4** | **985.9** | **194** | **yes** |
| zstd -3 | 4.51 | 159.30 | 278.9 | 1016.6 | 43 |  |
| zstd -3 -T | 4.51 | 159.30 | 764.6 | 1029.7 | 126 | yes |
| lz4 | 2.84 | 253.44 | 827.1 | 1220.3 | 46 | yes |

On the mix, Quick is strictly smaller and strictly faster to compress than xz -9, 7z -9, zstd -19 -T and `qze -3`. brotli -11 is still 6.3 MB larger and far slower to compress (0.6 vs 21.0 MB/s) but decompresses faster (380 vs 126 MB/s), so it stays on the Pareto front. The mix holds 21.0 MB/s, above the 20 MB/s floor. Fast is strictly smaller than zstd -3 -T on the mix (144.46 vs 159.30 MB) but not strictly faster (338 vs 765 MB/s).

### Thread scaling (1 / 4 / 8)

Block split in Quick/Deep can change with `-T` (sizes below are not identical across thread counts). Goldens pin `-T 2 -b 2` so streams stay fixed. T=8 is the default table. T=4 includes 7z and xz -6 -T; T=1 is qzip + zstd only.

| codec | T=1 MB/s (c / d) | T=4 | T=8 | T=8 size (MB) |
|---|---:|---:|---:|---:|
| Quick (`qze -2`) | 13.5 / 65.9 | 21.0 / 142.2 | 21.0 / 126.5 | 104.40 |
| Deep (`qze -3`) | 8.1 / 52.0 | 17.9 / 123.5 | 19.2 / 147.6 | 107.13 |
| Fast (`qze -1`) | 112.3 / 436.1 | 274.9 / 747.9 | 338.4 / 985.9 | 144.46 |
| zstd -3 -T | 295.4 / 1043.2 | 636.1 / 972.6 | 764.6 / 1029.7 | 159.30 |
| zstd -19 -T | 2.1 / 843.6 | 3.4 / 841.6 | 3.4 / 877.6 | 118.48 |

### Per-file dominance (Quick vs xz -9 / 7z -9 / brotli -11 / zstd -19 -T)

Success is *strict* Pareto dominance: smaller **and** faster than each comparable competitor. Ties are losses.

Quick **dominates all four** on 6/14 files: the three CSVs, enwik8 (22.15 MB @ 44.6 MB/s, 808 KB smaller than the previous Quick), openstack.log (1.97 MB @ 60.9 MB/s), and northwind.db (SQLite page-type grouping + LZMA; 5.30 MB at 18.3 MB/s, 236 KB smaller than xz -9).

Trade-offs on the other eight (bytes vs the named leader, compress speed vs that same leader):

| file | Quick | vs best high-ratio result | notes |
|---|---|---|---|
| ssh.log | 2.85 MB @ 94.4 MB/s | faster than brotli -11 (130×) but **+122.97 KB** | dominates xz -9 / 7z -9 / zstd -19 -T. LZP-32 + 32 MB blocks closed 119 KB of the old gap |
| healthapp.log | 1.48 MB @ 8.1 MB/s | faster than brotli -11 (~12×) but **+169.3 KB**; also **+46.4 KB** vs zstd -19 -T | dominates xz -9 / 7z -9. Already on LZMA; LZP does not apply. Solid lzma-9 is larger than current Quick and still far behind brotli |
| paper.pdf | 1.03 MB @ 6.5 MB/s | **+5.0 KB** vs brotli -11 (faster 8×); **+129 B and slower (0.44×)** vs 7z -9 | dominates xz -9 / zstd -19 -T |
| gh_events.json | 4.07 MB @ 78.3 MB/s | faster than all four, but **+12.3 KB** vs brotli -11, **+129.1 KB vs 7z -9**, **+137.0 KB vs xz -9** | LZP-32 + 32 MB blocks closed 549 KB of the old gap. Solid lzma-9 still ties xz -9 and would be slower |
| cpython-3.12.7.tar | 21.52 MB @ 136.8 MB/s | faster than all four, **+1,537 KB** vs 7z -9, **+1,475 KB** vs xz -9, **+1,241 KB** vs brotli -11 | solid lzma-9 ties xz -9, still loses to 7z -9, and would run at ~2.5 MB/s |
| mozilla | 16.35 MB @ 144.3 MB/s | faster than all four, **+3,010 KB** vs 7z -9, **+2,980 KB** vs xz -9, **+2,482 KB** vs brotli -11 | same solid-lzma story as CPython (~2.3 MB/s) |
| samba | 3.76 MB @ 3.6 MB/s | **−116 B** vs 7z -9 but **slower (0.53×)**; dominates xz -9 / brotli -11 / zstd -19 -T | fragile size win; speed is the gap |
| mixed_pystdlib.tar | 5.38 MB @ 2.7 MB/s | **−2.5 KB** vs 7z -9 but **slower (0.52×)**; **−8.6 KB** vs xz -9 but **0.99×** speed (not strictly faster) | dominates brotli -11 only |

Putting solid lzma-9 on mozilla + gh_events + CPython would drop the mix to about 6.8 MB/s, under the 20 MB/s floor. Even gh_events alone (the cheapest of the three) would drop the mix to about 16.4 MB/s. Those three stay on BWT/zstd on purpose until a *faster* path can beat 7z / brotli.

### Fast vs zstd -3 -T

Fast is strictly smaller than zstd -3 -T on 13/14 files (paper.pdf is **+15,107 B** and much slower). It is strictly faster on only mixed_pystdlib.tar (445 vs 388 MB/s). On the mix, 338 vs 765 MB/s at a better ratio (4.98 vs 4.51). The bottleneck is the single-thread baseline (T=1: 112 vs 295 MB/s), not scaling (Fast T1→T8 is 3.0×; zstd -3 -T is 2.6×).

## Recommended default and policy

- **Default everyday use: `qzip compress` / `--quick`.** On the 719 MB mix Quick is the size/speed leader against xz -9, 7z -9 and brotli -11 (104.40 MB at 21.0 MB/s, 562 MB RAM). It does not yet strictly dominate every file.
- **`--fast`** when encode speed or RAM matters more than size (better ratio than zstd -3; not yet as fast as zstd -3 -T).
- **`--deep` / `qze -3`** is currently dominated by Quick on this mix; it remains the placeholder for the looped-transformer path.

Quick LZMA policy (tier 2, skipped if the user passed `-b` for the solid/block choice only — the type route still runs):

- SQLite (`SQLite format 3`): page-type grouping + lzma-9 with an 8 MB dictionary, 8 MB blocks, T-way parallel.
- ustar / PDF / ELF / PE / ZIP at or under 32 MB: solid lzma-9.
- Other files at or under 32 MB: 4 MB sample of BWT vs zstd vs LZMA (and vs the CSV transform if a delimiter was detected). LZMA wins on pipe-delimited logs such as healthapp; a 1 MB sample was not enough there. Text uses 8 MB blocked lzma-9; other binaries use solid lzma-9.
- Larger files stay on BWT/zstd so the mix stays ≥20 MB/s.
- On that BWT/zstd path (not LZMA, and only if the user did not pass `-b`): a 16 MB prefix still picks 8 vs 16 MB blocks for CSV/text; non-CSV text larger than 32 MB uses 32 MB blocks; an 8 MB prefix picks libbsc LZP-32 only when it shrinks that prefix. A full 32 MB prefix probe of those choices dropped the mix to 14.5 MB/s and was rejected.

Levers already tried (stop after 3 zero-gain rounds):

1. Blanket `-b 16/32` or `-L 9/19`: net loss or a tiny / costly gain.
2. Type-aware larger blocks: helps CSV/JSON/prose, hurts some logs and binaries. Not a blanket rule.
3. **Adopted:** on text or CSV inputs larger than 16 MB, encode a 16 MB prefix at 8 MB and 16 MB blocks and use the winner (skipped if the user passed `-b`, so golden streams stay fixed).
4. Vendored portable LZMA + SQLite page-type grouping + size-capped solid lzma-9: **adopted.** Closed the xz -9 size gap on northwind, healthapp, samba, mixed, and paper; mix after that step was 23.3 MB/s, then 21.0 MB/s after LZP/32 MB blocks.
5. Fast: level 3 is closer in speed on enwik8 but slightly *larger* than zstd -3; level 1 + 1 MB blocks can match zstd -3 -T speed at a much worse ratio. Default stays level 5 so the mix keeps a better ratio than zstd -3.
6. **Adopted:** LZP-32 (when an 8 MB prefix agrees) and 32 MB blocks on large non-CSV text. Mix 104.40 MB at 21.0 MB/s (−1.69 MB vs the previous Quick). Forced solid lzma-9 on large files, blanket `-b`/`-L`, and a 32 MB prefix probe were zero-gain or broke the 20 MB/s floor (3-round stop).

Still open (do not land without a measured gain that also keeps the mix ≥20 MB/s; ties count as losses):

- A log / JSON transform, or libbsc LZP / adaptive QLFC, so ssh.log and healthapp.log (and gh_events) can strictly beat brotli -11. Raw LZMA cannot: solid lzma-9 is worse than current Quick on healthapp and still 141 KB behind brotli on gh_events.
- A path that strictly beats 7z -9 on mozilla, the CPython tarball, and paper.pdf (raw LZMA ties xz -9 and loses to 7z -9). Must be faster than solid lzma-9 to keep the 20 MB/s floor.
- Speed up samba / mixed (already slightly smaller than 7z -9, but ~0.5× as fast).
- Fast single-thread baseline vs zstd -3 -T, without giving back the ratio win.
- Cheaper or parallel prefix probe; a 32 MB block candidate on large text.

## Native builds

| Platform | How it is built | Status (2026-11-24) |
|---|---|---|
| Linux x86-64 | `make native` / `ARCH=x86-64-v3` | Reference. Host golden: 0 failures after adding the LZMA backend and the `tiny.sqlite` fixture. |
| Linux arm64 | `make native ARCH=portable` with `aarch64-linux-gnu-gcc`, run under `qemu-aarch64 -L /usr/aarch64-linux-gnu` | Cross-compile OK. qze-fast / qze-balanced streams (including `tiny.sqlite` and a real SQLite file, backend 3 / transform 2) identical to the x86-64 reference. Full-engine golden: qze, qzmf, x18, qzc, qzb identical; `qzs` still needs `QZ_SKIP=qzs` under qemu (harness limitation). |
| Windows x86-64 | MinGW-w64, `ARCH=portable`, static `.exe` | Cross-compile OK. Wine: qze-fast / qze-balanced identical, including SQLite. Same `qzs` harness skip. |
| macOS x86-64 / arm64 | `ARCH=portable`, sse2neon, `MACOSX_DEPLOYMENT_TARGET=11.0` | Source and `.github/workflows/native-matrix.yml` cover both (GitHub `macos-15-intel` and `macos-latest`). No Apple SDK on the Linux box, so no local cross-compile here. |
| Wheels | cibuildwheel (Linux, macOS) + pip wheel (Windows/MSYS2) | Wired in the same workflow; not built in this session. |

`python3 tests/portable/golden.py` checks byte-exact round trips and identical compressed bytes vs the Linux x86-64 reference. Cross runners: `QZ_RUNNER="qemu-aarch64 -L /usr/aarch64-linux-gnu"` or `QZ_RUNNER=wine QZ_EXE=.exe`.

Reproduce the table:

```bash
python3 bench/everyday/bench.py --threads 8 --timeout 1800 --out pareto.jsonl \
  --codecs qzip-fast,qzip-balanced,qzip-max,zstd-3,zstd-3-T,zstd-19-T,xz-6,xz-6-T,xz-9,7z-5,7z-9,brotli-5,brotli-9,brotli-11,lz4 \
  FILE...
python3 bench/everyday/pareto.py pareto.jsonl
python3 docs/img/make_everyday_pareto.py pareto.jsonl
```
