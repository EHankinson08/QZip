# qzcore — one QZip-owned compression engine (progress log)

Started Sat Nov 21 2026 15:45 MST. Goal (Elliott, 3:41 PM): our own engine, not a router. No GPL code.
Compute: PC2 (i9-14900KF, shared, load 6-19) at nice 19 + ionice -c3, dir `~/qzcore`. PC1 = ssh hop/file relay only. $0. No GPU.
Every size = full file needed to decode (header, dictionary, all metadata). Every "OK" = byte-exact sha256 round trip.

## What qzcore is
`qzc.cpp` — a clean-room context-mixing (CM) coder written for QZip from public CM literature
(bitwise arithmetic coding, hashed context bit-histories, indirect state->probability maps, run/match models,
gated logistic mixing, APM/SSE). No source from paq/lpaq/mcm/cmix/bsc-m03 was used or consulted.
All arithmetic is integer, so encode/decode are bit-identical across x86 machines.
Components (all ours):
- bit-history state machine generated at start-up from a (n0,n1) discounting rule (217 states)
- 24 hashed contexts: orders 1-8, 12, 20, 5 word contexts (case-folded word hashes, bigram/trigram/skip),
  3 sparse, 2 column ("char above"), 2 indirect (what followed this bigram/trigram last time), markup+word
- run model (last byte seen in 6 contexts), dual-length match model (6 and 24 byte hashes)
- 6 mixer weight sets selected by c0 / match length / c1 / hit-count / c2 / markup state, then a final mixer
- 5 APM/SSE stages, 16-bit final probability into a 32-bit binary arithmetic coder
- in-process QZip word transform (qwrt v2 logic ported into the engine; word model reads code tokens natively)

## 24 Nov 2026: the GPL mcm fork is QZip's default engine; qzcore is the `--max` option

Elliott approved making the QZip-mcm fork (`src/engine_mcmfork/`, GPL-3.0-or-later) the default (`auto`). qzcore
itself is unchanged and still clean-room (no mcm code in `src/qzcore/`); it now runs under `--max` (`auto-max`), and
for CSVs wider than the fork's 256-field table model. The repository is licensed GPL-3.0-or-later as a result.
Fork details: `src/engine_mcmfork/bench/RESULTS.md` (all PC2, byte-exact).

Same-machine comparison (PC2, nice 19 + ionice -c3, one job at a time; c / d wall s; peak RSS):

| file | fork -q11 (default) | x18 qzc2 | mcm -x11 | qzs |
|---|---:|---:|---:|---:|
| enwik8 | 17,361,988 · 44.5 / 44.2 · 6.1 GB | **17,257,456** · 139–144 / 145–150 · 9.9 GB | 17,543,256 · 40.5 / 40.3 · 6.1 GB | – |
| enwik9 | **136,854,237** · 373.4 / 369.6 · 6.2 GB | 137,076,090 · 1,296.2 / 1,324.8 · 12.0 GB | 138,569,355 · 336.0 / 333.0 · 6.2 GB | – |
| hctsa (33.1 MB, ~10k columns) | 8,510,953 · 13.3 / 13.9 · 6.3 GB | **7,009,669** · 44.7 / 46.0 · 9.4 GB (new, 24 Nov) | 8,510,968 · 12.3 / 12.9 | 7,107,262 · 169.3 / 119.4 · 2.2 GB |
| youth.csv | 1,435,856 · 10.1 / 10.8 · 4.6 GB | – | 1,798,461 | **1,408,094** · 139 / 7 |
| TRI_By_Name.csv | **4,254,555** · 18.1 / 19.3 · 6.1 GB | – | 5,418,808 | 4,775,793 · 204 / 58 |
| Panel_2000_2022.csv | **7,783,320** · 19.2 / 19.7 · 6.3 GB | – | 10,522,222 | 8,230,962 · 297 / 25 |

* Modal caveat: x18 enwik9 on a Modal 4-vCPU container took 2,746.5 / 2,774.6 s (same 137,076,090 B) on a different,
  slower CPU. Compare times on PC2 only: the fork is 3.5× faster than x18 there, not the ~7× its first notes said.
* hctsa x18 run (24 Nov, PC2): `QZ_THREADS=1 qzc2 c in out -m 12288` (router flags; hbits 29), cmp OK.
* Router rule (`src/qzrouter.py`, objective "balanced"): fork everywhere except predicted wide CSVs (> 256 fields) →
  x18, NIfTI → qzm, label maps → qzb, incompressible → stored. qzs is never a default route (slower than x18).

## 22–23 Nov 2026: size push under a speed cap, enwik9, PC1, THP, Data8277 (qzc2 default unchanged = x18)

All rows are byte-exact round trips (cmp on PC2/PC1, sha256 on Modal); sizes include all headers. PC2 = i9-14900KF, PC1 = Ryzen 9 9950X (both shared machines, nice 19 + ionice -c3, one benchmark at a time). Clean-room code; mcm is a reference binary only.

### Size push (opt-in experiment builds, not the default)
Extra first-layer weight sets, second-layer mixers, APMs and contexts (v7 header, Q7 flags; Q7 = 0 is byte-identical to x18). enwik8 results (Modal, RT OK):

| build | enwik8 B | vs x18 | vs mcm -x11 | compress time vs x18 (e10m) |
|---|---:|---:|---:|---:|
| x18 default | 17,257,456 | 0 | −1.63% | 1.00× |
| A (Q7 241) | 17,173,230 | −0.49% | −2.11% | ≈1.22× |
| B (Q7 7153) | 17,074,182 | −1.06% | −2.67% | ≈1.36× |
| 16 sets (Q7 65521) | 17,049,817 | −1.21% | −2.81% | ≈1.68× |

The rule is that no default may be slower than x18. Under that rule, every speed-recovery attempt gained nothing: prefetching the new rows, THP for the mixer weights, a 1-input mixer form, a long-match bypass, a per-set update skip, and swapping weak contexts for sets (best: −377 B on e10m, which is noise). The stop rule ended the search, and **x18 stays the default**. The target of about 10% under mcm (15,788,929 B) is still 1,468,527 B away.

### enwik9 (1,000,000,000 B)
| machine | codec | size B | vs mcm | c s | d s | peak RSS MB | load / vCPU |
|---|---|---:|---:|---:|---:|---:|---|
| PC2 | qzc2 x18 default `-m 12288 auto 2` | 137,076,090 | −1.08% | 1,296.2 | 1,324.8 | 11,968 | load 1.75 |
| PC2 | mcm -x11 | 138,569,352 | 0 | 331.4 | 325.5 | 6,171 | load 2.82 |
| Modal (4 vCPU reserved, 28 GB; CPU model not exposed) | qzc2 x18 default | 137,076,090 | −1.08% | 2,746.5 | 2,774.6 | 11,976 | load 0.00, nproc 20 |
| Modal (same container type) | mcm -x11 | 138,569,352 | 0 | 639.3 | 687.7 | 6,177 | |

The Modal qzc2 x18 run finished at 4:10:43 AM MST on 23 Nov. Three earlier preemptions (12:51, 2:36, and 2:38 AM MST) restarted from scratch because the result is only saved at the end. The successful attempt started at 2:38:33 AM MST (execution 5,530.06 s). Size matches PC2 exactly. Compress 2,746.46 s wall / 2,702.1 s user, peak 11,976 MB; decompress 2,774.62 s wall / 2,731.16 s user, peak 11,005 MB. Verify OK, sha 159b85351e5f76e6 (matches Modal mcm). Versus Modal mcm: 1,493,262 B smaller (1.08%) and about 4.30× slower to compress / 4.03× slower to decompress.

### PC1 (Ryzen 9 9950X; busy: load 3.7–7.8, swap full), enwik8
| build | size B | c s | d s | RSS MB |
|---|---:|---:|---:|---:|
| x18 `-O3 -mavx2` | 17,257,456 | 222.7 | 228.5 | 9,909 |
| x18 `-march=native` (gcc 13: znver4 + AVX-512; no znver5 target) | 17,257,456 (same bytes) | 217.4 | 228.6 | 9,909 |
| mcm -x11 | 17,543,254 | 60.8 | 63.4 | 6,144 |

enwik9 on PC1 was skipped: only 16 GB of RAM was free, swap was full, and other people's services were running there.

### Huge pages (THP) and core pinning, enwik8 x18 (THP mode = madvise, no reserved hugepages so no MAP_HUGETLB)
| machine | THP + pin | THP, no pin | no THP + pin | no THP, no pin |
|---|---|---|---|---|
| PC2 (P-core) | c143.2 / d148.4 | c142.3 / d147.5 | c184.1 / d192.6 | c182.8 / d191.5 |
| PC1 (CCD0 core 7) | c221.8 / d225.9 | c222.7 / d228.5 | c227.7 / d233.3 | c233.0 / d259.8 |

On PC2, THP (madvise on the big tables) is 22% faster on compress and 23% faster on decompress. On the loaded PC1 it is 3–4% faster on compress and up to 12% on decompress. Pinning has no measurable effect. THP was already the default in `big_alloc` with a fallback, so nothing changed.

### Structured files on PC2: router default `qzs` vs mcm -x11, xz -9 -T1, zstd -19 --long=27 -T1
| file | orig B | qzs B | c / d s | RSS c MB | mcm -x11 | xz -9 | zstd -19 --long | qzs vs mcm |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| Data8277.csv | 857,672,667 | **6,215,661** | 776.8 / 209.7 | 16,933 | 10,300,412 (70.0 / 67.9 s) | 30,534,864 (328.7 s) | 45,330,414 (449.4 s) | **−39.7%** |
| fndds_ingredient_nutrient_value (3).csv | 17,017,713 | **205,478** | 39.4 / 2.5 | 2,172 | 246,270 (2.3 / 2.3 s) | 728,608 (1.8 s) | 768,113 (5.9 s) | **−16.6%** |

qzs is much smaller on both files but much slower than mcm: about 11× on Data8277 compress and 17× on fndds.

## 22 Nov 2026: enwik8 with qzc2 (current default, x18 build), the numbers in the top-level README

PC2 i9-14900KF, a shared machine with other jobs running. Runs at nice 19 + ionice -c3, one benchmark at a time, America/Denver evening. Every row is OK: `cmp` byte-exact against enwik8; q24 rows also have the decoded sha256 prefix 2b49720ec4d78c3c, which matches enwik8. Sizes include all headers. Wall-clock times have a few % of noise.

| run | build | size B | vs mcm | c s | d s | RSS c / d MB | note |
|---|---|---:|---:|---:|---:|---:|---|
| mcm 0.84 -x11 (q23) | GPL ref | 17,543,254 | 0 | 39.17 | 38.01 | 6,148 | q21/q22: 40.4 / 38.8 s c, same size |
| qzc2 h29 `auto 2 -m 12288` (q22) | x17 + met1024 env = x18 | 17,257,456 | −1.63% | 139.33 | 144.74 | 9,936 / 9,840 | |
| qzc2 h29 default (q23) | x18 | 17,257,456 | −1.63% | 143.91 | 150.46 | 9,936 / 9,840 | |
| qzc2 h28 `-m 12288 28 2` (q24) | x18 | 17,310,999 | −1.32% | 139.37 | 144.03 | 5,840 / 5,745 | low-RAM row |
| QZ_THREADS=2 (q21) | x16 | 17,840,509 | +1.69% | 77.99 | 80.75 | 11,445 / 11,410 | 2 CPUs |
| QZ_THREADS=4 (q24) | x18 | 18,405,363 | +4.91% | 41.29 | 42.59 | 11,642 / 11,607 | CPUs 0,2,4,6 |
| QZ_THREADS=8 (q21) | x16 | 19,071,890 | +8.71% | 30.61 | 30.90 | 12,034 / 12,001 | 8 CPUs |
| QZ_THREADS=16 (q24) | x18 | 19,794,486 | +12.83% | 20.36 | 19.97 | 8,719 / 8,691 | CPUs 0-7,12-31 |
| (x16 T4 / T16, q21, for comparison) | x16 | 18,429,938 / 19,822,538 | | 42.36 / 20.99 | | | x18 is 0.13% / 0.14% smaller |

x16 = the same model without the mixer-update skip (v6 bits 20-22); its single-thread result is 17,281,177 B (+0.14%).
Rejected tonight and not in the build, measured on e3m/e10m/enwik8 (details in the speed log):
* final-mixer and APM learning-rate retune: −0.18% on e3m but +0.027% on enwik8
* state-map and APM update skips: +0.4–3% size
* PGO: +2.6% time
* GPU match finder: the match model is only 4% of encode time

## Log
### enwik8 slices (first 1 MB = e1m, first 10 MB = e10m), all OK
| build | e1m | e10m | e10m c s | note |
|---|---:|---:|---:|---|
| mcm 0.84 -x11 (GPL, reference only) | 217,494 | 1,957,478 | | target |
| mcm 0.84 -h11 | 218,710 | 1,966,373 | | |
| xz -9e | 290,732 | 2,720,256 | | |
| qzc v1 (first build) | 222,202 | 2,040,438 | 31 | |
| qzc v2 raw | 221,867 | 2,031,365 | 41 | |
| qzc v2 + word transform (-s1) | 232,957 | 2,099,811 | 24 | transform hurts CM at this size |
| qzc v2 + word transform (-s0) | 229,438 | 2,070,908 | 27 | |
| qzc v3 (+run model, indirect ctx, markup ctx, 6 weight sets, 16-bit p) | 218,181 | 1,994,401 | 46 | 1.9% from mcm -x11 |

## 17:47 MST update — resume run (sole owner), PC2, all OK = sha256 byte-exact, sizes include all headers
Timing note: PC2 was shared (load 10-27, several of our jobs at once), so wall times are upper bounds. RSS = peak of the largest process (`/usr/bin/time %M`).

### Build changes since v7
- Cfg header (v3): model mask, weight sets, APM stages, x2/f2, learning rates and table bits are stored in the file; decode never reads env.
- Auto table size (hbits 0): smallest table with 16·2^b ≥ 64 B per input byte, capped only by half of MemAvailable on the encoder. No artificial cap. enwik8 → hbits 29 (8.6 GB).
- Levels: L1/L2 drop the 6 cheapest-per-byte models (ablation on e10m), L3 = everything. Default level 0 = auto: L3 for inputs ≤ 64 MiB, L2 above.
- Speed: restrict/vectorised mixer, branchless model loop, rolling hashes for long orders and match model, prefetch, THP (2 MB pages): e10m 82 → 68 s on PC2, e3m 22.2 → 13.3 s on the box, identical output.
- Fast mode is now `qzb` = our word transform + our libbsc fork (Apache-2.0) in one binary. `qzf` (libsais + our post-BWT CM) was slower and bigger, kept as an experiment only.

### enwik8 (100,000,000 B)
| codec | size | c wall s | c CPU s | d wall s | peak RSS MB | MB/s c | OK |
|---|---:|---:|---:|---:|---:|---:|---|
| mcm 0.84 -x11 (GPL, reference only) | 17,543,256 | 103.1 | 102.9 | 88.1 | 6,148 | 0.97 | OK |
| mcm 0.84 -h11 | 17,624,088 | 77.6 | 77.4 | 69.9 | 6,051 | 1.29 | OK |
| **qzc L3 (apm3, sets 0x7d) hbits 29 auto** | 17,960,490 | 653.8 | 641.8 | 305.6 | 8,491 | 0.15 | OK |
| qzc L2 hbits 29 auto | 18,213,402 | 529.4 | 516.4 | 254.1 | 8,490 | 0.19 | OK |
| qzc L3 (apm3) hbits 27 | 18,198,061 | 585.4 | 580.6 | 450.6 | 2,347 | 0.17 | OK |
| qzc L2 hbits 27 | 18,395,297 | 342.7 | 341.4 | 421.6 | 2,357 | 0.29 | OK |
| qzc L1 hbits 27 | 18,569,598 | 337.7 | 336.6 | 390.5 | 2,358 | 0.30 | OK |
| (earlier) qzc v7 full hbits 29 | 17,833,266 | | | | | | OK |
| **qzb fast (word transform + libbsc fork)** | 20,660,391 | 4.14 | 11.52 | 0.97 | 454 | 24.2 | OK |
| qzb, transform off | 20,809,695 | 2.64 | 13.84 | 0.64 | 577 | 37.9 | OK |
| libbsc -b1024e2 | 20,803,016 | 3.99 | 11.41 | 0.95 | 481 | 25.1 | OK |
| qzf s4 (experiment) | 20,838,322 | 17.6 | 96.8 | 12.8 | 1,489 | 5.7 | OK |
| xz -9e -T1 | 24,831,648 | 169.7 | 165.1 | 2.1 | 670 | 0.59 | OK |
| zstd -19 --long=27 -T1 | 26,504,540 | 128.5 | 127.0 | 0.27 | 209 | 0.78 | OK |

Findings: the table size matters on enwik8 (h27 → h29: −1.0% L2, −1.3% L3). Trimming that cost 0.7% on e3m costs 1.4% at 100 MB. L3 with only 3 of 5 APM stages loses to v7 (all 5), so L3 now uses all 5 stages and all 7 weight sets (re-run in progress).
**Flag (>10% slower for <1% gain): L1 vs L2 (same speed, L2 −0.9%: L1 retired as default), L3 vs L2 at h29 (+23% time for −1.4%: kept as the max level, not flagged).**
Honest gap: max mode is 2.4% bigger than mcm -x11 and about 6x slower in CPU under this load. Not yet at the mcm size/speed target.

## 17:58 MST update
### enwik8, L3 with all 5 APM stages + all 7 weight sets (now the L3 default)
| codec | size | c wall s | c CPU s | d wall s | peak RSS MB | OK |
|---|---:|---:|---:|---:|---:|---|
| qzc L3 full, hbits 29 auto | **17,833,527** | 376.8 | 376.6 | 523.6 (loaded) | 8,504 | OK |
| mcm -x11 (ref) | 17,543,256 | 103.1 | 102.9 | 88.1 | 6,148 | OK |
Gap to mcm -x11: +1.65% size, 3.7x CPU. hbits 30 (17 GB) run in progress.

### enwik9 (1,000,000,000 B) — fast mode
| codec | size | c wall s | c CPU s | d wall s | d CPU s | peak RSS MB | threads | OK |
|---|---:|---:|---:|---:|---:|---:|---|---|
| **qzb (word transform + libbsc fork)** | **162,445,705** | 21.97 | 79.16 | 5.95 | 52.51 | 4,532 | 8 (OpenMP) | OK |
| libbsc -b1024e2 | 164,205,798 | 17.28 | 102.92 | 3.27 | 61.16 | 4,565 | all | OK |
| prior QZip qwrt+bsc pipeline | 162.47 MB | | | | | | | |
qzb is 1.07% smaller than libbsc, uses 23% less CPU, the same RAM, and runs at 45.5 MB/s. That is inside the target (about 2 min, under 8 GB).

### Logs / CSVs — structured mode `qzs` (qtyp column transform + qzc CM per stream, or whole-file CM, smaller wins, self-checked)
| file | size in | qzcore | prior best | mcm -x11 | c wall s | c CPU s | d wall s | peak RSS MB | OK |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Apache.log | 5,135,876 | 74,386 (L2) / **71,316 (qzc L3)** | 75,836 (mcm) | 75,836 | 24.1 | 50.0 | 13.5 | 612 | OK |
| HealthApp.log | 23,529,931 | **468,865** | 591,497 | pending | 138.9 | 262.5 | 40.8 | 2,183 | OK |
| SSH.log | 73,417,506 | 1,100,625 (qtyp only) / **716,603 (whole-file qzc L3)** | 933,871 | pending | 313.4 | 312.8 | 202.4 | 8,424 | OK |
| Android.log | 192,270,829 | **3,003,705** (whole-file L2) | 3,142,001 | pending | 433.6 | 1,287 | (running) | 17,248 | |
| fndds_ingredient_nutrient_value.csv | 24,057,371 | **230,391** | 239,195 | pending | 127.2 | 295.5 | 4.5 | 2,185 | OK |
| hctsa_timeseries-data.csv | 33,059,330 | **7,107,262** | 7,620,193 | pending | 169.3 | 500.1 | 119.4 | 2,208 | OK |
Fix made after SSH: whole-file CM used to be skipped above 64 MiB. Now it runs alongside the typed path up to 256 MiB, and only as a fallback above that.
Timing note: qzs c-time includes the self-check decode of the structured container. The RSS shown is the largest single process; parallel stream coders can add up to more.

### Medical NIfTI — `qzm` (QMED header split + exact int recast + 3D inter-slice/Lorenzo delta + shuffle; backends are qzcore only: qzb scores, qzc codes the top 2)
| file | qzcore | QMED (bsc/xz/zstd) | old 3DIS artifact | c wall s | d wall s | RSS MB | OK |
|---|---:|---:|---:|---:|---:|---:|---|
| 100001_flair_subtraction | 1,856,589 | 2,023,970 | 1,820,830 | 113.7 | 48.7 | 2,174 | OK |
| 100001_t1ce_subtraction | 2,381,306 | 2,471,142 | 2,191,705 | 87.5 | 42.2 | 2,175 | OK |
| 100001_time1_flair | **1,483,123** | 1,687,424 | 1,716,793 | 82.3 | 40.3 | 2,175 | OK |
| 100001_time1_t1ce-t1 | 2,185,190 | 2,290,094 | 2,105,785 | 94.6 | 48.7 | 2,175 | OK |
| 100001_time2_t1ce | **1,840,340** | 1,962,360 | 1,951,377 | 137.9 | 47.6 | 8,420 | OK |
| 100001_time2_t2 | **1,714,115** | 1,960,644 (pass 1) | 1,894,120 | 103.2 | 51.7 | 2,175 | OK |
| 100002_t1ce_subtraction | **10,780,836** | 10,970,930 (pass 1) | 11,666,574 | 331.9 | 162.7 | 8,434 | OK |
The remaining 19 volumes are running (`q_med.sh`). Bold = beats both QMED and the old artifact.

## 18:10 MST update
- enwik8 qzc L3, hbits 30 (17.1 GB table): 17,799,433 (−0.19% vs hbits 29). Compress took 551 s wall / 545 s CPU under load, against 376 s for hbits 29. **Flagged (>10% slower for <1% gain).** Auto sizing stays at 64 B of table per input byte (hbits 29 for enwik8). `hbits` can be passed explicitly, and the decoder reads it from the header.
- Android.log (192,270,829 B): qzs **3,003,705** (whole-file qzc L2; c 433.6 s wall / 1,287 s CPU including the parallel typed path; d 573.8 s; 16,843 MB; OK). For comparison: mcm -x11 3,081,546 (87.4 s / 78.7 s, 5,966 MB), xz -9e 7,093,892 (377 s), bsc 7,182,224 (4.1 s). **We beat mcm by 2.5% on size; we are 5x slower.**
- Medical 100002_time2_t1ce: qzm 2,065,832, against QMED 2,168,940 (pass 1) and the old artifact 2,122,898. OK.
- Still running on PC2 (`~/qzcore`, results append to rt.tsv / refs.tsv): q_med.sh (the remaining NIfTI volumes), then q_csv.sh (youth, TRI, Panel, products2m, Data8277 + refs, then mcm/xz/zstd refs on the earlier logs/CSVs), then q_e9.sh (enwik9 qzc L2 auto + mcm/xz/zstd refs).

## 18:51 MST update — fold finished PC2 runs (no restarts; three jobs still running)

Jobs left alone on PC2 (`~/qzcore`, nice 19 + ionice -c3): `q_med.sh`, `q_csv.sh`, `q_e9.sh`. Nothing had died. GPU was not used (free VRAM was 8,138 MiB of 32,607, under the 12 GB floor).

### Logs / CSVs — `qzs` vs libbsc, mcm -x11, xz -9e, zstd -19 --long

qzs compress wall time includes the self-check decode of the structured container. MB/s uses 10^6 B and that wall time, so it is conservative. "prior QZip" is the previous byte-exact QZip result (hybrid or web-app `.qc.gz`), not a reference codec. Refs still running or queued are marked pending.

| file | orig B | qzs B | c wall s | d wall s | MB/s c | peak RSS MB | mcm -x11 | libbsc | xz -9e | zstd -19 | vs mcm | OK |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Apache.log | 5,135,876 | **71,316** (L3; L2 74,386) | 24.1 | 13.5 | 0.213 | 612 | 75,836 (earlier) | pending | pending | pending | −6.0% | OK |
| HealthApp.log | 23,529,931 | **468,865** | 138.93 | 40.81 | 0.169 | 2,183 | pending | pending | pending | pending | — | OK |
| SSH.log | 73,417,506 | **716,603** (whole-file L3) | 313.38 | 202.39 | 0.234 | 8,424 | pending | pending | pending | pending | — | OK |
| Android.log | 192,270,829 | **3,003,705** (whole-file L2) | 433.60 | 573.79 | 0.443 | 16,843 | 3,081,546 | 7,182,224 | 7,093,892 | 7,997,164 | **−2.5%** | OK |
| fndds_ingredient_nutrient_value.csv | 24,057,371 | **230,391** | 127.22 | 4.45 | 0.189 | 2,185 | pending | pending | pending | pending | — | OK |
| hctsa_timeseries-data.csv | 33,059,330 | **7,107,262** | 169.29 | 119.36 | 0.195 | 2,208 | pending | pending | pending | pending | — | OK |
| youth-dataset-long-form.csv | 55,547,651 | **1,408,094** | 138.78 | 7.14 | 0.400 | 4,294 | 1,798,464 | 3,103,426 | 3,482,548 | 3,743,251 | **−21.7%** | OK |
| TRI_By_Name_1987_2014.csv | 68,403,261 | **4,775,793** | 204.44 | 58.11 | 0.335 | 8,419 | 5,418,813 | 6,655,472 | 7,182,196 | 7,618,709 | **−11.9%** | OK |
| Panel_2000_2022.csv | 58,192,381 | **8,230,962** | 296.74 | 24.66 | 0.196 | 4,313 | 10,522,226 | 11,317,634 | 12,846,924 | 13,691,107 | **−21.8%** | OK |
| products2m.csv | 326,108,495 | **47,643,116** | 386.97 | 166.29 | 0.843 | 8,437 | 54,780,824 | 55,599,448 | pending (running) | pending | **−13.0%** | OK |
| Data8277.csv | 857,672,667 | pending | | | | | pending | pending | pending | pending | — | |

Prior QZip (byte-exact, for context, not in the codec columns): youth 2,002,871; TRI_By_Name 6,184,004; Panel 12,492,837 (web app) / 12,926,900 (hybrid); products2m 63,180,558 (old artifact) / 63,810,146 (web app). qzs is smaller than all of those. mcm is faster than qzs on every file where both times exist (youth 9 s vs 139 s, products2m 189 s vs 387 s); qzs wins on size.

### Medical NIfTI — `qzm` vs QMED pass 2 and the old 3DIS artifact

21 of 26 volumes finished, all OK. QMED is pass 2 (best of the QMED transform + bsc/xz/zstd). Bold = smaller than both QMED and a byte-exact old artifact (or smaller than QMED when there is no exact artifact). MB/s uses 10^6 B.

| file | orig B | qzcore (qzm) | QMED (pass 2) | old artifact | c wall s | d wall s | MB/s c | peak RSS MB | OK |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 100001_flair_subtraction | 71,424,352 | 1,856,589 | 2,023,970 | 1,820,830 | 113.72 | 48.71 | 0.628 | 2,174 | OK |
| 100001_t1ce_subtraction | 71,424,352 | 2,381,306 | 2,471,142 | 2,191,705 | 87.45 | 42.21 | 0.817 | 2,175 | OK |
| 100001_time1_flair | 71,424,352 | **1,483,123** | 1,687,424 | 1,716,793 | 82.30 | 40.32 | 0.868 | 2,175 | OK |
| 100001_time1_t1ce-t1 | 71,424,352 | 2,185,190 | 2,290,094 | 2,105,785 | 94.56 | 48.66 | 0.755 | 2,175 | OK |
| 100001_time2_t1ce | 71,424,352 | **1,840,340** | 1,962,360 | 1,951,377 | 137.93 | 47.61 | 0.518 | 8,420 | OK |
| 100001_time2_t2 | 71,424,352 | **1,714,115** | 1,768,558 | 1,894,120 | 103.22 | 51.70 | 0.692 | 2,175 | OK |
| 100002_t1ce_subtraction | 71,424,352 | **10,780,836** | 10,970,930 | 11,666,574 | 331.94 | 162.70 | 0.215 | 8,434 | OK |
| 100002_time2_t1ce | 71,424,352 | **2,065,832** | 2,168,940 | 2,122,898 | 290.81 | 105.31 | 0.246 | 8,421 | OK |
| 100002_time2_t2 | 71,424,352 | **1,900,237** | 1,947,354 | 1,924,910 | 187.24 | 89.39 | 0.381 | 8,420 | OK |
| 100003_flair_subtraction | 71,424,352 | **1,858,801** | 1,985,780 | 2,193,580 | 81.50 | 38.23 | 0.876 | 2,175 | OK |
| 100004_flair_subtraction | 71,424,352 | **1,998,322** | 2,101,674 | 2,563,501 (not byte-exact) | 75.12 | 35.46 | 0.951 | 2,175 | OK |
| 100005_time2_t1ce-t1 | 71,424,352 | **1,975,088** | 2,021,850 | 2,172,848 | 87.48 | 55.21 | 0.816 | 2,175 | OK |
| 100008_time1_flair | 71,424,352 | **1,707,280** | 1,910,840 | 2,237,736 | 105.03 | 51.58 | 0.680 | 2,176 | OK |
| 100011_time2_t1ce-t1 | 71,424,352 | **2,515,885** | 2,564,990 | 2,904,985 | 130.67 | 48.54 | 0.547 | 2,177 | OK |
| 100013_time1_t1 | 71,424,352 | **4,464,881** | 4,479,087 | — | 294.34 | 125.97 | 0.243 | 8,426 | OK |
| 100015_time2_t1ce-t1 | 71,424,352 | **2,954,694** | 3,078,018 | 2,414,762 (not byte-exact) | 263.99 | 120.11 | 0.271 | 8,422 | OK |
| 100018_flair_subtraction | 71,424,352 | **8,386,641** | 8,587,706 | 9,582,732 | 234.27 | 106.97 | 0.305 | 8,432 | OK |
| 100018_time2_t2 | 71,424,352 | **1,247,441** | 1,364,114 | 1,605,521 | 72.20 | 34.14 | 0.989 | 2,175 | OK |
| 100020_time1_t1 | 71,424,352 | **1,981,159** | 2,160,206 | 2,540,754 | 95.87 | 50.08 | 0.745 | 2,175 | OK |
| 100023_time2_t1 | 71,424,352 | **1,343,846** | 1,513,474 | 1,712,871 | 104.05 | 44.64 | 0.686 | 2,175 | OK |
| 100026_time2_seg | 35,712,352 | 5,457 | 4,106 | — | 20.86 | 0.27 | 1.712 | 1,132 | OK |
| 100026_time2_t1ce | 71,424,352 | pending (compress running) | 1,901,492 | 1,891,623 | | | | | |
| 100031_time2_flair | 71,424,352 | pending | 1,951,030 | 1,952,512 | | | | | |
| 100038_time2_t1ce-t1 | 71,424,352 | pending | 2,238,738 | 2,287,560 | | | | | |
| 100296_flair_subtraction | 71,424,352 | pending | 2,145,324 | 2,321,029 | | | | | |
| 100302_flair_subtraction | 71,424,352 | pending | 3,447,034 | 2,316,794 | | | | | |

Honest misses among the finished set: 100001_flair_subtraction, 100001_t1ce_subtraction and 100001_time1_t1ce-t1 beat QMED but are larger than the old byte-exact artifact. 100026_time2_seg (a small segmentation volume) is **5,457 B vs QMED 4,106 B (+32.9%)** — QMED wins that one. 100001_time2_t2 was earlier compared to QMED pass 1 (1,960,644); pass 2 is 1,768,558 and qzm still wins.

### enwik9 max (qzc L2 auto) — still running

| codec | size | c wall s | d wall s | MB/s c | peak RSS MB | OK |
|---|---:|---:|---:|---:|---:|---|
| **qzb (fast)** | **162,445,705** | 21.97 | 5.95 | 45.5 | 4,532 | OK |
| libbsc -b1024e2 | 164,205,798 | 17.28 | 3.27 | 57.9 | 4,565 | OK |
| prior QZip fast (qwrt+libbsc, web path) | 162,473,695 | 126.7 | 39.7 | 7.89 | 6,816 | OK |
| prior QZip max P4 × mcm -h11 (stock mcm, not qzcore) | 146,200,704 | 235.7 | 141.4 | 4.24 | ~24,240 (4×) | OK |
| Oct-2025 web path, shipped stored_xz | 213,370,900 | 4,390.7 (engine+verify+xz) | 9.03 | 0.228 | 38,711 | OK |
| Oct-2025 engine only (not shipped; larger than xz) | 250,131,976 | included in the 4,390.7 s path | — | — | (same run) | OK |
| **qzc L2 auto** | **pending (compress running)** | ~38 min elapsed at 18:50 MST, ~35.7 GB RSS (hbits 31 class) | | | ~35,700 so far | |
| mcm -x11 / xz -9e / zstd -19 --long | pending (queued after qzc) | | | | | |

If qzc L2 scales like enwik8 L2 at 0.19 MB/s, compress would take ~88 min (ETA around 19:40 MST America/Denver), then a decode, then the refs. A larger table can be slower than that, so the ETA is a lower-ish bound, not a promise.

### Still running (do not restart)

- `q_med.sh`: 21/26 done; now on 100026_time2_t1ce; then 4 volumes. Rough ETA 19:15–19:30 MST America/Denver.
- `q_csv.sh`: products2m xz -9e running, then zstd, then Data8277.csv (857,672,667 B) + refs, then leftover refs on Apache/HealthApp/SSH/fndds/hctsa. Data8277 is the long pole.
- `q_e9.sh`: enwik9 qzc L2 auto compress, then decode, then mcm/xz/zstd refs. See ETA above.
