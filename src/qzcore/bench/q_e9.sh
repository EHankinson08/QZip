#!/bin/bash
# enwik9: fast mode first (qzb + bsc ref), then max mode after the enwik8 full runs free their RAM
ln -sf ~/qzip_bench/enwik/enwik9 enwik9
./rt.sh qzb enwik9 e9_qzb 1
./refs.sh enwik9 bsc
while pgrep -f "qzc13 c enwik8|qzc13 d out/e8full" >/dev/null; do sleep 30; done
./rt.sh qzc14 enwik9 e9_qzc_L2 0 0 2
./refs.sh enwik9 mcmx11 xz zstd
