# QZip auto router: predict the best codec, run only that one

`qzip compress FILE` (mode `auto`, now the default in the CLI, jobs and web app) no longer runs several encoders
and keeps the smallest. `src/qzrouter.py` reads about 160 KB of the file (head 64 KB, middle 32 KB, tail 32 KB),
computes a few cheap features and picks ONE codec. Only that codec encodes, and the result is decoded and
sha256-checked as before. The legacy engine only runs if the qzcore binaries are missing.

| mode | goal | codecs it can pick |
|---|---|---|
| `auto` (default) | smallest output | `qzc2` (CM), `qzc2_text` (CM + sorted word dictionary, prose), `qzs` (CSV / field logs), `qzm` (NIfTI), `qzb_w0` (label maps), `stored` |
| `auto-fast` | smallest among the fast codecs | `qzb`, `qzb_w0`, `stored` |
| `--codec NAME` / web `codec:NAME` | override, no prediction | any of `qzrouter.CODECS` (also `zstd`, `xz`, `qzc2_lean`) |
| `--ram-mb MB` / `QZIP_RAM_MB` | RAM budget | if `qzc2` needs more than the budget (default 3/4 of RAM), the router picks `qzc2_lean` (`-l 1`) |

Features (all from the sample): magic bytes (NIfTI, DICOM, JPEG/PNG, other compressed containers, ELF/PE,
safetensors), text share, letter / digit / space shares, order-0 and order-1 entropy, a zlib trial on 3 x 2 KB,
and line structure: log timestamps on >= 60% of lines; CSV = the same count of `,` / `\t` / `|` / `;` on >= 85% of
complete lines, ignoring quoted commas, with a density test for rows longer than the sample.

Rules (fitted on the measured corpus below):
* incompressible container -> stored. JPEG/PNG -> qzc2 (max) or qzb_w0 (fast). NIfTI with h0 < 0.1 b/B (label map) -> qzb_w0. Other NIfTI -> qzm.
* CSV >= 1 MB with letters -> qzs. Small or purely numeric CSV -> qzc2.
* Log with few spaces (field-delimited, e.g. `a|b|c`) -> qzs. Free-text logs -> qzc2.
* Prose (letters > 0.6, spaces > 0.1, >= 1 MB) -> qzc2_text, which caps its tables at `-m 8192`; with the RAM
  budget too small it falls back to `qzc2_text_lean`. Everything else -> qzc2.
* Fast: qzb with the word transform only when letters > 0.1 and the input is >= 16 MB, else qzb_w0.

## Accuracy (`bench/router/router_eval.py`, ground truth = every codec actually run, byte-exact)

PC2 (i9-14900KF, shared with other jobs). Ground truth: `bench/router/gt.tsv` + `extra.tsv` (earlier qzs/qzm/xz/zstd
runs) + `extra2.tsv` (qzc2_text). Holdout: 12 files never used to fit the rules (`gt_h.tsv`), but see the note below.
Loss = predicted codec size / best measured size - 1.

| set | objective | files | picked the best | within 1% | mean loss | worst |
|---|---|---|---|---|---|---|
| corpus | max (`auto`) | 53 | 42 (79%) | 46 (87%) | 0.685% | 13.34% (a NIfTI where qzc2 beats qzm) |
| corpus | fast (`auto-fast`) | 53 | 47 (89%) | 52 (98%) | 0.060% | 1.42% (Apache log) |
| holdout | max | 12 | 10 (83%) | 11 (92%) | 0.180% | 1.18% (airports.csv: qzc2 beat qzs) |
| holdout | fast | 12 | 11 (92%) | 12 (100%) | 0.016% | 0.19% |

The holdout is not fully clean. Its first fast-mode score was 9/12 best, mean loss 0.627%, worst 5.84% (a 79 KB
dpkg log). I then changed the log rule (no word transform below 16 MB) after seeing it, so the fast holdout row is
partly in-sample. The max holdout row was not tuned on.

Where `auto` is not optimal:
* qzm vs qzc2 on NIfTI. qzm is smaller on 16 of 25 volumes (up to 13.8%), qzc2 on 9 (up to 13.3%). No sample feature
  separated them, so the router keeps qzm. The 9 qzc2 wins cause most of the corpus loss.
* stored vs zstd on a .tgz: 0.84%.

Cost of predicting:
* Analysis takes 3-5 ms on an idle box and 9-40 ms on the loaded PC2. That is a median 0.012% of the predicted
  codec's compress time in `auto`, and a median 1.6% in `auto-fast`, where qzb is very fast; on tiny files it can
  exceed the encode time.
* Compress time, predicted codec only vs running every measured candidate: 6,935 s vs 11,166 s on the corpus (62%),
  296 s vs 619 s on the holdout (48%). The try-all total is a lower bound: unmeasured pairs and the try-all decode
  checks are not counted.
* `qzip/core.py` now builds the enterprise engine lazily. Routed qzcore and stored containers skip its ~10 s import:
  a 300 KB CLI compress went from 11.0 s to 1.2 s wall.

## RAM model
`qzc2_ram_mb(n)` vs measured RSS at `-m 12288`:

| input | model | measured |
|---|---|---|
| e1m | 571 MB | 576 MB |
| e10m | 8,799 MB | 8,819 MB |
| Panel 58 MB | 9,421 MB | 9,487 MB |
| enwik8 | 9,461 MB | 9,573 MB |

## Reproduce
`bench/router/gt.py JOBS [workers]` (paths are set at the top; `run_gt.sh` caps OpenMP at 2 threads), then
`router_eval.py CORPUS_DIR gt.tsv extra.tsv extra2.tsv`. `corpus_files.txt` lists the files; they are local copies
of public data sets and system files, not committed.

## qzc2 changes from the clean-room "mcm-style" ideas (published ideas only, no mcm code read or copied)

All runs are enwik8 on PC2 with byte-exact round trips. "Same session" rows ran side by side.

| config | size B | compress s | decompress s | RSS MB |
|---|---|---|---|---|
| qzc2 raw text, `-m 12288` (= PR #2 model) | 17,833,950 | 190.9 | 260.7 | 9,575 |
| qzc2 `wrt 2` prose dictionary, `-m 12288` (same session) | 17,781,803 | 122.8 | 195.9 | 9,601 |
| qzc2 `wrt 2`, `-m 8192` (router default for prose) | 17,835,480 | 206.5 | 159.9 | 5,505 |
| qzc2 lean `-l 1`, `-m 4096` | 18,090,626 | 235.1 | 194.2 | 2,566 |
| qzc2 lean + `wrt 2`, `-m 4096` (same session as the row above) | 18,011,850 | 152.3 | 149.0 | 2,593 |
| mcm -x11 (earlier, solo run) | 17,543,256 | 103 | 88 | 6.1 GB |

Notes:
* Word dictionary. PR #2's dictionary made qzc worse. Sorting the codes alphabetically inside each code-length class
  is the main win (e10m at hbits 25: frequency order 2,053,572, alphabetical 2,033,435, by word suffix 2,045,209).
  Dropping the merged space and keeping only words seen >= 128 times helps further (same order: 2,000,180 with no
  merged space and words seen >= 4 times; 1,977,213 at >= 128 times; raw text 1,980,756). The dictionary order is
  stored in the stream, so `qzc d` (also the v7 binary) decodes it unchanged.
* It loses on code, JSON and token lists: py stdlib +0.96%, src +0.80%, tokenizer.json +2.98%, merges +1.78%,
  word list +0.18%, jsonl +0.93%. So only the router's prose rule turns it on.
* Checksum-tagged, cache-line buckets. qzc already uses an 8-bit check plus low-priority replacement inside one
  64-byte line of four 16-byte slots. Two-line 8-way buckets were worse: e1m 217,257 vs 216,952; enwik8 at
  hbits 27 18,027,205 vs 18,013,799, and slower. Not adopted. The RAM win came instead from the dictionary: a
  shorter CM input fits a smaller table, so `-m 8192` with `wrt 2` matches raw text at `-m 12288` within 0.009%
  while using 42% less RSS.
* Smaller model set. `-l 1` (16 of 26 contexts, from the greedy ablation) plus `wrt 2` is 0.44% smaller and 35%
  faster to compress than `-l 1` alone, in 2.6 GB.
* Against mcm, qzc2 is still 1.36% larger on enwik8 (17,781,803 vs 17,543,256) and slower.

## Verifier: check uncertain picks on a sample (`src/qzverify.py`, on by default, `--no-verify` / `QZIP_VERIFY=0` off)

Clean-room, in the spirit of a "code owns the branch" decision gate: the router's rule pick gets a confidence; a
pick is checked only when it is uncertain, and the evidence (real bytes out of the real codecs) decides, not the score.

1. **Gate.** A pick is uncertain if it is one side of a known confusable pair (qzm vs qzc2 on medical volumes,
   prose vs code for qzc2_text vs qzc2, stored vs zstd), its features are close to the rule threshold
   (confidence = sigmoid of the threshold margin), or it is out of distribution (kNN distance > 0.15 to
   `bench/router/features.tsv` vectors of the same kind). Everything else acts on the rule pick at once.
2. **Skip when evidence is not worth it** (`skip_reason`): fast objective; predicted encode < 10 s; and the
   qzs vs qzc2 pair (CSV / logs), where the sample was measured to point the wrong way (qzs's column model gains
   with size, so a short sample favours qzc2: fndds sample -10.2% vs full +1.9%, HealthApp -6.3% vs +4.5%).
3. **Trial.** Compress a stratified sample with the pick and its rival: sized for ~2.5% of the predicted full
   encode, clamped 64 KB-1 MB, except NIfTI which needs a slab of >= 3 axial slices at 40% depth (CSV keeps its
   header; text uses 4-8 line-aligned blocks). Each trial is time-capped (max(1 s, 10% of the predicted encode)).
4. **Commit.** Switch only if the rival's sample is > 1% smaller; ambiguous or timed out -> the rule pick.

### Measured (`bench/router/verify_eval.py`, real trials on PC2; ground truth = every codec run byte-exact)

| set | router (rules) | verifier, all pairs, no skips (`ver_*.txt`) | verifier, final policy (`ver_*_policy.txt`) |
|---|---|---|---|
| corpus 53, max: best / within 1% | 42 / 46 | 46 / 50 | **48 / 52** |
| corpus mean / worst loss | 0.685% / 13.34% | 0.400% / 13.34% | **0.279% / 13.34%** |
| corpus files checked; added time median / max | - | 45; 6.35% / 1034.62% | **27; 5.44% / 12.68%** |
| holdout 12, max: best / within 1% | 10 / 11 | 12 / 12 | 10 / 11 (no file is checked) |
| holdout mean loss | 0.180% | 0.000% | 0.180% |
| corpus fast: best / mean loss | 47 / 0.060% | 48 / 0.059% (median +8.9% time) | 47 / 0.060% (off) |
| holdout fast: best / mean loss | 11 / 0.016% | 11 / 0.016% (median +38% time) | 11 / 0.016% (off) |

Added time = verifier wall / full encode time of the final codec. The final-policy column is a replay of the
recorded trials with `skip_reason` applied (`verify_eval.py ... --replay`), not a re-run. **The skip policy was chosen
after seeing these corpus results (in-sample).** On the holdout the two files fixed by the unrestricted verifier
(airports, records100k, both qzs vs qzc2) were right by luck of the same unreliable pair, so the policy gives them up.
The medical gains are not tuned (the 1% threshold was fixed beforehand) but the holdout has no medical volume, so
they have no out-of-sample confirmation yet.

Medical pair detail (`verify_detail.py`): correct switches qzm -> qzc2 on 7 volumes (up to -9.5% full size on
100302); one wrong switch (100002_time2_t1ce, +0.37%); 100015 ambiguous on the sample (-0.82%) but -11.77% on the
full file (still the worst loss); 100013's qzm trial hit its time cap -> rule pick.

**"Always correct" is not reachable this way.** Sample trials cannot see effects that grow with input size (qzs
column statistics, the 100015 volume), and on small files any trial costs more than it saves. The time target of
2-3% is also missed on medical volumes (median 5.4%) because qzm needs a 3-slice slab. What the verifier does buy:
on the corpus, within 1% of the best on 52/53 files instead of 46/53, for ~5% extra time on the files it checks.

## qzc2 v5: word model for prose (wrt 2)

`qzc2 c in out auto 2` (the router's `qzc2_text`) now adds 18 text contexts and one mixer weight set keyed by the
previous word. The two biggest wins were the innermost open bracket plus the first word inside it (-0.51% on e10m alone)
and the first bytes / first word of the current line (-0.32% / -0.35%). The stream records them (format v5, 5 more header
bytes), so the decoder needs no flags. Other modes, and `-l 1` / `-l 3`, still write v4 streams that are byte-identical
to the previous build. v3/v4 streams still decode.
enwik8 (`-m 12288`, decoded and compared): 17,781,803 -> 17,427,419 B (-1.99%), 0.66% smaller than mcm -x11 (17,543,256).
It costs about 1.25x the compress and decompress time of the previous build (about 2x mcm -x11's time in the same batch),
plus about 250 MB of RAM. Tables: `bench/wordmodel/README.md`.
