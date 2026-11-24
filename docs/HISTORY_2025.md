# QZip history: the 2025 engines

These modes (`fast`, `max`, `ultra`, `legacy`, `hybrid`) and decoding of 2025 web-app `.qc` files need the optional
2025 engines: `pip install -e ".[legacy]"` (torch, numba, scipy, ...). The default engine and `--max` do not.

## The Oct-2025 engine
The best 2025 results (Data8277.csv 116:1, products 5.16:1) were made by the **Oct-2025 encoder**
(`src/qzip_legacy_2025_10.py`, kept verbatim). The Nov-2025 engine cannot reproduce them, and its GPU CSV
path could return lossy output. QZip now runs the Oct-2025 engine (with the router above) in the default modes,
self-checks the GPU CSV path, and verifies every result. One showcase file,
`quantum_compression_enterprise.log` (195.7:1), turned out to be **lossy** (30,083 bytes missing on decode),
so it was dropped from all benchmarks.

## Older results: the 2025 web path (measured Nov 2026, before the new default)
Every file was sent through the web app's own HTTP endpoints (compress, download `.qc.gz`, decompress,
download, sha256 compare) on a CPU-only box. "QZip" = size of the `.qc.gz` file you download.

| file | type | original B | QZip | xz -9 | zstd -19 --long | brotli -11 | winner |
|---|---|---:|---:|---:|---:|---:|---|
| youth-dataset-long-form.csv | csv | 55,547,651 | **26.7:1** | 14.0 | 14.9 | 15.7 | **QZip** |
| TRI_By_Name_1987_2014.csv | csv | 68,403,261 | **9.20:1** | 9.1 | 9.1 | 9.3 | brotli (≈tie) |
| Panel_2000_2022.csv | csv | 58,192,381 | **4.66:1** | 4.47 | 4.25 | 4.45 | **QZip** |
| HPC.log (loghub) | log | 33,553,503 | **23.7:1** | 19.8 | 17.0 | 18.2 | **QZip** |
| 100008_time1_flair.nii | medical | 71,424,352 | **31.9:1** | 28.8 | 26.0 | 27.2 | QZip ≈ bzip2 (32.4) |
| fndds_ingredient_nutrient_value.csv (24 MB) | csv | 24,057,371 | 23.3:1 (xz) | 23.6 | 27.6 | 30.8 | bzip2 35.3 |
| 100000 Records.csv | csv | 27,811,781 | 3.43:1 (xz) | - | - | - | - |
| Zookeeper / OpenStack / Mac / Spark logs | log | 5–70 MB | 14.6–29.3:1 (xz) | ≈ same | +3–33% | +4–45% | brotli/bzip2 |
| Apache access log (242 MB) | log | 241,981,538 | 15.3:1 (xz) | 15.5 | 17.3 | 17.5 | bzip2 23.1 |
| JPEG / PNG photos and graphics (13 files) | image | 0.4–13 MB | 0.99–1.09:1 | ≈ same | ≈ same | ≈ same | JPEG XL / WebP lossless |

Earlier showcase results with the Oct-2025 encoder on the library path: Data8277.csv **116:1**,
products-2000000.csv **5.16:1**, both byte-exact. (Per-file tables from the 2025 review are not part of the public repository.)





## Routing table (Nov 2026)
### The existing router (Oct-2025 engine, `_hybrid_quantum_compress`)
* `_detect_data_type`: UTF-8 decodable = `text`, NIfTI/DICOM = `medical_imaging`, else `binary`.
* text: `_detect_repetitive_pattern` "structured log" (consistent line lengths or any repeated line) tries the ML
  pattern coder (`ml_columnar_log`); otherwise `_neural_dictionary_compress`: CSV (csv.reader: >100 rows, >1 column)
  goes to the ML columnar CSV coder (`_compress_csv_with_neural`), other text to the word-dictionary coder.
* medical_imaging: `medical_imaging_direct` (LZMA of the raw volume). binary: `_apply_binary_quantum_compression`.

### What changed
A hook at the top of `_hybrid_quantum_compress` calls `src/qzip_routes.py`. It refines the engine's `data_type`
(same CSV test, plus image / already-compressed magic bytes and a timestamp sniff that splits logs into structured
and free-form) and swaps a type's route **only where a measured method beats it**; elsewhere it returns `None` and
the original route runs unchanged. Exactly one method is encoded. Mode `legacy` turns the hook off.

| detected type | fast | max | ultra | if not installed | old route kept? |
|---|---|---|---|---|---|
| text / markup / JSON / XML | `text_fast` (QWRT + libbsc) | `text_max` (4 x mcm -h11) | `text_ultra` (2 x mcm -x11) | text_fast, then old route | no |
| CSV / TSV / delimited | `qtyp` * | `qtyp` * | `qtyp` * | **old ML-columnar CSV route** | yes, as fallback |
| structured log (fixed ` - ` / `\|` / tab fields) | `qtyp` * | `text_max` | `text_ultra` | xz | no |
| free-form log | `lpaq` * | `text_max` | `text_ultra` | text_fast | no |
| NIfTI / DICOM | **old `medical_imaging_direct`** | same | same | – | **yes** |
| image (JPEG/PNG/GIF/TIFF/WebP/HEIF) | xz (stored if larger) | same | same | – | no |
| already compressed (zip/gz/xz/zstd/7z/pdf/media) | stored | same | same | – | no |
| other binary | xz (zstd > 32 MiB) | same | same | – | no |

\* `qtyp` / `lpaq` (`src/qzip_fast_codecs.py`) plug in through `qzip_routes.register_candidate()`; if their helper
binaries are not built the "if not installed" column applies.

### Evidence (byte-exact round trips, PC2, Nov 2026; B = payload bytes)
| type | file | old route (Oct-2025) | chosen method | other measured options |
|---|---|---:|---:|---|
| text | enwik8 (100 MB) | 28,810,613 (480–608 s; the web app shipped xz 24,865,244) | **fast 20,666,109** (9.9 s / 4.5 s); max 17,624,100 (141 s / 64 s); ultra 17,543,268 | – |
| text | enwik9 (1 GB) | not measurable (>39 GB RAM after 4 min) | **fast 162,473,695** (127 s / 40 s); max 146,200,704 (236 s / 141 s); ultra 141,839,139 (957 s / 260 s) | – |
| text | enwik8 first 5 MB | 1,663,541 | **fast 1,217,743** (0.5 s); max 1,017,937 | Nov engine 1,740,606; xz 1,399,932 |
| CSV | fndds (17 MB) | **299,969** (2.4 s) | qtyp 212,603 | Nov engine 287,960; text_fast larger |
| CSV | youth (56 MB) | **2,060,195** (11.6 s) | qtyp 1,436,287 | Nov engine 2,003,127 (176 s) |
| CSV | products2m / Data8277 / TRI / Panel | 63.2 MB / 7.39 MB / 6.18 MB / 12.9 MB (artifacts) | qtyp −25% / −18% / −20% / −30% | – |
| structured log | HealthApp.log (24 MB) | 2,076,329 | qtyp 669,679; **max 600,430** | xz 1,496,408; text_fast 1,582,977 |
| free-form log | Apache.log (5 MB) | 268,677 | lpaq 91,041; text_fast 156,967; **max 76,803** | Nov engine 241,184; xz 180,972 |
| free-form log | Android.log (192 MB) | ~11.9 MB | lpaq 5,076,544 | qtyp 7,566,593 |
| free-form log | openstack_abnormal.log | 516,097 | text_fast 323,047; **max 231,191** | Nov engine round-trip FAIL |
| medical | 100001_time2_t2.nii (71 MB) | **1,885,848** (26 s) | kept | xz 2,249,568; bzip2 2,300,506; zstd-19 2,478,859 |
| image | JPEG/PNG (13 files) | 0.99–1.09:1 (larger than input on a 20 MP JPEG) | xz | JPEG XL / WebP lossless recommended |

### Time saved vs the old try-all `auto` (Oct + Nov engines + xz, keep smallest)
| file | old `auto`: s, B | routed `fast`: s, B | routed `max`: s, B |
|---|---|---|---|
| Apache.log | 25.1 s, 180,972 (xz) | 19.7 s incl. ~19 s one-time engine load, 156,967 | 22.8 s, 76,803 |
| HealthApp.log | 41.2 s, 1,496,408 (xz) | 1.5 s, 1,582,977 (text_fast; now routed to xz) | 16.7 s, 600,430 |
| openstack_abnormal.log | 12.1 s, 369,236 (xz) | 0.27 s, 323,047 | 6.8 s, 231,191 |
| fndds (17 MB) | 23.5 s, 287,960 (Nov engine) | 2.4 s, 299,969 (+4.2%) | 2.8 s, 299,969 |
| youth (56 MB) | 176.4 s, 2,003,127 (Nov engine) | 11.6 s, 2,060,195 (+2.8%) | 11.6 s, 2,060,195 |
| enwik8 5 MB | 18.0 s, 1,399,932 (xz) | 0.5 s, 1,217,743 | 13.4 s, 1,017,937 |
Times are compress + verify, library path, shared loaded box (load 16–25). The two CSV rows are the only size
regressions vs try-all (the Nov engine won them, but it is ~15x slower and fails round trips on other files);
both turn into −28% to −30% wins once `qtyp` is installed.
