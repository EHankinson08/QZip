#!/bin/bash
# Regression: qtyp + qzcsv must round-trip byte-exact for line-ending edge cases.
set -e; cd "$(dirname "$0")/.."; T=$(mktemp -d); fail=0
gen(){ python3 - "$T" <<'PY'
import sys,os,random
d=sys.argv[1]; random.seed(7)
rows=["%d,%s,%d.%02d,%s"%(i,random.choice(["a","bb","ccc"]),random.randint(0,999),random.randint(0,99),"2026-11-%02d"%(1+i%28)) for i in range(3000)]
body="\n".join(rows)
cases={
 "lf_trailing":"id,k,v,d\n"+body+"\n",
 "lf_no_trailing":"id,k,v,d\n"+body,
 "crlf_trailing":("id,k,v,d\n"+body+"\n").replace("\n","\r\n"),
 "crlf_no_trailing":("id,k,v,d\n"+body).replace("\n","\r\n"),
 "mixed":"id,k,v,d\r\n"+"".join(r+("\r\n" if i%3==0 else "\n") for i,r in enumerate(rows)),
 "mixed_no_trailing":"id,k,v,d\r\n"+"".join(r+("\r\n" if i%3==0 else "\n") for i,r in enumerate(rows))[:-1],
 "cr_only_tail":"id,k,v,d\n"+body+"\r",
 "blank_lines":"id,k,v,d\n\n"+body.replace("\n","\n\n",50)+"\n\n",
 "quoted_newlines":'id,txt\n'+"".join('%d,"line one\nline, two"\n'%i for i in range(500)),
 "header_only":"id,k,v,d",
 "header_only_nl":"id,k,v,d\n",
 "empty":"",
 "single_newline":"\n",
}
for k,v in cases.items(): open(os.path.join(d,k+".csv"),"wb").write(v.encode())
PY
}
gen
for f in $T/*.csv; do
  n=$(basename $f .csv)
  ./qtyp e $f $T/$n.dir 2>/dev/null >/dev/null || mkdir -p $T/$n.dir
  if ./qtyp d $T/$n.dir $T/$n.qt 2>/dev/null && cmp -s $f $T/$n.qt; then a=OK; else a=FAIL; fail=1; fi
  python3 qzcsv.py c $f $T/$n.qzs 2>/dev/null && python3 qzcsv.py d $T/$n.qzs $T/$n.out && cmp -s $f $T/$n.out && b=OK || { b=FAIL; fail=1; }
  printf "%-20s qtyp=%s qzcsv=%s (%d -> %d)\n" $n $a $b $(stat -c%s $f) $(stat -c%s $T/$n.qzs 2>/dev/null || echo 0)
done
rm -rf $T; exit $fail
