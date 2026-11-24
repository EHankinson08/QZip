# Everyday QZip (fast / balanced)

Recommended go-to compressor: **`qzip compress --balanced`** (`qze -2`).
Speed-first alternative: **`qzip compress --fast`** (`qze -1`).

These are the qze tiers: block-parallel zstd (fast) or libbsc BWT + static QLFC / zstd (balanced), with a CSV column transform when a delimiter is detected. They are not the slower mcm-fork default (`qzip compress` with no flag). Format is unchanged (`QZE1`); streams are identical across Linux x86-64, Linux arm64, and Windows x86-64.

## Pareto (14 files, 719 MB, 8 threads)

Measured on Linux x86-64 (this development box) on 2026-11-24, America/Chicago. Every row is a byte-exact round trip. Throughput is input MB (10^6 B) / wall seconds. RAM is peak RSS.

Corpus: enwik8; Silesia `mozilla` and `samba`; mixed real-world set (3 CSVs, 3 Loghub logs, GitHub events JSON, CPython 3.12.7 tarball, a Python stdlib mix tarball, Northwind SQLite, a PDF). Full Silesia is in the tree; the other 10 Silesia files were not added to this table.

| codec | ratio | size (MB) | compress MB/s | decompress MB/s | peak RAM (MB) | Pareto |
|---|---:|---:|---:|---:|---:|:-:|
| qzip-max (`qze -3`) | 6.71 | 107.13 | 17.4 | 139.5 | 637 | yes |
| **qzip-balanced (`qze -2`)** | **6.58** | **109.22** | **51.4** | **213.7** | **578** | **yes** |
| 7z -9 | 6.52 | 110.22 | 3.1 | 144.6 | 1048 |  |
| xz -9 | 6.51 | 110.39 | 1.4 | 133.3 | 677 |  |
| brotli -11 | 6.49 | 110.70 | 0.4 | 205.0 | 244 | yes |
| xz -6 | 6.33 | 113.47 | 2.5 | 150.9 | 97 |  |
| xz -6 -T | 6.29 | 114.31 | 5.4 | 259.4 | 557 | yes |
| 7z -5 | 6.27 | 114.61 | 3.3 | 112.5 | 384 |  |
| zstd -19 -T | 6.07 | 118.48 | 3.1 | 801.3 | 450 | yes |
| brotli -9 | 5.60 | 128.44 | 6.9 | 429.4 | 116 |  |
| brotli -5 | 5.24 | 137.28 | 43.2 | 415.7 | 94 | yes |
| **qzip-fast (`qze -1`)** | **4.98** | **144.46** | **359.0** | **807.6** | **193** | **yes** |
| zstd -3 | 4.51 | 159.30 | 266.3 | 842.6 | 43 |  |
| zstd -3 -T | 4.51 | 159.30 | 671.7 | 915.6 | 132 | yes |
| lz4 | 2.84 | 253.44 | 443.7 | 1053.9 | 45 | yes |

Balanced (after a 16 MB prefix probe that picks 8 MB vs 16 MB blocks on text/CSV) **beats xz -9 and brotli -11 on total size**, at about 37× xz -9 and 130× brotli -11 compress speed. It is slower than the previous 8 MB-only balanced (6.47 ratio at 129 MB/s) because the probe plus larger blocks cost time; it stays far above the x18 speed floor (~0.7 MB/s). Fast **beats zstd -3 on ratio** at similar or better single-thread compress speed; threaded zstd -3 is still faster to encode (672 vs 359 MB/s) and uses less RAM.

### Where balanced wins and loses

Strong: CSVs (youth.csv 31.9 vs xz -9 14.0 and brotli -11 15.7; TRI 11.3 vs ~9) and enwik8 (4.36 vs xz -9 4.02). Logs stay close to or ahead of xz. Weak: databases and some binaries (northwind.db 3.31 vs xz -9 4.46; mozilla / samba behind xz and 7z). `--max` (`qze -3` or `--max` / x18) is the size-first fallback there.

## Recommended default and next levers

- **Default everyday use: `--balanced`.** It is the one that can replace xz / 7z / brotli -11 as the go-to setting: better total size than those leaders here, tens of MB/s, one command.
- **`--fast`** when encode speed or RAM matters more than size (the zstd -3 replacement).
- **`--max` / `qze -3`** when size wins and ~17 MB/s is acceptable.

Levers already tried (stop after 3 zero-gain rounds; round 1 was the committed binary sample choice, B2BIN=97):

1. Blanket `-b 16/32` or `-L 9/19`: net loss or a tiny / costly gain. `-L 9` was zero-gain; `-L 19` helped some binaries but dropped to ~65 MB/s without catching xz on mozilla / northwind.
2. Type-aware larger blocks: helps CSV/JSON/prose, hurts some logs and binaries. Not safe as a blanket rule.
3. **Adopted:** on text or CSV inputs larger than 16 MB, encode a 16 MB prefix at 8 MB and 16 MB blocks and use the winner for the whole file (skipped if the user passed `-b`, so golden streams stay fixed). Prefix choice matched the full-file oracle on every file we measured. Probe is skipped on binary inputs so those stay at the old speed.

Still open (do not land without a measured gain):

- Cheaper or parallel prefix probe (text files that keep 8 MB still pay the probe).
- A 32 MB block candidate on large text (enwik8 4.51 and JSON 15.49 at `-b 32`, but slower).
- Better binary / SQLite modeling (the remaining gap vs xz -9).
- JSON-specific transform; libbsc LZP or adaptive QLFC only where a sample says it wins.

## Native builds

| Platform | How it is built | Status (2026-11-24) |
|---|---|---|
| Linux x86-64 | `make native` / `ARCH=x86-64-v3` | Reference. Host golden: 0 failures after regenerating `tests/portable/golden.json` for the new balanced streams. |
| Linux arm64 | `make native ARCH=portable` with `aarch64-linux-gnu-gcc`, run under `qemu-aarch64 -L /usr/aarch64-linux-gnu` | Cross-compile OK. Full golden 2026-11-24: qze, qzmf, x18, qzc, qzb streams identical to the x86-64 reference (0 mismatches). The one failure is `qzs` (a Python helper) exec'ing the arm64 `qtyp` without the qemu runner — harness limitation, not a stream bug. Skip with `QZ_SKIP=qzs`. |
| Windows x86-64 | MinGW-w64, `ARCH=portable`, static `.exe` | Cross-compile OK. Wine golden 2026-11-24: qze, qzmf, x18, qzc, qzb streams identical. Same `qzs`/`qtyp.exe` harness limitation as arm64 (`QZ_SKIP=qzs`). |
| macOS x86-64 / arm64 | `ARCH=portable`, sse2neon, `MACOSX_DEPLOYMENT_TARGET=11.0` | Source and `.github/workflows/native-matrix.yml` cover both (GitHub `macos-15-intel` and `macos-latest`). No Apple SDK on the Linux box, so no local cross-compile here. |
| Wheels | cibuildwheel (Linux, macOS) + pip wheel (Windows/MSYS2) | Wired in the same workflow; not built in this session. |

`python3 tests/portable/golden.py` checks byte-exact round trips and identical compressed bytes vs the Linux x86-64 reference. Cross runners: `QZ_RUNNER="qemu-aarch64 -L /usr/aarch64-linux-gnu"` or `QZ_RUNNER=wine QZ_EXE=.exe`.

Reproduce the table:

```bash
python3 bench/everyday/bench.py --threads 8 --timeout 1800 --out pareto.jsonl \
  --codecs qzip-fast,qzip-balanced,qzip-max,zstd-3,zstd-3-T,zstd-19-T,xz-6,xz-6-T,xz-9,7z-5,7z-9,brotli-5,brotli-9,brotli-11,lz4 \
  FILE...
python3 bench/everyday/pareto.py pareto.jsonl
```
