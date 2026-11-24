# qzcore — QZip's own compression engine

Every file here is QZip code except `vendor/libbsc` (libbsc 3.4.1 by Ilya Grebnov, Apache-2.0, with libsais and libcubwt 1.6.3 under Apache-2.0; see their LICENSE/AUTHORS files. Only the libcubwt header is used in the CPU build, and `libcubwt.cu` is compiled only for CUDA builds). There is no GPL code, and no paq/lpaq/mcm source was read.

| binary | mode | what it is |
|---|---|---|
| `qzc` | max | Clean-room context-mixing coder. All-integer, so output is bit-exact across machines. Levels 1-3, with 0 = auto (L3 up to 64 MiB, L2 above). The table is sized to the input (64 B per input byte, up to half the free RAM) and stored in the header. |
| `qzc2` | max (router default) | Successor to `qzc` with the same clean-room design, plus a prose word model (`wrt 2`), a template/structure model, a mixer-update skip and opt-in segment threads. The whole configuration is stored in the header, so the decoder never reads env variables and older streams still decode. enwik8: 17,257,456 B, 1.63% smaller than mcm -x11 and about 3.6× slower (see the top-level README). |
| `qzb` | fast | Our word transform plus a libbsc fork (BWT/libsais + QLFC), OpenMP. |
| `qzs` (`qzcsv.py` + `qtyp`) | structured | CSV/log column transform with each stream coded by `qzc`, or whole-file `qzc`, whichever is smaller. Self-checked before writing. Records line-ending style and the trailing newline. |
| `qzm` (`qzmed.py` + `qmed.py`) | medical | NIfTI header split, exact int recast, 3D inter-slice/Lorenzo delta and shuffle. `qzb` scores the candidates and `qzc` codes the best two. |

Build: `./build.sh` (needs g++ with OpenMP). Test: `tests/test_csv_endings.sh` covers 13 line-ending and trailing-newline cases.
CLI: `qzc c|d in out [hbits 0=auto] [wrt 0] [level 0..3]`, `qzb c|d in out [wrt 1]`, `qzs c|d in out`, `qzm c|d in out`.
Benchmarks (size, compress/decompress wall and CPU time, peak RAM, all sha256-checked) are in `RESULTS.md`. The scripts that produced them are in `bench/`.

### qzc2 options (env at compress time; all stored in the header, so decode needs no flags)

| knob | values | effect (enwik8, 22 Nov 2026, PC2) |
|---|---|---|
| `QZ_THREADS=T` (qzc2) | 1 (default) … 64 | Segment-parallel mode: T equal segments, each with its own model, thread and 1/T of the `-m` memory budget (at least 2 MB per segment). Encode and decode both use T threads. Output is deterministic and byte-exact for a given T. Size cost vs T1: T2 +3.2%, T4 +6.7%, T8 +10.4%, T16 +14.7% (T2/T8 measured on x16). Time: 144 → 78 / 41 / 31 / 20 s. |
| `QZIP_QZC2_THREADS=N\|auto` (router) | default 1 | How the router sets `QZ_THREADS` for qzc2. `auto` = idle logical CPUs (CPU count minus the 1-min load average), capped at 16 and at one segment per 2 MB. The default stays 1 because every split costs at least 3%. `QZIP_THREADS` is a separate setting, the libbsc thread count in `src/native/qz_native.py` (default 8), and it does not affect qzc2. |
| `QZ_DROP=mask`, `QZ_DROPH=mask` | bitmask of context models to switch off (models 0–31 / 32–43) | Speed/size presets for prose. **default** = `25184` / `44` (8 weak contexts dropped by greedy ablation; 36 of 44 active): 17,257,456 B, about 139–144 s. **D1** = `2147509088` / `60`: +0.25% size, about −3% time. **D2** = `3355992928` / `60`: +0.49% size, about −10% time. Any mask decodes, because the mask is in the header. |
| `-m MB`, hbits arg | `-m 12288` + `auto` (hbits 29 on enwik8) or `28` | hbits 28 = 5.8 GB peak RAM for +0.31% size (17,310,999 B). |
