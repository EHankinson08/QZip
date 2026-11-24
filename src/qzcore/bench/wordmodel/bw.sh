#!/bin/bash
# bw.sh TAG "ENV1 ENV2" BIN INPUT [compress args...] -> wm.tsv: tag n size c_wall c_user d_wall d_user c_rssMB d_rssMB RT env args
set -u; cd ~/qzcore/wm; T=$1; EV=$2; B=$3; IN=$4; shift 4; mkdir -p tmp log
O=tmp/$T.q; D=tmp/$T.d
env $EV nice -n 19 ionice -c3 /usr/bin/time -f "%e %U %M" -o log/$T.ct $B c $IN $O "$@" 2> log/$T.c
env $EV nice -n 19 ionice -c3 /usr/bin/time -f "%e %U %M" -o log/$T.dt $B d $O $D 2> log/$T.d
read ce cu cm < log/$T.ct; read de du dm < log/$T.dt
cmp -s $IN $D && RT=OK || RT=FAIL
printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%d\t%d\t%s\t%s\t%s\n" $T $(stat -L -c%s $IN) $(stat -c%s $O) $ce $cu $de $du $((cm/1024)) $((dm/1024)) $RT "$EV" "$*" >> wm.tsv
rm -f $D; [ $RT = OK ] && rm -f $O
