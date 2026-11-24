# qzc2 v5: stronger word model for prose (clean-room)

Built from published context-mixing ideas only (no mcm / paq / cmix source read or copied).
Binary under test: the experimental build with env toggles (`QZ_XW` = extra-context bitmask, `QZ_XS=1` = an
extra mixer weight set keyed by the previous word), then the adopted `src/qzcore/qzc2.cpp` (stream v5).
All runs on PC2 (i9-14900KF, shared and loaded: load average 10-27 from other jobs), nice 19, ionice -c3,
`-m 12288 auto 2` (prose dictionary), at most 6 jobs at once. Every e2e row was decoded and compared byte for byte (column RT).
Under that load, absolute times are only comparable inside one batch (jobs started together).

Files: `e2e.tsv` (compress + decompress + cmp), `conly.tsv` (compress only; screening / timing).
Columns: tag n size c_wall c_user d_wall d_user c_rssMB d_rssMB RT env args.
Bit meanings: see `TEXT_XW` in qzc2.cpp. Tag prefixes: s_/r2_/r3_/r4_ = single-context screens on e10m,
d_/a_d = drop one of the original 26 contexts, a_x = remove one new context from the full set, p_ = pruned sets,
e8_/t8_ = enwik8, e9_ = enwik9.

## enwik8 (100 MB), one batch each

| run | size (B) | vs old | compress s | decompress s | peak RSS MB |
|---|---:|---:|---:|---:|---:|
| old qzc2 (v4) | 17,781,803 | | 206.3 | 191.7 | 9,601 |
| v5 full set (adopted) | 17,427,419 | -1.99% | 265.2 | 258.8 | 9,858 |
| v5 minus 5 weakest | 17,431,449 | -1.97% | 272.3 | 280.8 | 9,858 |
| v5 minus 12 weakest | 17,484,444 | -1.67% | 277.2 | 242.8 | 9,857 |

Second batch, compress only, mcm -x11 run at the same time:

| run | size (B) | compress s | user s | peak RSS MB |
|---|---:|---:|---:|---:|
| mcm -x11 | 17,543,256 | 121.4 | 116.8 | 6,147 |
| old qzc2 | 17,781,803 | 200.4 | 197.2 | 9,599 |
| v5 full | 17,427,419 | 245.9 | 243.6 | 9,856 |
| v5 full, PGO build | 17,427,419 | 247.3 | 246.0 | 9,857 |

v5 is 115,837 B (0.66%) smaller than mcm -x11 on enwik8 but about 2x slower and uses 1.6x the RAM.
