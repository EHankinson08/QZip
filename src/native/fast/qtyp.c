/* (c) 2026 Elliott Hankinson. QZip, GPL-3.0-or-later. QZip-authored.
   qtyp: type-aware reversible column transform for CSV/TSV/logs (QZip "structured" fast-mode prototype).
   qtyp e <in> <dir>      qtyp d <dir> <out>
   Line 0 is kept raw (header). Remaining lines split on '\n', fields split quote-aware on an auto-detected
   delimiter (, \t | ; space). Per column, the type is inferred over ALL values:
     TEMPLATE: dominant numeric skeleton (digit runs -> placeholder, optional leading '-' as a sign flag),
               e.g. ints, fixed-point decimals, dates, times, timestamps, versions. Each digit run becomes its
               own integer sub-stream (fixed width with leading zeros, or canonical variable width), coded as
               zigzag(delta) or zigzag(value) LEB128 varints, whichever is smaller. Non-matching values are
               exceptions (flag + raw text); empty cells get their own flag.
     ENUM:     <= 65536 distinct values and distinct <= n/8 -> frequency-ranked dictionary + varint codes.
     TEXT:     raw values, '\n'-terminated.
   Output is a directory of streams; QZip compresses each stream with its best backend. Byte-exact by construction. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#define MAXC 512
#define MAXR 16
typedef struct{unsigned char*b;size_t n,cap;}buf;
static void put(buf*x,const void*s,size_t k){if(x->n+k>x->cap){x->cap=(x->n+k)*2+4096;x->b=realloc(x->b,x->cap);}memcpy(x->b+x->n,s,k);x->n+=k;}
static void pc(buf*x,int c){unsigned char ch=c;put(x,&ch,1);}
static void pv(buf*x,uint64_t v){while(v>=128){pc(x,(v&127)|128);v>>=7;}pc(x,v);}
static uint64_t gv(const unsigned char*b,size_t*p){uint64_t v=0;int s=0;for(;;){unsigned char c=b[(*p)++];v|=(uint64_t)(c&127)<<s;if(c<128)break;s+=7;}return v;}
static uint64_t zz(int64_t v){return ((uint64_t)v<<1)^(uint64_t)(v>>63);}
static int64_t uz(uint64_t v){return (int64_t)(v>>1)^-(int64_t)(v&1);}
static unsigned char*rd(const char*p,size_t*n){FILE*f=fopen(p,"rb");if(!f){perror(p);exit(1);}fseek(f,0,2);*n=ftell(f);fseek(f,0,0);unsigned char*b=malloc(*n+1);if(*n&&fread(b,1,*n,f)!=*n)exit(2);fclose(f);return b;}
static char*DIR_;
static void wf(const char*nm,buf*x){char p[4096];snprintf(p,sizeof p,"%s/%s",DIR_,nm);FILE*f=fopen(p,"wb");if(x->n)fwrite(x->b,1,x->n,f);fclose(f);}
static int ex(const char*nm){char p[4096];snprintf(p,sizeof p,"%s/%s",DIR_,nm);FILE*f=fopen(p,"rb");if(f){fclose(f);return 1;}return 0;}
static unsigned char*rf(const char*nm,size_t*n){char p[4096];snprintf(p,sizeof p,"%s/%s",DIR_,nm);return rd(p,n);}
/* ---- skeleton of a value: digit runs -> 0x01; returns -1 if value contains 0x01 or a run > 18 digits ---- */
static int skel(const unsigned char*s,size_t k,unsigned char*out,size_t*ol,int*neg,size_t rs[MAXR],size_t rl[MAXR],int*nr){
 size_t i=0;*neg=0;*nr=0;*ol=0;if(k>=2&&s[0]=='-'&&s[1]>='0'&&s[1]<='9'){*neg=1;i=1;}
 while(i<k){unsigned char c=s[i];if(c==1)return -1;
  if(c>='0'&&c<='9'){size_t j=i;while(j<k&&s[j]>='0'&&s[j]<='9')j++;if(j-i>18||*nr>=MAXR)return -1;rs[*nr]=i;rl[*nr]=j-i;(*nr)++;out[(*ol)++]=1;i=j;}
  else{out[(*ol)++]=c;i++;}
  if(*ol>250)return -1;}
 return 0;}
static uint64_t pnum(const unsigned char*s,size_t k){uint64_t v=0;for(size_t i=0;i<k;i++)v=v*10+(s[i]-'0');return v;}
/* simple hash map: bytes -> index */
typedef struct{uint64_t*h;uint32_t*idx;size_t cap,n;}hmap;
static uint64_t hsh(const unsigned char*s,size_t k){uint64_t h=1469598103934665603ULL;for(size_t i=0;i<k;i++){h^=s[i];h*=1099511628211ULL;}return h|1;}
typedef struct{const unsigned char*s;uint32_t k;uint64_t cnt;}ent;
static int hfind(hmap*m,ent*E,const unsigned char*s,size_t k,int ins){uint64_t h=hsh(s,k);size_t p=h&(m->cap-1);
 for(;;){if(!m->h[p]){if(!ins)return -1;m->h[p]=h;m->idx[p]=m->n;E[m->n].s=s;E[m->n].k=k;E[m->n].cnt=0;return m->n++;}
  if(m->h[p]==h){ent*e=&E[m->idx[p]];if(e->k==k&&!memcmp(e->s,s,k))return m->idx[p];}p=(p+1)&(m->cap-1);}}
static int cmpent(const void*a,const void*b){const ent*x=*(ent*const*)a,*y=*(ent*const*)b;return x->cnt<y->cnt?1:x->cnt>y->cnt?-1:0;}
typedef struct{uint64_t*off;uint32_t*len;size_t n,cap;}col;
int main(int c,char**v){
 if(c<4){fprintf(stderr,"qtyp e in dir | qtyp d dir out\n");return 1;}
 if(v[1][0]=='e'){
  size_t n;unsigned char*b=rd(v[2],&n);DIR_=v[3];
  size_t h0=0;while(h0<n&&b[h0]!='\n')h0++;
  buf hdr={0};put(&hdr,b,h0);wf("hdr",&hdr);
  size_t st=h0<n?h0+1:n; int lastnl=(n>0&&b[n-1]=='\n'); int hashdrnl=h0<n;
  const char cand[]={',','\t','|',';',' '};int best=0;double bs=-1;
  for(int d=0;d<5;d++){int hist[64]={0};int lines=0,cnt=1,q=0;for(size_t i=st;i<n&&lines<2000;i++){if(b[i]=='"')q^=1;else if(b[i]==cand[d]&&!q)cnt++;else if(b[i]=='\n'){hist[cnt<63?cnt:63]++;cnt=1;lines++;q=0;}}
   int m=1;for(int k=2;k<64;k++)if(hist[k]>hist[m])m=k;double s=(m<2)?0:hist[m]*(double)(m<32?m:32)*(d==4?0.5:1);if(s>bs){bs=s;best=d;}}
  unsigned char D=cand[best];
  col C[MAXC];memset(C,0,sizeof C);buf cnt={0};uint64_t nl=0;int maxf=0;
  size_t i=st;
  while(i<n){size_t e=i;while(e<n&&b[e]!='\n')e++;
   int f=0;size_t s=i;int q=0;
   for(size_t k=i;;k++){ if(k<e&&b[k]=='"')q^=1;
    if(k==e||(b[k]==D&&!q&&f<MAXC-1)){col*x=&C[f];if(x->n==x->cap){x->cap=x->cap*2+1024;x->off=realloc(x->off,x->cap*8);x->len=realloc(x->len,x->cap*4);}x->off[x->n]=s;x->len[x->n]=k-s;x->n++;f++;s=k+1;if(k==e)break;}}
   if(f>maxf)maxf=f;pv(&cnt,f);nl++;i=e+1;}
  wf("cnt",&cnt);
  buf meta={0};put(&meta,"QTYP1",5);pc(&meta,D);pc(&meta,lastnl);pc(&meta,hashdrnl);put(&meta,&nl,8);uint32_t MF=maxf;put(&meta,&MF,4);
  unsigned char sk[256];size_t sl,rs[MAXR],rl[MAXR];int ng,nr;
  for(int j=0;j<maxf;j++){col*x=&C[j];size_t N=x->n;char nm[64];
   /* pass 1: skeleton frequencies + distinct values */
   size_t cap=1;while(cap<N*2+16)cap<<=1; if(cap<1024)cap=1024;
   hmap ms={calloc(4096,8),calloc(4096,4),4096,0};ent*SE=malloc(4096/2*sizeof(ent)+sizeof(ent)*2048);
   hmap mv={calloc(cap,8),calloc(cap,4),cap,0};ent*VE=malloc((N+16)*sizeof(ent));
   unsigned char*skbuf=malloc(256*2048);size_t nempty=0,nsk=0;
   for(size_t r=0;r<N;r++){const unsigned char*s=b+x->off[r];size_t k=x->len[r];
    int id=hfind(&mv,VE,s,k,1);VE[id].cnt++;
    if(!k){nempty++;continue;}
    if(skel(s,k,sk,&sl,&ng,rs,rl,&nr)==0&&nr>0){int e=hfind(&ms,SE,sk,sl,0);if(e<0&&ms.n<2000){memcpy(skbuf+ms.n*256,sk,sl);e=hfind(&ms,SE,skbuf+ms.n*256,sl,1);}if(e>=0){SE[e].cnt++;nsk++;}}}
   int bsk=-1;for(size_t e=0;e<ms.n;e++)if(bsk<0||SE[e].cnt>SE[bsk].cnt)bsk=e;
   size_t nne=N-nempty; int type=2;
   if(bsk>=0&&SE[bsk].cnt>=0.8*nne&&nne>0)type=0; else if(mv.n<=65536&&mv.n*8<=N)type=1;
   if(type==0){
    const unsigned char*S=SE[bsk].s;size_t SL=SE[bsk].k;int R=0;for(size_t q=0;q<SL;q++)if(S[q]==1)R++;
    /* per-run width analysis */
    int fixedw[MAXR];int wset[MAXR];int allcanon[MAXR];for(int q=0;q<R;q++){fixedw[q]=1;wset[q]=-1;allcanon[q]=1;}
    for(size_t r=0;r<N;r++){size_t k=x->len[r];if(!k)continue;const unsigned char*s=b+x->off[r];
     if(skel(s,k,sk,&sl,&ng,rs,rl,&nr)||sl!=SL||memcmp(sk,S,SL))continue;
     for(int q=0;q<R;q++){if(wset[q]<0)wset[q]=rl[q];else if(wset[q]!=(int)rl[q])fixedw[q]=0;if(rl[q]>1&&s[rs[q]]=='0')allcanon[q]=0;}}
    int wmode[MAXR];for(int q=0;q<R;q++)wmode[q]=fixedw[q]?wset[q]:0; /* 0 = canonical variable */
    buf fl={0},rw={0},nr0[MAXR],nr1[MAXR];memset(nr0,0,sizeof nr0);memset(nr1,0,sizeof nr1);uint64_t prev[MAXR]={0};
    uint64_t c0[MAXR]={0},c1[MAXR]={0};
    for(size_t r=0;r<N;r++){size_t k=x->len[r];const unsigned char*s=b+x->off[r];
     if(!k){pc(&fl,2);continue;}
     int ok=skel(s,k,sk,&sl,&ng,rs,rl,&nr)==0&&sl==SL&&!memcmp(sk,S,SL);
     if(ok)for(int q=0;q<R;q++){if(wmode[q]){if((int)rl[q]!=wmode[q])ok=0;}else if(rl[q]>1&&s[rs[q]]=='0')ok=0;}
     if(!ok){pc(&fl,1);put(&rw,s,k);pc(&rw,'\n');continue;}
     pc(&fl,ng?3:0);
     for(int q=0;q<R;q++){uint64_t val=pnum(s+rs[q],rl[q]);pv(&nr0[q],zz((int64_t)val));pv(&nr1[q],zz((int64_t)(val-prev[q])));prev[q]=val;}}
    pc(&meta,0);pc(&meta,SL);put(&meta,S,SL);pc(&meta,R);
    for(int q=0;q<R;q++){int dl=nr1[q].n<nr0[q].n;pc(&meta,wmode[q]);pc(&meta,dl);snprintf(nm,64,"c%03d.n%02d",j,q);wf(nm,dl?&nr1[q]:&nr0[q]);
     /* same numbers as signed decimal text, one per line (alternative form; container keeps the smaller) */
     buf*src=dl?&nr1[q]:&nr0[q];buf tx={0};size_t p=0;char tb[32];while(p<src->n){int64_t z=uz(gv(src->b,&p));int k=snprintf(tb,32,"%lld\n",(long long)z);put(&tx,tb,k);}
     snprintf(nm,64,"c%03d.t%02d",j,q);wf(nm,&tx);free(tx.b);free(nr0[q].b);free(nr1[q].b);}
    snprintf(nm,64,"c%03d.f",j);wf(nm,&fl);snprintf(nm,64,"c%03d.r",j);wf(nm,&rw);free(fl.b);free(rw.b);
   } else if(type==1){
    ent**P=malloc(mv.n*sizeof(ent*));for(size_t e=0;e<mv.n;e++)P[e]=&VE[e];qsort(P,mv.n,sizeof(ent*),cmpent);
    uint32_t*rank=malloc(mv.n*4);for(size_t e=0;e<mv.n;e++)rank[P[e]-VE]=e;
    buf dct={0},cd={0};for(size_t e=0;e<mv.n;e++){put(&dct,P[e]->s,P[e]->k);pc(&dct,'\n');}
    for(size_t r=0;r<N;r++){int id=hfind(&mv,VE,b+x->off[r],x->len[r],0);pv(&cd,rank[id]);}
    pc(&meta,1);snprintf(nm,64,"c%03d.d",j);wf(nm,&dct);snprintf(nm,64,"c%03d.e",j);wf(nm,&cd);free(dct.b);free(cd.b);free(P);free(rank);
   } else {
    buf rw={0};for(size_t r=0;r<N;r++){put(&rw,b+x->off[r],x->len[r]);pc(&rw,'\n');}
    pc(&meta,2);snprintf(nm,64,"c%03d.r",j);wf(nm,&rw);free(rw.b);
   }
   if(type!=2){buf rw={0};for(size_t r=0;r<N;r++){put(&rw,b+x->off[r],x->len[r]);pc(&rw,'\n');}snprintf(nm,64,"c%03d.x",j);wf(nm,&rw);free(rw.b);}
   fprintf(stderr,"col %d type %d n=%zu distinct=%zu\n",j,type,N,mv.n);
   free(ms.h);free(ms.idx);free(SE);free(mv.h);free(mv.idx);free(VE);free(skbuf);
  }
  wf("meta",&meta);
  fprintf(stderr,"qtyp: delim=0x%02x lines=%llu cols=%d\n",D,(unsigned long long)nl,maxf);
 } else {
  DIR_=v[2];size_t mn;unsigned char*m=rf("meta",&mn);if(memcmp(m,"QTYP1",5))return 3;
  unsigned char D=m[5];int lastnl=m[6],hashdrnl=m[7];uint64_t NL;memcpy(&NL,m+8,8);uint32_t MF;memcpy(&MF,m+16,4);size_t mp=20;
  typedef struct{int type;unsigned char S[256];int SL,R;int wm[MAXR],dl[MAXR];unsigned char*n[MAXR];size_t np[MAXR];uint64_t prev[MAXR];unsigned char*f,*r,*d,*e;size_t fp,rp,ep;unsigned char**dv;size_t*dk;}dc;
  dc*X=calloc(MF?MF:1,sizeof(dc));char nm[64];size_t L;
  for(uint32_t j=0;j<MF;j++){dc*x=&X[j];x->type=m[mp++];
   if(x->type==0){x->SL=m[mp++];memcpy(x->S,m+mp,x->SL);mp+=x->SL;x->R=m[mp++];
    for(int q=0;q<x->R;q++){x->wm[q]=m[mp++];x->dl[q]=m[mp++];}
    snprintf(nm,64,"c%03u.x",j);if(ex(nm)){x->type=2;x->r=rf(nm,&L);continue;}
    for(int q=0;q<x->R;q++){snprintf(nm,64,"c%03u.n%02d",j,q);
     if(ex(nm))x->n[q]=rf(nm,&L);
     else{snprintf(nm,64,"c%03u.t%02d",j,q);size_t TL;unsigned char*t=rf(nm,&TL);buf vb={0};size_t p=0;
      while(p<TL){int neg=0;if(t[p]=='-'){neg=1;p++;}uint64_t a=0;while(t[p]!='\n')a=a*10+(t[p++]-'0');p++;pv(&vb,zz(neg?-(int64_t)a:(int64_t)a));}
      x->n[q]=vb.b?vb.b:(unsigned char*)calloc(1,1);free(t);}}
    snprintf(nm,64,"c%03u.f",j);x->f=rf(nm,&L);snprintf(nm,64,"c%03u.r",j);x->r=rf(nm,&L);}
   else if(x->type==1&&(snprintf(nm,64,"c%03u.x",j),ex(nm))){x->type=2;x->r=rf(nm,&L);}
   else if(x->type==1){snprintf(nm,64,"c%03u.d",j);size_t DL;unsigned char*d=rf(nm,&DL);snprintf(nm,64,"c%03u.e",j);x->e=rf(nm,&L);
    size_t cntv=0;for(size_t q=0;q<DL;q++)if(d[q]=='\n')cntv++;x->dv=malloc((cntv+1)*sizeof(char*));x->dk=malloc((cntv+1)*8);
    size_t s=0,id=0;for(size_t q=0;q<DL;q++)if(d[q]=='\n'){x->dv[id]=d+s;x->dk[id]=q-s;id++;s=q+1;}}
   else{snprintf(nm,64,"c%03u.r",j);x->r=rf(nm,&L);}}
  size_t hl;unsigned char*hd=rf("hdr",&hl);size_t cl;unsigned char*cb=rf("cnt",&cl);size_t cp=0;
  FILE*o=fopen(v[3],"wb");setvbuf(o,NULL,_IOFBF,1<<24);fwrite(hd,1,hl,o);if(hashdrnl)fputc('\n',o);
  char tmp[32];
  for(uint64_t li=0;li<NL;li++){uint64_t f=gv(cb,&cp);
   for(uint64_t j=0;j<f;j++){if(j)fputc(D,o);dc*x=&X[j];
    if(x->type==2){size_t q=x->rp;while(x->r[q]!='\n')q++;fwrite(x->r+x->rp,1,q-x->rp,o);x->rp=q+1;}
    else if(x->type==1){uint64_t id=gv(x->e,&x->ep);fwrite(x->dv[id],1,x->dk[id],o);}
    else{int fg=x->f[x->fp++];
     if(fg==2){}
     else if(fg==1){size_t q=x->rp;while(x->r[q]!='\n')q++;fwrite(x->r+x->rp,1,q-x->rp,o);x->rp=q+1;}
     else{if(fg==3)fputc('-',o);int q=0;for(int s=0;s<x->SL;s++){if(x->S[s]!=1){fputc(x->S[s],o);continue;}
       int64_t z=uz(gv(x->n[q],&x->np[q]));uint64_t val=x->dl[q]?x->prev[q]+(uint64_t)z:(uint64_t)z;x->prev[q]=val;
       int k=x->wm[q]?snprintf(tmp,32,"%0*llu",x->wm[q],(unsigned long long)val):snprintf(tmp,32,"%llu",(unsigned long long)val);fwrite(tmp,1,k,o);q++;}}}}
   if(li+1<NL||lastnl)fputc('\n',o);}
  fclose(o);}
 return 0;}
