#!/bin/bash
# mfbench.sh TAG BIN LEVEL FILE... -> TSV rows: tag file insize outsize c_wall c_user d_wall d_user peakMB ok
# Round-trip check is byte-exact (cmp). One file at a time unless PAR=1.
TAG=$1; BIN=$(readlink -f $2); LV=$3; shift 3; FILES=(); for a in "$@"; do FILES+=("$(realpath -s "$a")"); done
W=${WORKDIR:-/tmp/mfb}; mkdir -p $W
run() { f="$1"; b=$(basename "$f"); d=$W/$TAG.$b.$$; rm -rf $d; mkdir -p $d; cd $d
  /usr/bin/time -o c.t -f "%e %U %M" $NICE $BIN -$LV "$f" a.mcm >/dev/null 2>&1
  mkdir x; cd x; /usr/bin/time -o ../d.t -f "%e %U %M" $NICE $BIN d ../a.mcm junk >/dev/null 2>&1; cd ..
  ok=FAIL; cmp -s "$f" "x/$b" && ok=OK
  crash=""; grep -q signal c.t d.t && crash=CRASH; read ct cu cm < <(tail -1 c.t); read dt du dm < <(tail -1 d.t); cm=${cm:-0}; dm=${dm:-0}; mm=$(( (cm>dm?cm:dm)/1024 ))
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" $TAG $b $(stat -L -c%s "$f") $(stat -c%s a.mcm) $ct $cu $dt $du $mm $ok$crash
  cd /; rm -rf $d; }
if [ "$PAR" = 1 ]; then for f in "${FILES[@]}"; do run "$f" & done; wait; else for f in "${FILES[@]}"; do run "$f"; done; fi
