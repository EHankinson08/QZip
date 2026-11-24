# qzm vs outside lossless codecs on 26 NIfTI volumes (BraTS-style, 240x240x155)

Every result was decoded and compared byte for byte with the input. Sizes are the whole .nii file (352 B header
included); all 26 files are 71,424,352 B except the label map (35,712,352 B).

Codecs:
* `qzm`, `qzc2` (`-m 12288`), `qzb0` (qzb without the word transform), `xz -9`, `zstd -19 --long=27`: run by `../router/gt.py`.
* JPEG-LS (CharLS 2.4.5) and reversible JPEG 2000 (OpenJPEG 2.5.4): run by `med_outside.py` through imagecodecs
  2026.11.11, a $0 wheel installed into a local `--target` directory. Axial 2D slices, one thread.

Data. 22 volumes are float64 with non-integer values (resampled / registered). JPEG-LS and JPEG 2000 only code
integers, so their only lossless route is to split each 64-bit word into four 16-bit planes (`planes` mode); that
makes them 3-4x larger than qzm, which is not a fair test of the codecs. Four volumes are integer valued with a
<= 16-bit range (`int16` mode). That is the fair comparison:

| volume (int16) | qzm | JPEG-LS | JPEG 2000 rev. | xz -9 | zstd -19 | best |
|---|---|---|---|---|---|---|
| 100004_flair_subtraction | 1,998,322 | 2,396,710 | 2,561,439 | 2,581,068 | 3,069,473 | qzm (-16.6% vs JPEG-LS) |
| 100008_time1_flair | 1,707,280 | 2,190,584 | 2,296,135 | 2,481,264 | 2,763,295 | qzm (-22.1%) |
| 100018_time2_t2 | 1,247,441 | 1,738,769 | 1,941,639 | 1,845,312 | 2,016,087 | qzm (-28.3%) |
| 100026_time2_seg (label map) | 5,457 | 28,494 | 55,124 | 13,108 | 11,651 | qzb0 4,629 |

All 26 volumes (total bytes):

| qzm | qzc2 | qzb0 | xz -9 | zstd -19 | JPEG-LS | JPEG 2000 |
|---|---|---|---|---|---|---|
| 67,890,658 | 68,742,037 | 72,013,198 | 80,723,500 | 88,413,056 | 175,839,438 | 184,652,938 |

Smallest per volume: qzm 16, qzc2 9, qzb0 1.

Time, summed over the 26 volumes (PC2, loaded, not the same session for every codec, so only rough):

| codec | compress | decompress |
|---|---|---|
| qzm | 3,759 s | 1,693 s |
| qzc2 | 2,538 s | 2,792 s |
| qzb0 | 17 s | 10 s |
| xz | 276 s | 6 s |
| zstd | 227 s | 1 s |
| JPEG-LS | 4 s | 8 s |
| JPEG 2000 | 30 s | 30 s |

qzm buys its 17-28% smaller output over JPEG-LS with about 1000x more CPU.

Rows: `med_outside.tsv` (file codec n size enc_s dec_s mode OK); the others are in `../router/gt.tsv` and `extra.tsv`.
