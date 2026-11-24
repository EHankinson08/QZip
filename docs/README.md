# QZip documentation

| document | what's in it |
|---|---|
| [USAGE.md](USAGE.md) | CLI and modes, the web app, configuration, the `.qz` container, default-engine results in detail, when not to use QZip |
| [BENCHMARKS.md](BENCHMARKS.md) | methodology and full results for QZip's own engines (`--max`): enwik8/9, logs, CSVs, NIfTI, with charts |
| [../bench/LAUNCH_FACTS.md](../bench/LAUNCH_FACTS.md) | every headline number with its source and caveats |
| [../bench/E2E_20261124.md](../bench/E2E_20261124.md) | end-to-end CLI and web-app checks of the v1.0.0 release |
| [img/CHARTS.md](img/CHARTS.md) | the README and launch charts, and how to regenerate them |
| [../src/engine_mcmfork/QZIP_FORK.md](../src/engine_mcmfork/QZIP_FORK.md) | what the default engine adds to mcm, and its A/B log |
| [HISTORY_2025.md](HISTORY_2025.md) | the 2025 engines and their older results |

## Benchmark methodology (summary)

* Every size is a byte-exact round trip (decoded file compared with `cmp` or sha256) and includes all headers.
* Machine: Intel i9-14900KF ("PC2"), a shared desktop. Every run at `nice 19` + `ionice -c3`, one job at a time,
  single-threaded coders, g++ 13.3. Times are wall-clock seconds with a few percent of noise; RAM is peak RSS.
* Tools are compared on the same machine, in the same session where possible; exceptions are marked in the tables.
* MB = 10^6 bytes.
