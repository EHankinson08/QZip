# Third-party notices

QZip is licensed GPL-3.0-or-later (see `LICENSE`, `NOTICE`). It vendors the components below; `make native` builds
all of them except libcubwt.

| Component | Version | Path | License | Used for |
|---|---|---|---|---|
| **mcm fork "QZip-mcm"** (Mathieu Chartier; QZip changes by Elliott Hankinson) | mcm 0.84 @ 1c44c95 + QZip CSV/table and text contexts (`src/engine_mcmfork/QZIP_FORK.md`) | `src/engine_mcmfork/` | **GPL-3.0-or-later** (`src/engine_mcmfork/COPYING`) | **default engine** (`auto` mode, codec `qzmf`), built as the separate program `src/native/qzmf` |
| mcm (Mathieu Chartier) | 0.84 + QZip build fixes (`src/native/mcm/QZIP_PATCHES.diff`) | `src/native/mcm/` | GPL-3.0-or-later (`src/native/mcm/COPYING`) | legacy context-mixing text modes (`max`, `ultra`), separate program `qzcm` |
| lpaq1 (Matt Mahoney) | 1 | `src/native/fast/lpaq1.cpp` | GPL-2.0-or-later (used under GPL-3.0) | free-form log candidate of the 2025 router, separate program `qzlpaq` |
| libbsc (Ilya Grebnov) | 3.4.1 | `src/native/libbsc/` | Apache-2.0 (`src/native/LICENSE.libbsc`) | BWT + QLFC entropy coder (text modes, qzb) |
| libsais (Ilya Grebnov) | 2.12.2 | `src/native/libbsc/bwt/libsais/` | Apache-2.0 (`LICENSE` next to it) | suffix-array construction for the BWT |
| libcubwt (Ilya Grebnov) | as shipped with libbsc 3.4.1 | `src/native/libbsc/bwt/libcubwt/` | Apache-2.0 | source only, not compiled (CUDA) |
| zstd (Facebook / Meta, Yann Collet) | 1.5.7 | `src/native/everyday/zstd/` | BSD-3-Clause (`src/native/everyday/zstd` / LICENSE) | qze fast / binary-block backend |
| LZMA SDK (Igor Pavlov) | 26.06 | `src/native/everyday/lzma/` | Public domain (`src/native/everyday/lzma/lzma-sdk.txt`) | qze Quick LZMA backend (SQLite, small binaries / logs) |

QZip's own code (`src/qzcore/` x18 `qzc2` / `qzc` / `qzs` / `qzb` / `qzm`, `src/native/qz_wrt.c`, `qz_bsc.c`,
`fast/qtyp.c`, the Python packages) is Copyright 2026 Elliott Hankinson, GPL-3.0-or-later. The qzcore codecs were
written clean-room (no code from paq/lpaq/mcm/cmix/zpaq) and are kept as-is.

## License compatibility
* Apache-2.0 code (libbsc, libsais) is included under GPL-3.0 with its license text and notices kept in the paths above.
* GPL-2.0-or-later (lpaq1) is used under GPL-3.0 terms.
* BSD-3-Clause (zstd) is included under GPL-3.0 with its license text kept next to the sources.
* LZMA SDK 26.06 is public domain (Igor Pavlov) and is compatible with GPL-3.0.
