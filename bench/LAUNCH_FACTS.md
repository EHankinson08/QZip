# QZip launch facts (verified numbers + caveats)

Use only the numbers on this page in posts. Every size is a byte-exact round trip (decoded file compared with `cmp`
or sha256 against the original), and every size includes all headers. "MB" = 10^6 bytes.

**Hardware:** "PC2", Intel Core i9-14900KF, a shared machine with other jobs running. Every run was at `nice 19` +
`ionice -c3`, one benchmark at a time, single-threaded coders, g++ 13.3. Times are wall-clock seconds and carry a few
percent of noise. **Dates:** 21–24 Nov 2026.
**Sources:** `src/engine_mcmfork/bench/RESULTS.md` + `pc2_results.tsv` (fork, mcm), `src/qzcore/RESULTS.md`
(x18, qzs, xz, zstd, qzb, libbsc). Charts: `charts/` (`python3 charts/make_launch_charts.py`).

## What QZip is (one line)
A verified lossless compressor with a local drag-and-drop web app and CLI. Its default engine is a GPL fork of
Mathieu Chartier's **mcm** context-mixing compressor, with new CSV/table and text models added by QZip.

## Credit (say this)
Most of the compression comes from **mcm** (Mathieu Chartier; Copyright (C) 2013 Google Inc.; GPL-3.0-or-later). QZip's fork adds about
1–1.2% on text and 20–26% on tabular CSVs on top of mcm. QZip is GPL-3.0-or-later as a result.

## Headline numbers

| | QZip default (mcm fork `-q11`) | stock mcm 0.84 `-x11` | QZip `--max` (x18, own code) | xz -9e | zstd -19 --long |
|---|---:|---:|---:|---:|---:|
| enwik8 size | **17,361,988** | 17,543,256 | 17,257,456 | 24,831,648 | 26,504,540 |
| enwik8 compress / decompress | 44.5 s / 44.2 s | 40.5 s / 40.3 s | 139–144 s / 145–150 s | 169.7 s / 2.1 s ¹ | 128.5 s / 0.27 s ¹ |
| enwik8 peak RAM | 6.1 GB | 6.1 GB | 9.9 GB | 0.7 GB | 0.2 GB |
| enwik9 size | **136,854,237** | 138,569,355 | 137,076,090 | not measured | not measured |
| enwik9 compress / decompress | 373.4 s / 369.6 s | 336.0 s / 333.0 s | 1,296.2 s / 1,324.8 s | | |
| enwik9 peak RAM | 6.2 GB | 6.2 GB | 12.0 GB | | |

¹ xz/zstd enwik8 times are from an earlier session (21 Nov) with heavier machine load.

Derived (exact):
* enwik8: fork is **1.03% smaller than mcm**, 30.1% smaller than xz -9e, 34.5% smaller than zstd -19; 1.389 bits/char.
  It is **0.61% larger** than QZip's own x18, which is 3.1–3.2× slower.
* enwik9: fork is **1.24% smaller than mcm** and **0.16% smaller than x18**; 1.095 bits/char. It takes 1.11× mcm's time
  and is **3.5× faster than x18** on the same machine (373.4 s vs 1,296.2 s), with half its RAM (6.2 vs 12.0 GB).

## CSV results (real public datasets)

| file | size | QZip default | ratio | vs mcm | vs qzs (`--max`) | xz -9e | zstd -19 |
|---|---:|---:|---:|---:|---:|---:|---:|
| youth-dataset-long-form.csv | 55,547,651 | **1,435,856** | 38.7:1 | −20.2% | +2.0% (qzs smaller) | 3,482,548 (2.43× larger) | 3,743,251 (2.61×) |
| TRI_By_Name_1987_2014.csv | 68,403,261 | **4,254,555** | 16.1:1 | −21.5% | −10.9% | 7,182,196 (1.69×) | 7,618,709 (1.79×) |
| Panel_2000_2022.csv | 58,192,381 | **7,783,320** | 7.5:1 | −26.0% | −5.4% | 12,846,924 (1.65×) | 13,691,107 (1.76×) |
| fndds_ingredient_nutrient_value (3).csv | 17,017,713 | **193,851** | 87.8:1 | −21.3% | −5.7% | 728,608 (xz -9; 3.76×) | 768,113 (3.96×) |
| records100k.csv | 27,811,781 | **5,098,228** | 5.5:1 | −3.1% | – | – | – |
| hctsa_timeseries-data.csv (~10,000 columns) | 33,059,330 | 7,009,669 via x18 ² | 4.7:1 | −17.6% vs fork | qzs 7,107,262 | – | – |

Fork CSV times: 10–19 s compress for 55–68 MB files (1.12–1.26× mcm's time; qzs takes 139–297 s).
² The fork's table model tracks at most 256 fields, so on hctsa it gains nothing (8,510,953 B). QZip's router predicts
this from the column count and uses x18 instead (44.7 s compress, 9.4 GB RAM).

## File size on disk
The `.qz` file is the payload above plus a ~780-byte header (enwik8: 17,362,773 B; enwik9: payload + <1 KB). Before
v1.0.0 the `.qc.gz` container added about 1% (enwik8 17,535,597 B), which hid most of the text lead over mcm.

## brotli (box, 8-vCPU VM; size is machine-independent)
brotli -q11 lgwin24: enwik8 25,742,001 B; records100k.csv 7,893,616 B. Same session: records100k.csv xz -9e
8,018,604 B, zstd -19 --long=27 8,427,879 B (QZip 5,098,228; mcm 5,261,158).

## Caveats (keep these in any post)
1. **Not a record.** enwik9 136.85 MB is well behind the top of the Large Text Compression Benchmark (cmix, nncp and
   similar, mattmahoney.net/dc/text.html), which are far slower. QZip's pitch is "mcm-class text, much better CSVs,
   verified, with a drag-and-drop app", not "best ratio in the world".
2. **Context mixing is symmetric:** decompression takes as long as compression (~44 s for enwik8), unlike xz/zstd
   (a few seconds or less). RAM is ~6 GB at the default level (it drops to ~3 GB / 1.5 GB on smaller machines, at some
   cost in size).
3. **Modal timing caveat:** x18 enwik9 was also timed on a Modal 4-vCPU container: same 137,076,090 B in 2,746.5 s /
   2,774.6 s, on a different, slower CPU. Do not compare that time with PC2 numbers. An early fork note said "~7×
   faster than x18" using it; the same-machine figure is **3.5×**.
4. **Single machine, shared:** all times are from one shared desktop at low priority. Expect ±5% elsewhere.
5. **Where `--max` wins:** youth.csv (qzs −1.9%), enwik8 (x18 −0.6%), HealthApp.log (qzs −8.9% vs the fork,
   468,865 vs 514,724 B; the fork figure is from an 8-core VM). `--max` is 3–15× slower.
6. **Images/compressed files:** no gain (stored). NIfTI medical volumes use QZip's own qzm codec, unchanged.
7. **Verified output:** every result is decoded and sha256-checked before it is returned; if that fails, QZip ships
   xz instead. Nothing unverified ships.
8. **License:** GPL-3.0-or-later (because of mcm). A patent application (U.S. 19/318,010) is pending; GPLv3
   section 11 governs the patent rights that come with the code (`PATENTS.md`).

## End-to-end checks of this release (box, 8 vCPU / 15 GB VM, 24 Nov 2026)
See `bench/E2E_20261124.md` (CLI and web-app round trips on an enwik8 sample and the CSV set).
