import sys
sys.argv=[0]; import router_eval as E
for tag,ev,tsvs in (("corpus","eval_c.tsv",["gt.tsv","extra.tsv","extra2.tsv"]),("holdout","eval_h.tsv",["gt_h.tsv"])):
    gt=E.load(tsvs); P=0; A=0; n=0; P_or=0
    for l in open(ev):
        r=l.rstrip("\n").split("\t")
        if len(r)<11 or r[0]=="file" or r[3].endswith("(unmeasured)"): continue
        m=gt.get(r[0],{}); cand=[c for c in ("qzc2","qzc2_text","qzs","qzm","qzb","qzb_w0") if c in m]
        if r[3]!="stored" and r[3] not in m: continue
        n+=1; P+=m[r[3]][1] if r[3] in m else 0; A+=sum(m[c][1] for c in cand)
    print(f"{tag}: {n} files, compress time predicted-only {P:.0f} s vs try-all over measured candidates {A:.0f} s ({P/A*100:.0f}%)")
