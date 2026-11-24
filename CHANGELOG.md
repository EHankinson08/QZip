# Changelog

## v1.0.0 (2026-11-24): first public release

**Default engine: QZip-mcm**, a fork of Mathieu Chartier's mcm 0.84 with an adaptive CSV/TSV table model and
three text contexts from QZip's x18 design (`src/engine_mcmfork/`).
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

**License:** GPL-3.0-or-later (the default engine is a fork of GPL mcm). Patent application pending (`PATENTS.md`).

**Removed:** the commercial edition and build targets, pricing/enterprise/dashboard pages, the S3 storage
backend, the 2025 GPU/RL experiments, exploratory test scripts and internal review notes.
