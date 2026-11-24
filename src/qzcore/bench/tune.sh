#!/bin/bash
# tune.sh BIN FILE TAG ENV... : compress-only probe -> tune2.tsv: tag env size c_wall c_cpu rssMB
B=$1; F=$2; T=$3; shift 3
env "$@" nice -n 19 ionice -c3 /usr/bin/time -o /tmp/qzt_$T.t -f "%e %U %M" ./$B c $F /tmp/qzt_$T.q ${HB:-27} 0 >/dev/null 2>&1
read w u m < /tmp/qzt_$T.t
echo -e "$T\t$*\t$(stat -c%s /tmp/qzt_$T.q)\t$w\t$u\t$((m/1024))" >> tune2.tsv; rm -f /tmp/qzt_$T.q /tmp/qzt_$T.t
