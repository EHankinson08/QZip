#!/bin/bash
# rt.sh BIN FILE TAG [args...] -> tsv: tag file insize outsize c_wall c_cpu d_wall d_cpu peakRSS_MB sha
B=$1; F=$2; T=$3; shift 3
O=out/$T.$(basename $F).q; mkdir -p out
N="nice -n 19 ionice -c3"
$N /usr/bin/time -o $O.tc -f "%e %M %U %S" ./$B c $F $O "$@" 2>$O.log
$N /usr/bin/time -o $O.td -f "%e %M %U %S" ./$B d $O $O.d 2>>$O.log
a=$(sha256sum < $F | cut -c1-16); b=$(sha256sum < $O.d | cut -c1-16)
ok=FAIL; [ -s $O ] && [ -e $F ] && [ "$a" = "$b" ] && ok=OK
read ct cm cu cs < $O.tc; read dt dm du ds < $O.td
cc=$(echo "$cu+$cs"|bc); dc=$(echo "$du+$ds"|bc); mm=$(( (cm>dm?cm:dm)/1024 ))
echo -e "$T\t$(basename $F)\t$(stat -L -c%s $F)\t$(stat -c%s $O)\t$ct\t$cc\t$dt\t$dc\t$mm\t$ok" | tee -a rt.tsv
rm -f $O.d
