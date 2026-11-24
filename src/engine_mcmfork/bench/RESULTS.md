# QZip-mcm fork results (PC2: i9-14900KF, nice 19 + ionice -c3, one job at a time, g++ 13.3)

Every row round-trips byte-exact (cmp). Times are wall seconds; RAM is peak RSS (MB) over compress and decompress.
"stock" is mcm 0.84 rebuilt from this repo. It matches the reference binary byte-for-byte on enwik8
(17,543,256 B; ref 40.2 s, stock 38.9 s / 40.5 s on a rerun). Raw rows are in pc2_results.tsv.

| file | stock mcm -x11 | fork -x11 (tables only) | fork -q11 (tables + text ctx) | vs mcm | c / d time vs mcm | QZip x18 / qzs (clean-room) |
|---|---:|---:|---:|---:|---:|---:|
| enwik8 (100 MB) | 17,543,256 · 40.5 s / 40.3 s · 6.1 GB | 17,543,256 (identical) | **17,361,988** · 44.5 s / 44.2 s · 6.1 GB | −1.03% | 1.10× / 1.10× | x18 default 17,257,456 · 139–144 s / 145–150 s · 9.9 GB (h28 low-RAM: 17,310,999 · 137.9 / 143.2 s) |
| enwik9 (1 GB, run once) | 138,569,355 · 336.0 s / 333.0 s · 6.2 GB | (not run) | **136,854,237** · 373.4 s / 369.6 s · 6.2 GB | −1.24% | 1.11× / 1.11× | x18 137,076,090 · 1,296.2 s / 1,324.8 s · 12.0 GB (PC2); Modal, other CPU: 2,746 / 2,775 s |
| youth.csv (55.5 MB) | 1,798,461 · 8.1 / 8.7 s | 1,439,215 | **1,435,856** · 10.1 / 10.8 s · 4.6 GB | −20.2% | 1.23× / 1.24× | qzs 1,408,094 · 139 s / 7 s |
| TRI_By_Name.csv (68.4 MB) | 5,418,808 · 14.8 / 15.6 s | 4,275,688 | **4,254,555** · 18.1 / 19.3 s · 6.1 GB | −21.5% | 1.23× / 1.24× | qzs 4,775,793 · 204 s / 58 s |
| Panel_2000_2022.csv (58.2 MB) | 10,522,222 · 15.7 / 16.6 s | 7,783,320 | **7,783,320** · 19.2 / 19.7 s · 6.3 GB | −26.0% | 1.22× / 1.18× | qzs 8,230,962 · 297 s / 25 s |
| fndds (3).csv (17.0 MB) | 246,276 · 2.3 / 2.4 s | 194,562 | **193,851** · 2.9 / 2.9 s · 3.3 GB | −21.3% | 1.26× / 1.23× | (qzs measured on a different fndds file) |
| hctsa.csv (33.1 MB, ragged rows) | 8,510,968 · 12.3 / 12.9 s | 8,510,953 | 8,510,953 · 13.3 / 13.9 s · 6.3 GB | −0.0% | 1.08× / 1.08× | qzs **7,107,262** · 169 s / 119 s |
| records100k.csv (27.8 MB) | 5,261,158 · 12.8 / 13.0 s | 5,108,275 | **5,098,228** · 14.3 / 15.2 s · 6.1 GB | −3.1% | 1.12× / 1.17× | — |

Peak RAM is mcm's (same hash table); the fork adds 128 KB plus about 2 × 64 KB line buffers.

## What was kept or rejected (box A/B on 8 MB slices; see box_dev_ablations.log)
Kept:
* CSV tracker + table profiles (text: Bracket/Sparse3/Sparse4 → Csv1-3): CSV slices −7.3%.
* Numeric tables detected as binary got their own table profile: Data8277 −35%, Panel −19%.
* Binary table profile with Csv4 (replacing Sparse3): Panel −18.7% → −24.4%.
* Field-indexed mixer select, text (MIX=1): records −2.1% → −3.6%, youth −19% → −20%.
* Field-indexed mixer select, binary (BMIX=1): Panel −27%. CSV-slice total: −8.99%.
* Activation rule (3+ fields, 16 consistent rows) + 64-line probe lockout: enwik8 loss from wiki tables went from +0.40% to 0.00%, and there is no tracker cost on plain text.
* -q text swap (Sparse2/3/4 → order-2 indirect, word skip-bigram, prev word + order 2), still 13 inputs: enwik 8 MB slice −0.96%.

Rejected (no meaningful gain):
* A 14th, 15th or 16th mixer input (Sparse34: +0.09%; Word12: +0.03%). q16 with 3 new contexts gave −0.90%, worse than the 13-input swap and slower.
* A 4th text swap (Bracket → Word12: −0.01%; Interval2 → Word12: −0.39%).
* Csv4 in the text profile (records got worse).
* Csv5 (field + order 2): +0.03%.
* Csv in the match profile's spare slot: none.
* Learn-rate retunes: none.
* Mixer variant 2: weaker than variant 1.

The stop rule fired on the CSV side after 3 consecutive rounds under 0.2% (M4/LR, Csv5, q-profile CSV drop lists).

## Verdict
* Text: -q is 1.03% smaller than mcm on enwik8 and 1.24% smaller on enwik9, at 1.10–1.11× mcm's time for both
  compress and decompress, with the same RAM. On enwik9 it beats QZip x18 by 0.16% (136,854,237 vs 137,076,090)
  and is 3.5× faster on the same machine (PC2: 373.4 s vs 1,296.2 s; an earlier version of this note compared against
  the Modal x18 time, 2,746 s on a different CPU, and said ~7×). On enwik8 it is 0.61% bigger than the x18 default
  (17,257,456; 0.29% vs the low-RAM h28 setting) and 3.1–3.2× faster, with about 60% of x18's RAM.
* Tables: −20% to −26% vs mcm on youth/TRI/Panel/fndds and −3% on records, at 1.12–1.26× mcm's time. It also beats clean-room
  qzs on TRI (−10.9%) and Panel (−5.4%), loses on youth (+2.0%), and is 10–15× faster.
  Ragged numeric series (hctsa, ~10,000 columns > the 256-field model) get nothing: x18 is 17.6% smaller
  (7,009,669 B, 44.7 s, PC2 24 Nov) and qzs 16.5% smaller (but 169 s).
* License: this fork is GPL-3.0-or-later (mcm's license). It cannot go into the clean-room QZip code as-is.
