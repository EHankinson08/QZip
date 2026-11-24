#!/bin/bash
# refs.sh FILE [codecs...] : reference codecs on PC2, same machine, nice19/ionice3.
# row: codec file insize outsize c_wall c_cpu d_wall d_cpu peakRSS_MB threads sha
F=$1; shift; CODECS=${@:-"bsc mcmx11 mcmh11 xz zstd"}
BSC=~/qzip_wrt/tools/bscbin; MCM=~/qzip_bwt/bin/mcm; N="nice -n 19 ionice -c3"; mkdir -p out
for c in $CODECS; do
  O=out/ref_$c.$(basename $F); th=1
  case $c in
    bsc)    C="$BSC e $F $O -b1024e2"; D="$BSC d $O $O.d"; th=all ;;
    bsc1)   C="$BSC e $F $O -b1024e2 -T"; D="$BSC d $O $O.d -T"; th=1 ;;
    mcmx11|mcmh11) lv=${c#mcm}; C="$MCM -$lv $F $O"; D="sh -c 'rm -rf $O.x; mkdir $O.x; cd $O.x; $MCM d ../$(basename $O) junk; mv $(basename $F) ../$(basename $O).d; cd ..; rm -rf $(basename $O).x'" ;;
    xz)     C="sh -c 'xz -9e -T1 -k -c $F > $O'"; D="sh -c 'xz -d -T1 -c $O > $O.d'" ;;
    zstd)   C="zstd -q -19 --long=27 -T1 -f $F -o $O"; D="zstd -q -d --long=27 -f $O -o $O.d" ;;
  esac
  eval $N /usr/bin/time -o $O.tc -f \"%e %M %U %S\" $C >/dev/null 2>&1
  eval $N /usr/bin/time -o $O.td -f \"%e %M %U %S\" $D >/dev/null 2>&1
  a=$(sha256sum < $F | cut -c1-16); b=$(sha256sum < $O.d 2>/dev/null | cut -c1-16); ok=FAIL; [ -s $O ] && [ -e $F ] && [ "$a" = "$b" ] && ok=OK
  read ct cm cu cs < $O.tc; read dt dm du ds < $O.td
  cc=$(echo "$cu+$cs"|bc); dc=$(echo "$du+$ds"|bc); mm=$(( (cm>dm?cm:dm)/1024 ))
  echo -e "$c\t$(basename $F)\t$(stat -L -c%s $F)\t$(stat -c%s $O)\t$ct\t$cc\t$dt\t$dc\t$mm\t$th\t$ok" | tee -a refs.tsv
  rm -f $O.d
done
