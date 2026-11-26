# Changelog

## v1.2.0 (2026-11-26): Quick wins Silesia and Loghub

**Quick is smallest on all 24 files** of an independent Silesia + Loghub benchmark, and
**at least as fast to compress as 7-Zip** on every one of them. The 24 inputs are the
official 12-file Silesia corpus, the same 12 packed as `silesia.tar`, ten public Loghub
system logs, and those ten packed as `loghub.tar`. Every encode–decode job was
sha256-exact, in two confirming runs.

* **Silesia, 12 files separately:** Quick 42,679,123 B, 11.9% smaller than xz -9e
  (48,456,100 B). Also smaller than 7z -mx9 (48,758,631), brotli -q11 (49,564,563) and
  zstd -19 --long (52,817,908). Closest compress-time race is ooffice at 0.41 s vs
  7-Zip's 0.45 s.
* **silesia.tar:** Quick 42,678,988 B (11.9% under xz -9e at 48,419,296 B) in 11.75 s
  vs 7-Zip's 33.91 s. Quick now cuts a tar at member boundaries and compresses each
  file as if it arrived on its own.
* **Loghub, 10 files separately:** Quick 7,376,662 B, 48.9% smaller than xz -9e
  (14,443,644 B). Android_v1.log is 4,074,408 B vs xz at 7,093,900 B; Zookeeper.log is
  60% smaller than xz (84,622 vs 209,344 B). Apache.log compresses in 0.10 s vs
  7-Zip's 0.24 s.
* **loghub.tar:** Quick 7,640,965 B, 47.1% under xz -9e (14,455,700 B) in 9.27 s vs
  7-Zip's 34.00 s.
* **sao** uses a format-aware transform for binary star-catalog files (WCSTools/TDC
  layout), the same idea as PNG filters, FLAC, or 7-Zip's executable BCJ filter.
* Other Quick routes new in this release: numeric-delta log transform, Android logcat
  field streams, compact tar-header packing, and a PE code-section split.

**Speed caveats.** zstd and brotli still decompress faster than Quick. On `loghub.tar`,
Quick's decompress time is 2.2 s, up from 0.8 s in v1.1.0, and slower than 7-Zip and xz
on that file. `silesia.tar` decompresses in 0.85 s.

**Format:** v1.2.0 reads all v1.1.0 files. v1.1.0 cannot read the new archive, log, or
star-catalog streams Quick writes in this release.

Method: Intel Core i9-14900KF, 8 cores (`qze c -2 -T 8`, `7z -mx9 -mmt8`, `xz -9e -T8`,
`brotli -q 11 -w 24`, `zstd -19 --long=27 -T8`), 26 Nov 2026. Full table in the README.

## v1.1.0 (2026-11-25): Quick is the default

**Quick (`qzip compress`, `--quick`, engine `qze -2`) is the new default.** On a 14-file, 719 MB everyday mix
(CSVs, logs, JSON, source tarballs, enwik8, Silesia samba/mozilla, SQLite, PDF), Quick is strictly smaller AND
strictly faster to compress than 7z -9, xz -9, brotli -11 and zstd -19 -T on **all 14 files**. Mix at 8 threads:
ratio 7.38 (97.45 MB), 17.5 MB/s compress, 134.8 MB/s decompress. Peak RAM is 2.77 GB on the CPython tarball and
≤ 0.6 GB on the other files. Margins on some files are thin (CPython 0.09% under 7z -9; paper.pdf 124 B under
brotli -11). Full table in the README and `docs/EVERYDAY.md`.
* Three modes in the CLI and the web app: **Quick** (default), **Classic** (`--classic`, the v1.0 engine) and
  **Deep** (`--deep`, `qze -3` for now). `--fast` (`qze -1`) and `--max` stay.
* Quick engine (`src/native/everyday/qze`): parallel blocks with per-file routing. CSV column transform, SQLite
  page-type grouping, log field and NDJSON key transforms, x86/ARM64 branch filters, PDF stream split, and LZMA from
  the vendored LZMA SDK 26.06 so streams are byte-identical on x86-64, arm64 and Windows.
* New **shared-window parallel LZMA** for source tarballs: 8 fixed raw-LZMA blocks, each with its dictionary preset to
  all earlier bytes, plus a long-distance hash table in the encoder. Output does not depend on `-T`. This is what
  beats 7z -9 on CPython (19,965,372 vs 19,983,689 B, 11.9 vs 31.8 s), at the cost of 2.77 GB RAM on that file and a
  slower mix (17.5 MB/s, down from 22.6).
* Prebuilt standalone `qze` binaries (Quick / Fast / Deep engine; `qze c -2 in out`, `qze d in out`) attached to the
  release with SHA256SUMS.
* Docs: README results table and chart, `docs/EVERYDAY.md`, benchmark harness `bench/everyday/`.

**Format:** `.qz` containers from v1.0 still open. Quick writes QZE1 streams; backend 5 (shared-window LZMA) is new
in v1.1, so v1.0 cannot read Quick files of source tarballs.

## v1.0.0 (2026-11-24): first public release

**Default engine: QZip's table and text models on an mcm fork.** The fork starts from Mathieu Chartier's mcm 0.84;
the adaptive CSV/TSV table model and three text contexts from QZip's x18 design are ours (`src/engine_mcmfork/`).
That's where the size gain comes from.
* enwik9 136,854,237 B (1.24% smaller than mcm -x11, 1.11x its time, 6.2 GB RAM); enwik8 17,361,988 B.
* CSVs 20-26% smaller than mcm and 1.65-3.8x smaller than xz -9e on public datasets.
* Predictive router (`src/qzrouter.py`): one ~160 KB analysis, one encode, one verifying decode. Very wide CSVs
  (>256 fields) go to x18; NIfTI to qzm; incompressible data is stored.
* `--max` (`auto-max`): QZip's own clean-room codecs (x18 `qzc2`, `qzs`, `qzm`), sometimes smaller, 3-15x slower.

**Container `.qz` (format qzip-2):** a small JSON header followed by the raw codec output. Files are the
benchmarked size plus a few hundred bytes (the older base64+gzip `.qc.gz` added about 1%). `.qc.gz` / `.qc` files
from earlier versions still open.

**Packaging:** default install is small (numpy, zstandard, Flask, FastAPI; no torch). The 2025 engines are an
optional extra: `pip install -e ".[legacy]"`. `./run_local.sh` sets everything up and starts the web app.

**Web app:** local drag-and-drop UI on 127.0.0.1 (`qzip web`), no account or API key; large files run as
background jobs with progress and cancel.

**License:** GPL-3.0-or-later (the default engine starts from a fork of GPL mcm). Patent application pending (`PATENTS.md`).

**Removed:** the commercial edition and build targets, pricing/enterprise/dashboard pages, the S3 storage
backend, the 2025 GPU/RL experiments, exploratory test scripts and internal review notes.
