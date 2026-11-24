# QZip-mcm (fork of mcm; QZip's default engine)

This is a fork of **mcm** by Mathieu Chartier (Copyright (C) 2013, Google Inc.),
https://github.com/mathieuchartier/mcm, upstream commit 1c44c95.
mcm is licensed under the GNU General Public License v3 or later (see COPYING);
this fork stays under that same license. Every original copyright/license header is kept unchanged.

This repo is separate from the clean-room QZip code (repo_staging / repo_speed). No GPL code
from here is copied into those repos.

## What the fork adds (Elliott Hankinson / QZip, 2026)

* `QzCsv.hpp` (new, GPL): an adaptive CSV/TSV tracker. It learns the delimiter (`,` `\t` `;` `|`)
  and the field count only from bytes that are already coded, so the decoder stays in sync with no
  extra header bits. It switches on after 16 consistent rows with 3 or more fields. A block that does
  not look like a table in its first 64 lines stops being tracked, so plain text costs nothing.
* Table profiles: when a block is a table, weak text or binary contexts are swapped for:
  * Csv1: field index + the byte at the same offset in the same field of the previous row + last byte;
  * Csv2: field index + this field's prefix;
  * Csv3: the whole value of the same field in the previous row + this field's prefix;
  * Csv4 (binary-detected/numeric tables): previous field of this row + this field's prefix.
  
  The mixer weight set is selected by field index and position in the field.
  The number of mixer inputs is unchanged, so the speed cost is small.
* `-q` level (new archive type `kTypeCMQz`, 13 inputs like `-x`): on text blocks, sparse 2/3/4 are
  replaced by three contexts from the QZip x18 design:
  * an order-2 indirect context (the last two bytes that followed this bigram before);
  * a word skip-bigram;
  * the previous word + order 2.
* `-x` keeps mcm's text model; only the table path changes.
* Dev knobs: `QZF_*` environment variables (see CM-inl.hpp) override the profiles for A/B
  experiments. Encoder and decoder must use the same values. Defaults are the measured best.

The archive format of the stock levels is unchanged for non-table data. Tables compressed by this fork
are not decodable by stock mcm (and vice versa) because the models differ.

`bench/mfbench.sh` measures size, compress/decompress wall/user time and peak RSS, with a byte-exact cmp.

## Integration into QZip (24 Nov 2026)

This directory is the fork as of `repo_mcmfork` commit `f2f2938`, imported with `git archive` (history stays in
`repo_mcmfork`). It is now QZip's **default engine**, and the QZip repository as a whole is licensed
GPL-3.0-or-later (see `../../LICENSE`, `../../NOTICE`). The earlier note that no fork code goes into the clean-room
QZip repos no longer applies to `repo_staging`; QZip's own x18 code (`src/qzcore/`) is unchanged and still contains
no mcm code.

* Build: `make -C src/native qzmf` (part of `make native`) with exactly the flags benchmarked on PC2
  (`-O3 -fomit-frame-pointer -msse2 -std=c++11`, no `-march`). The output matched `bins/f7 -q11` byte for byte.
* Router codec `qzmf` (`src/qzrouter.py`): `qzmf -q<level> in out` in a temp dir (only the relative name `in` is
  stored in the archive); level 11 by default, lower when the RAM budget is smaller (~3 x 2^level MB).
* The router clears `QZF_*` from the environment before running the binary, so the dev knobs can never make an
  archive that the default decoder reads differently.
* Benchmarks: `bench/RESULTS.md` (PC2) — unchanged.
