import qzrouter as q, mmap, os
S={}
for gt,d in (("gt.tsv","c"),("gt_h.tsv","h")):
    for l in open(gt):
        r=l.rstrip("\n").split("\t")
        if r[-1]=="OK" and r[1] in ("qzb1","qzb0"): S.setdefault((d,r[0]),{})[r[1]]=int(r[3])
for (d,f),v in sorted(S.items()):
    if len(v)<2: continue
    fh=open(d+"/"+f,"rb"); a=q.analyze(mmap.mmap(fh.fileno(),0,access=mmap.ACCESS_READ))
    if a["kind"] in ("text","csv","log") and a["letters"]>0.1:
        print(d,f,a["kind"],a["size"]>=16<<20,"w1wins" if v["qzb1"]<v["qzb0"] else "w0wins",round((v["qzb1"]/v["qzb0"]-1)*100,2))
