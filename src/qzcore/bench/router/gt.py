# ground truth: run codecs on corpus files (compress + decompress + cmp), resumable; rows -> gt.tsv
import os, subprocess, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
R=os.path.expanduser("~/qzcore/router"); C=os.environ.get("GTC",R+"/c"); GT=os.environ.get("GTOUT",R+"/gt.tsv"); Q=os.path.expanduser('~/qzcore')
NI=['nice','-n','19','ionice','-c3']
CODECS={
 'qzb1':  (lambda i,o:[Q+'/qzb','c',i,o,'1'], lambda i,o:[Q+'/qzb','d',i,o]),
 'qzb0':  (lambda i,o:[Q+'/qzb','c',i,o,'0'], lambda i,o:[Q+'/qzb','d',i,o]),
 'xz9':   (lambda i,o:['sh','-c',f'xz -9 -c -T1 "{i}" > "{o}"'], lambda i,o:['sh','-c',f'xz -d -c "{i}" > "{o}"']),
 'zstd19':(lambda i,o:['sh','-c',f'zstd -f -q -19 --long=27 -T1 -c "{i}" > "{o}"'], lambda i,o:['sh','-c',f'zstd -q -d --long=27 -c "{i}" > "{o}"']),
 'qzc2':  (lambda i,o:[R+'/qzc2','c',i,o,'-m','12288'], lambda i,o:[R+'/qzc2','d',i,o]),
 'qzc2l1':(lambda i,o:[R+'/qzc2','c',i,o,'-l','1','-m','4096'], lambda i,o:[R+'/qzc2','d',i,o]),
 'qzs':   (lambda i,o:[Q+'/qzs','c',i,o], lambda i,o:[Q+'/qzs','d',i,o]),
 'qzm':   (lambda i,o:[Q+'/qzm','c',i,o], lambda i,o:[Q+'/qzm','d',i,o]),
}
lock=threading.Lock()
def done():
    s=set()
    if os.path.exists(GT):
        for l in open(GT): p=l.rstrip().split("\t"); (p[-1]!="ERR") and s.add((p[0],p[1]))
    return s
def timed(cmd,tag):
    t=R+f'/tmp/{tag}.time'
    r=subprocess.run(NI+['/usr/bin/time','-f','%e %U %M','-o',t]+cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    e,u,m=open(t).read().split()[-3:]; os.remove(t); return r.returncode,float(e),float(u),int(m)//1024
def job(f,c):
    i=C+'/'+f; tag=f+'.'+c; o=R+'/tmp/'+tag+'.q'; d=o+'.d'
    rc,ce,cu,cm=timed(CODECS[c][0](i,o),tag+'c')
    if rc or not os.path.exists(o): row=[f,c,os.path.getsize(i),'',ce,cu,'','',cm,'','ERR']
    else:
        sz=os.path.getsize(o); rc2,de,du,dm=timed(CODECS[c][1](o,d),tag+'d')
        ok= rc2==0 and subprocess.run(['cmp','-s',i,d]).returncode==0
        row=[f,c,os.path.getsize(i),sz,ce,cu,de,du,cm,dm,'OK' if ok else 'FAIL']
    for p in (o,d):
        if os.path.exists(p): os.remove(p)
    with lock: open(GT,'a').write('\t'.join(map(str,row))+'\n')
jobs=[l.split() for l in open(sys.argv[1]) if l.strip()]
dn=done(); jobs=[j for j in jobs if tuple(j) not in dn]
with ThreadPoolExecutor(int(sys.argv[2]) if len(sys.argv)>2 else 4) as ex: list(ex.map(lambda j: job(*j), jobs))
