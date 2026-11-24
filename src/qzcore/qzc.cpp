// qzcm — QZip-owned context-mixing coder (clean-room, written from public
// literature on CM: bitwise prediction, hashed context bit-histories,
// indirect state->probability maps, match model, gated logistic mixing, SSE).
// No code from paq/lpaq/mcm/cmix. License: GPL-3.0-or-later (QZip-authored).
// All arithmetic is integer => encoder/decoder are bit-exact across machines.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <vector>
#include <string>
#include <algorithm>
#include <sys/mman.h>
#include <x86intrin.h>
#ifdef QZPROF
static unsigned long long PT[10]; static unsigned long long pt0;
#define TS() (pt0=__rdtsc())
#define TE(i) do{ unsigned long long t=__rdtsc(); PT[i]+=t-pt0; pt0=t; }while(0)
#else
#define TS()
#define TE(i)
#endif
#include <functional>
typedef uint8_t U8; typedef uint16_t U16; typedef uint32_t U32; typedef uint64_t U64; typedef int64_t I64;

// ---------------- logistic tables ----------------
static short SQ[4096]; static short STR[4096];
static inline int squash(int d){ if(d>2047)d=2047; if(d<-2047)d=-2047; return SQ[d+2048]; }
static inline int stretch(int p){ return STR[p]; }
static void initLog(){
  for(int i=0;i<4096;i++){ double x=(i-2048)/256.0; int v=(int)floor(4096.0/(1.0+exp(-x))+0.5); if(v>4095)v=4095; if(v<1)v=1; SQ[i]=v; }
  int pi=0; for(int x=-2047;x<=2047;x++){ int v=squash(x); for(int j=pi;j<=v;j++) STR[j]=x; pi=v+1; }
  for(int j=pi;j<4096;j++) STR[j]=2047;
}

// ---------------- bit-history state machine (generated) ----------------
// state = (n0,n1) counts with nonstationary discounting of the opposite count.
static U8 NX[256][2]; static U8 SN0[256], SN1[256], DET[256]; static int NSTATES;
static void initStates(){
  int id[64][64]; memset(id,-1,sizeof id);
  std::vector<std::pair<int,int>> q; q.push_back({0,0}); id[0][0]=0;
  for(size_t k=0;k<q.size();k++){
    for(int y=0;y<2;y++){
      int a=q[k].first,b=q[k].second; // a=n0 b=n1
      if(y){ b++; if(a>2) a=(a+1)/2; } else { a++; if(b>2) b=(b+1)/2; }
      if(a==0){ if(b>40) b=40; } else if(b==0){ if(a>40) a=40; }
      else { if(a+b>17){ if(y){ b=17-a; if(b<1){b=1;a=16;} } else { a=17-b; if(a<1){a=1;b=16;} } } }
      if(id[a][b]<0){ id[a][b]=(int)q.size(); q.push_back({a,b}); }
      NX[k][y]=(U8)id[a][b];
    }
  }
  NSTATES=(int)q.size(); if(NSTATES>256){ fprintf(stderr,"too many states %d\n",NSTATES); exit(1); }
  for(int k=0;k<NSTATES;k++){ SN0[k]=q[k].first; SN1[k]=q[k].second; DET[k]= k>0 && (SN0[k]==0||SN1[k]==0); }
}

// ---------------- adaptive probability map ----------------
static int REC[1024];
struct SMap { // context -> p (22 bit) + count (10 bit)
  std::vector<U32> t; int lim; U32 *cur=nullptr;
  void init(size_t n,int limit,bool states=false){ t.assign(n,0); lim=limit;
    for(size_t i=0;i<n;i++){ U32 p=1u<<21; if(states){ int s=i&255; if(s<NSTATES){ p=(U32)(((SN1[s]*2+1)<<22)/(SN0[s]*2+SN1[s]*2+2)); } } t[i]=p<<10; } }
  inline int p(size_t cx){ cur=&t[cx]; return (int)(*cur>>20); }
  inline void upd(int y){ U32 v=*cur; int n=v&1023; int p=(int)(v>>10); int tg=y<<22; p+=(int)(((I64)(tg-p)*REC[n])>>16);
    if(p<0)p=0; if(p>(1<<22)-1)p=(1<<22)-1; if(n<lim)n++; *cur=((U32)p<<10)|n; }
};

// ---------------- APM / SSE ----------------
struct APM { std::vector<U16> t; int idx=0, rate;
  void init(int n,int r){ rate=r; t.resize((size_t)n*33); for(int i=0;i<n;i++) for(int j=0;j<33;j++) t[(size_t)i*33+j]=(U16)(squash((j-16)*128)*16); }
  inline int pp(int pr,int cx){ int s=stretch(pr)+2048; int lo=s>>7, w=s&127; idx=cx*33+lo; int p=(t[idx]*(128-w)+t[idx+1]*w)>>7; if(w>=64) idx++; if(p<1)p=1; if(p>65535)p=65535; return p; }
  inline void upd(int y){ int g=(y<<16)+(y<<rate)-y-y; t[idx]+=(g-t[idx])>>rate; }
};

// ---------------- mixer: shared inputs, several selectable weight sets ----------------
static int envi(const char*k,int d){ const char*v=getenv(k); return v?(int)strtol(v,0,0):d; }
struct Mixer {
  int n=0, nx=0; std::vector<int> x; 
  struct Set { std::vector<int> w; int *cw; int pr; int sel; int lr; };
  std::vector<Set> S;
  void init(int ninputs){ n=ninputs; x.assign(n+8,0); }
  void addset(int nsel,int lr,int initw){ Set s; s.w.assign((size_t)n*nsel,initw); s.lr=lr; s.cw=s.w.data(); s.pr=2048; s.sel=0; S.push_back(std::move(s)); }
  inline void add(int v){ x[nx++]=v; }
  inline int dot(const int*__restrict w){ int s=0; const int*__restrict xx=x.data(); const int N=nx; for(int i=0;i<N;i++) s+=(xx[i]*w[i])>>8; int d=s>>8; if(d>2047)d=2047; if(d<-2047)d=-2047; return d; }
  inline int mix(int k,int sel){ Set&s=S[k]; s.cw=&s.w[(size_t)sel*n]; int d=dot(s.cw); s.pr=squash(d); return d; }
  inline void upd(int y){ const int N=nx; const int*__restrict xx=x.data(); const int ns=(int)S.size();
    for(int k=0;k<ns;k++){ Set&s=S[k]; const int err=((y<<12)-s.pr)*s.lr; int*__restrict w=s.cw;
      for(int i=0;i<N;i++){ int v=w[i]+((xx[i]*err+(1<<15))>>16); v=v>524287?524287:v; v=v<-524287?-524287:v; w[i]=v; } } nx=0; }
};
// ---------------- arithmetic coder ----------------
struct Coder { U32 x1=0,x2=0xffffffff,x=0; std::vector<U8>*out; const U8*in; size_t ip=0,in_n=0; bool dec;
  void initEnc(std::vector<U8>*o){ out=o; dec=false; }
  void initDec(const U8*p,size_t n){ in=p; in_n=n; dec=true; for(int i=0;i<4;i++) x=(x<<8)|nextb(); }
  inline int nextb(){ return ip<in_n?in[ip++]:0; }
  inline int code(int y,int p){ // p = P(1) 16-bit (1..65535)
    U32 r=x2-x1; U32 xm=x1+(U32)(((U64)r*(U32)p)>>16);
    if(dec) y=x<=xm;
    if(y) x2=xm; else x1=xm+1;
    while(((x1^x2)&0xff000000)==0){ if(!dec) out->push_back(x2>>24); x1<<=8; x2=(x2<<8)|255; if(dec) x=(x<<8)|nextb(); }
    return y; }
  void flush(){ for(int i=0;i<4;i++){ out->push_back(x1>>24); x1<<=8; } }
};

static inline U64 hh(U64 a,U64 b){ U64 h=(a+0x9E3779B97F4A7C15ULL)*0xD6E8FEB86659FD93ULL; h^=h>>32; h=(h+b)*0x9E3779B97F4A7C15ULL; h^=h>>29; return h; }

static void thp(void*p,size_t n){ if(!envi("QZ_THP",1)||n<(4u<<20)) return; uintptr_t a=((uintptr_t)p+(1u<<21)-1)&~(uintptr_t)((1u<<21)-1), e=((uintptr_t)p+n)&~(uintptr_t)((1u<<21)-1); if(e>a) madvise((void*)a,e-a,MADV_HUGEPAGE); }
template<class V> static void thpv(V&v){ thp(v.data(),v.size()*sizeof(v[0])); }
// ---------------- hashed context table ----------------
struct Slot { U8 chk; U8 st[15]; };
struct HTable { Slot *t; U64 mask;
  void init(int bits){ size_t n=(size_t)1<<bits; size_t sz=n*sizeof(Slot); t=(Slot*)aligned_alloc(1<<21,sz<(1u<<21)?(1u<<21):sz); if(!t){fprintf(stderr,"oom\n");exit(1);}
    if(envi("QZ_THP",1)) madvise(t,sz,MADV_HUGEPAGE); memset(t,0,sz); mask=(n/4)-1; }
  inline Slot* bucket(U64 h){ return t+((h>>8)&mask)*4; }
  inline U8* find(U64 h){ Slot*b=bucket(h); U8 c=(U8)(h>>56); 
    for(int i=0;i<4;i++) if(b[i].chk==c) return b[i].st;
    int v=0,bp=999; for(int i=0;i<4;i++){ int pr=SN0[b[i].st[0]]+SN1[b[i].st[0]]; if(pr<bp){bp=pr;v=i;} }
    memset(&b[v],0,sizeof(Slot)); b[v].chk=c; return b[v].st; }
};

static void wrt_roles(const U8*t,U8 role[256]);
// model configuration: chosen by level at compress time, stored in the header, restored by the decoder
struct Cfg { U32 mask; U8 sets, apm, x2, f2, fin, lr, lrf; U16 slim; };
static Cfg level_cfg(int lv){ // 1 = fastest CM, 2 = balanced (default), 3 = full model
  Cfg c; memset(&c,0,sizeof c); c.apm=3; c.fin=1; c.lr=12; c.lrf=2; c.slim=1023;
  U32 trim=0x7ffffff & ~((1u<<24)|(1u<<25)|(1u<<15)|(1u<<9)|(1u<<18)|(1u<<6));
  if(lv<=1){ c.mask=trim; c.sets=0x65; c.x2=0; c.f2=0; }
  else if(lv==2){ c.mask=trim; c.sets=0x65; c.x2=1; c.f2=0; }
  else { c.mask=0x7ffffff; c.sets=0x7f; c.x2=1; c.f2=1; c.apm=0x1f; }  // L3 = every model, every weight set, all 5 APM stages
  if(getenv("QZ_MASK")) c.mask=(U32)strtoul(getenv("QZ_MASK"),0,0);
  c.sets=envi("QZ_SETS",c.sets); c.apm=envi("QZ_APM",c.apm); c.x2=envi("QZ_X2",c.x2); c.f2=envi("QZ_F2",c.f2);
  c.fin=envi("QZ_FIN",c.fin); c.lr=envi("QZ_LR",c.lr); c.lrf=envi("QZ_LRF",c.lrf); c.slim=envi("QZ_SLIM",c.slim); return c; }
static Cfg G=level_cfg(2);
// ---------------- predictor ----------------
enum { MAXM=40, NRUN=6 };
struct RunE { U8 chk, b, n, pad; };
struct Predictor {
  int nm=0; // hashed models
  HTable ht; U64 H[MAXM]; U8 *slot[MAXM]; SMap sm[MAXM];
  SMap o0, o1d, mm, rsm[NRUN]; Mixer M; Mixer F; APM a1,a2,a3,a4,a5;
  std::vector<RunE> rt; U64 rmask; RunE *re[NRUN]; int rbyte[NRUN], rcnt[NRUN];
  std::vector<U16> ind1, ind2; U32 i2h=0; std::vector<U32> wn; U64 num=0; int conf=0;
  int c0=1,bp=0; U32 c4=0,c8=0; U64 w0=0,w1=0,w2=0,w3=0; int inword=0; int col=0; U32 lastnl=0, prevnl=0;
  U32 mk=0; // markup state
  U64 rh6=0,rh12=0,rh20=0,rh24=0; U64 RB6,RB12,RB20,RB24; U32 MASK=0xffffffff, SETS=0x7f, APMS=0x1f; int X2=1;
  std::vector<U8> buf; U32 pos=0;
  std::vector<U32> mt1, mt2; U32 mptr=0; int mlen=0; int mbits=22;
  const U8 *role=nullptr; bool wrt=false; U8 rl[256];
  int codepend=0, escpend=0; U64 codeh=0, lastw1=0;
  int prf=32768, hits=0, ml=0; int NM_FIX=0; int OPT_FIN=0, OPT_F2=0, OPT_SMBP=0, OPT_LS=0, OPT_O2D=0; U32 lstart=0; SMap o2d; Mixer F2;
  void init(int hbits,size_t bufsize,const U8*r){
    wrt=(r!=nullptr); ht.init(hbits); buf.resize(bufsize+8);
    MASK=G.mask; SETS=G.sets; APMS=G.apm; X2=G.x2;
    { U64 b=1; for(int k=1;k<=24;k++){ b*=0x100000001B3ULL; if(k==6)RB6=b; if(k==12)RB12=b; if(k==20)RB20=b; if(k==24)RB24=b; } }
    int lim=G.slim;
    OPT_FIN=G.fin; OPT_F2=G.f2; OPT_LS=0; OPT_O2D=0; OPT_SMBP=0;
    for(int i=0;i<MAXM;i++) sm[i].init(256*8,lim,true);
    o0.init(256,1023); o1d.init(65536,1023); mm.init(64*2,1023); for(int i=0;i<NRUN;i++) rsm[i].init(64*2,1023);
    int rbits=22; rt.assign((size_t)1<<rbits,RunE{0,0,0,0}); rmask=((U64)1<<rbits)-1;
    ind1.assign(65536,0); ind2.assign(1<<22,0); wn.assign(1<<22,0);
    mt1.assign((size_t)1<<mbits,0); mt2.assign((size_t)1<<mbits,0);
    thpv(buf); thpv(rt); thpv(ind2); thpv(wn); thpv(mt1); thpv(mt2);
    update_ctx(); NM_FIX=nm;
    int ni=2*nm+NRUN+4+2; M.init(ni);
    int lr=G.lr, iw=1<<14;
    { int ns[7]={256,2048,2048,1024,2048,2048,128}; for(int k=0;k<7;k++) if(SETS>>k&1) M.addset(ns[k],lr,iw); }
    int nf=(int)M.S.size(); F.init(nf+1); F.addset(256,G.lrf,(1<<16)/nf); F2.init(nf+1); F2.addset(2048,G.lrf,(1<<16)/nf);
    a1.init(256,7); a2.init(65536,7); a3.init(65536,7); a4.init(64*256,7); a5.init(65536,7);
  }
  int rsel[NRUN]={1,2,3,5,11,12};
  inline int mlb(){ if(mlen==0) return 0; if(mlen<16) return mlen; if(mlen<32) return 16+((mlen-16)>>2); if(mlen<64) return 20+((mlen-32)>>3); if(mlen<512) return 24+((mlen-64)>>6); return 31; }
  void update_ctx(){
    U32 c1=c4&255; int i=0;
    H[i++]=hh(1,c4&0xff); H[i++]=hh(2,c4&0xffff); H[i++]=hh(3,c4&0xffffff); H[i++]=hh(4,c4);
    H[i++]=hh(5,hh(c4,c8&0xff)); H[i++]=hh(6,hh(c4,c8&0xffff)); H[i++]=hh(7,hh(c4,c8&0xffffff)); H[i++]=hh(8,hh(c4,c8));
    H[i++]=hh(9,rh12); H[i++]=hh(10,rh20);
    U64 wc = inword? w0 : hh(w0, c1);
    H[i++]=hh(11,wc); H[i++]=hh(12,hh(wc,w1)); H[i++]=hh(13,hh(hh(wc,w1),w2)); H[i++]=hh(14,hh(wc,w2)); H[i++]=hh(15,hh(hh(wc,w1),hh(w2,w3)));
    H[i++]=hh(16,hh(w1,c4&0xffff));
    H[i++]=hh(17,c4&0xff00ff00); H[i++]=hh(18,c4&0xffff0000); H[i++]=hh(19,(c4&0xff00)|(c8&0xff000000ULL));
    U32 ab=0; if(lastnl>0){ U32 q=prevnl+col; ab=(q<lastnl-1)?buf[q]:0; }
    H[i++]=hh(20,hh(ab,c1)); H[i++]=hh(21,hh(ab,col<64?col:64));
    // indirect: what followed the last occurrences of this bigram / trigram
    U16 t1=ind1[c4&0xffff]; U16 t2=ind2[(c4&0xffffff)*0x9E3779B1u>>10];
    H[i++]=hh(22,hh(c1,t1)); H[i++]=hh(23,hh(c4&0xffff,t2));
    // markup state + word
    H[i++]=hh(24,hh(mk,wc));
    H[i++]=hh(25,hh(wn[(w1*0x9E3779B97F4A7C15ULL)>>42],wc));
    H[i++]=hh(26,hh(num,w1));
    if(OPT_LS){ H[i++]=hh(27,hh(lstart,wc)); }
    { int j=0; for(int k=0;k<i;k++) if(MASK>>k&1) H[j++]=H[k]; nm=j; }
    for(int k=0;k<nm;k++) __builtin_prefetch(ht.bucket(H[k]));
    for(int k=0;k<nm;k++) slot[k]=ht.find(H[k]);
    // run model on selected contexts
    for(int r=0;r<NRUN;r++){ U64 h=H[rsel[r]<nm?rsel[r]:0]*0x9E3779B97F4A7C15ULL; RunE*e=&rt[(h>>20)&rmask]; U8 ck=(U8)(h>>56);
      if(e->chk==ck && e->n){ rbyte[r]=e->b; rcnt[r]=e->n; } else { rbyte[r]=-1; rcnt[r]=0; } re[r]=e; }
  }
  inline int pp(){
    int node = (bp<4)? ((1<<bp)-1 + (c0&((1<<bp)-1))) : ((1<<(bp-4))-1 + (c0&((1<<(bp-4))-1)));
    TS(); hits=0; conf=0;
    { int* __restrict xo=M.x.data()+M.nx; int j=0;
      for(int k=0;k<nm;k++){ int st=slot[k][node]; int pk=sm[k].p(st); int nz=st!=0; hits+=nz; conf=(nz&(k<10))? k+1:conf;
        xo[j++]=stretch(pk); if(X2) xo[j++]=((pk-2048)>>2)&-(int)DET[st]; }
      M.nx+=j; }
    TE(0);
    for(int r=0;r<NRUN;r++){ int x=0; if(rbyte[r]>=0 && ((rbyte[r]|256)>>(8-bp))==c0){ int e=(rbyte[r]>>(7-bp))&1; int n=rcnt[r]>31?31:rcnt[r]; x=stretch(rsm[r].p(n*2+e)); } else rsm[r].p(0); M.add(x); }
    M.add(stretch(o0.p(c0))); M.add(stretch(o1d.p(c0|((c4&255)<<8)))); if(OPT_O2D) M.add(stretch(o2d.p(c0|((c4&0xffff)<<8))));
    int mx_in=0; ml=0;
    if(mlen>0){ int eb=buf[mptr]; if(((eb|256)>>(8-bp))==c0){ int meb=(eb>>(7-bp))&1; ml=mlb(); mx_in=stretch(mm.p(ml*2+meb)); } else { mlen=0; mm.p(0); } } else mm.p(0);
    M.add(mx_in); M.add(256);
    TE(1);
    int c1=c4&255, c2=(c4>>8)&255;
    int hh5 = hits>31?31:hits;
    { int sel[7]={c0,(ml<<3)+bp,(c1<<3)+bp,(hh5<<5)|(ml>31?31:ml),(c2<<3)+bp,(int)((mk&255)<<3)+bp,(conf<<3)+bp}; int j=0;
      for(int k=0;k<7;k++) if(SETS>>k&1){ F.add(M.mix(j,sel[k])); j++; } }
    F.add(256);
    F.mix(0,c0); int p0=F.S[0].pr;
    if(OPT_F2){ for(int k=0;k<F.nx;k++) F2.add(F.x[k]); F2.mix(0,(c1<<3)+bp); p0=squash((stretch(p0)+stretch(F2.S[0].pr))>>1); }
    TE(2);
    int pf;
    if(APMS==0x1f && OPT_FIN<=3){
      int pa1=a1.pp(p0,c0), pa2=a2.pp(p0,c0|(c1<<8)), pa3=a3.pp(p0,(U32)(hh(c4&0xffffff,c0)>>48)), pa4=a4.pp(p0,(ml<<8)|c0), pa5=a5.pp(p0,(U32)(hh(wc_cur(),c0)>>48));
      if(OPT_FIN==0) pf=(p0*16*2+pa1+pa2*2+pa3+pa4+pa5)>>3;
      else if(OPT_FIN==1) pf=(p0*16+pa2+pa3+pa5)>>2;
      else if(OPT_FIN==2) pf=(p0*16*2+pa2*2+pa3*2+pa4+pa5)>>3;
      else pf=(p0*16*3+pa1+pa2+pa3+pa4+pa5)>>3;
    } else { int s=0,k=0;
      if(APMS&1){ s+=a1.pp(p0,c0); k++; } if(APMS&2){ s+=a2.pp(p0,c0|(c1<<8)); k++; } if(APMS&4){ s+=a3.pp(p0,(U32)(hh(c4&0xffffff,c0)>>48)); k++; }
      if(APMS&8){ s+=a4.pp(p0,(ml<<8)|c0); k++; } if(APMS&16){ s+=a5.pp(p0,(U32)(hh(wc_cur(),c0)>>48)); k++; }
      pf = k? (p0*16 + s/k)>>1 : p0*16; }
    if(pf<8)pf=8; if(pf>65535-8)pf=65535-8; prf=pf; TE(3); return prf;
  }
  inline U64 wc_cur(){ return inword? w0 : hh(w0, c4&255); }
  void update(int y){
    int node = (bp<4)? ((1<<bp)-1 + (c0&((1<<bp)-1))) : ((1<<(bp-4))-1 + (c0&((1<<(bp-4))-1)));
    TS();
    for(int k=0;k<nm;k++){ U8 &s=slot[k][node]; sm[k].upd(y); s=NX[s][y]; }
    TE(4);
    for(int r=0;r<NRUN;r++) rsm[r].upd(y);
    o0.upd(y); o1d.upd(y); if(OPT_O2D) o2d.upd(y); mm.upd(y);
    TE(5); M.upd(y); F.upd(y); if(OPT_F2) F2.upd(y); else F2.nx=0; if(APMS&1)a1.upd(y); if(APMS&2)a2.upd(y); if(APMS&4)a3.upd(y); if(APMS&8)a4.upd(y); if(APMS&16)a5.upd(y);
    TE(6); c0=(c0<<1)|y; bp++;
    if(bp==3){ for(int k=0;k<nm;k++){ __builtin_prefetch(ht.bucket(hh(H[k],c0<<1))); __builtin_prefetch(ht.bucket(hh(H[k],(c0<<1)|1))); } }
    if(bp==4){ for(int k=0;k<nm;k++) slot[k]=ht.find(hh(H[k],c0)); }
    TE(7);
    if(bp==8){ int c=c0&255; byte_update(c); c0=1; bp=0; }
    TE(8);
  }
  void byte_update(int c){
    // run table update (contexts of the byte just coded)
    for(int r=0;r<NRUN;r++){ RunE*e=re[r]; U8 ck=(U8)((H[rsel[r]<nm?rsel[r]:0]*0x9E3779B97F4A7C15ULL)>>56);
      if(e->chk==ck && e->b==c){ if(e->n<255) e->n++; } else { e->chk=ck; e->b=c; e->n=1; } }
    ind1[c4&0xffff]=(U16)((ind1[c4&0xffff]<<8)|c); { U32 k=(c4&0xffffff)*0x9E3779B1u>>10; ind2[k]=(U16)((ind2[k]<<8)|c); }
    buf[pos++]=c; if(wrt && !role && pos>=9 && pos==9u+buf[5]){ wrt_roles(buf.data(),rl); role=rl; }
    c8=(c8<<8)|(c4>>24); c4=(c4<<8)|c;
    { auto ob=[&](U32 k)->U64{ return pos>k? buf[pos-1-k]+1 : 0; }; const U64 B=0x100000001B3ULL; U64 cc=(U64)c+1;
      rh6=rh6*B+cc-ob(6)*RB6; rh12=rh12*B+cc-ob(12)*RB12; rh20=rh20*B+cc-ob(20)*RB20; rh24=rh24*B+cc-ob(24)*RB24; }
    bool letter=(c>='a'&&c<='z')||(c>='A'&&c<='Z'); bool used=false;
    if(role){
      if(escpend){ escpend=0; used=true; }
      else if(codepend>0){ codepend--; codeh=hh(codeh,c); used=true; if(codepend==0){ w3=w2; w2=w1; w1=codeh; w0=0; inword=0; } }
      else if(role[c]>=4 && role[c]<=6){ if(inword){ w3=w2; w2=w1; w1=w0; w0=0; inword=0; } codeh=hh(77,c); codepend=role[c]-4; used=true; if(codepend==0){ w3=w2; w2=w1; w1=codeh; w0=0; } }
      else if(role[c]==1){ escpend=1; used=true; }
      else if(role[c]) used=true;
    }
    if(!used){ if(letter){ w0=hh(w0+1,c|32); inword=1; } else if(inword){ w3=w2; w2=w1; w1=w0; w0=0; inword=0; } }
    if(w1!=lastw1){ wn[(w2*0x9E3779B97F4A7C15ULL)>>42]=(U32)w1; lastw1=w1; }
    if(c>='0'&&c<='9') num=hh(num+1,c); else if(c!='.'&&c!=',') num=0;
    // markup state: last structural opener and nesting hint
    if(c=='['||c=='{'||c=='<'||c=='|'||c=='='||c=='&'||c=='\n'||c=='('||c=='"'||c=='\'') mk=(mk<<8)|c; else if(c==']'||c=='}'||c=='>'||c==')') mk=(mk<<8)|c;
    mk&=0xffff;
    if(c=='\n'){ prevnl=lastnl; lastnl=pos; col=0; lstart=0; } else { col++; if(col<=2) lstart=(lstart<<8)|c; }
    if(mlen>0){ if(buf[mptr]==c){ mlen++; mptr++; if(mlen>65535) mlen=65535; } else mlen=0; }
    if(pos>=24){
      U32 i1=(U32)((rh6*0x9E3779B97F4A7C15ULL)>>(64-mbits)); U32 i2=(U32)((rh24*0xD6E8FEB86659FD93ULL)>>(64-mbits));
      if(mlen==0){ U32 cand=mt2[i2]; if(cand==0) cand=mt1[i1];
        if(cand>0){ int l=0; while(l<400 && cand>(U32)l && buf[cand-1-l]==buf[pos-1-l]) l++; if(l>=6){ mlen=l; mptr=cand; } } }
      mt1[i1]=pos; mt2[i2]=pos;
    }
    update_ctx();
  }
};
static void initAll(){ initLog(); initStates(); for(int i=0;i<1024;i++) REC[i]=(int)(65536.0/(i+1.6)); }

// ---------------- CM encode/decode of a buffer ----------------
static std::vector<U8> cm_encode(const U8*in,size_t n,int hbits,const U8*role){
  Predictor *P=new Predictor(); P->init(hbits,n,role);
  std::vector<U8> out; Coder C; C.initEnc(&out);
  for(size_t i=0;i<n;i++){ int c=in[i]; for(int b=7;b>=0;b--){ int y=(c>>b)&1; C.code(y,P->pp()); P->update(y); } }
  C.flush(); delete P;
#ifdef QZPROF
  { const char*nm[]={"models.p","run/o0/match","mix","apm","models.upd","small.upd","mixer.upd+apm.upd","nibble find","byte_update+ctx"}; double T=0; for(int i=0;i<9;i++) T+=PT[i]; for(int i=0;i<9;i++) fprintf(stderr,"%-20s %5.1f%%  %.1f cyc/byte\n",nm[i],100.0*PT[i]/T,(double)PT[i]/n); }
#endif
  return out;
}
static std::vector<U8> cm_decode(const U8*in,size_t inl,size_t n,int hbits,const U8*role){
  Predictor *P=new Predictor(); P->init(hbits,n,role);
  std::vector<U8> out(n); Coder C; C.initDec(in,inl);
  for(size_t i=0;i<n;i++){ int c=0; for(int b=0;b<8;b++){ int y=C.code(0,P->pp()); P->update(y); c=(c<<1)|y; } out[i]=c; }
  delete P; return out;
}


// ---------------- QZip word transform (qwrt v2 logic, in-process) ----------------
// roles: 1=ESC 2=CAP 3=UPPER 7=NOSPACE 4/5/6 = lead byte of 1/2/3-byte word code
struct WrtParams { long maxd=8000000, minf=4; int K1=100,K2=40,K3=8, SP=1; };
static inline int isL(int c){ return (c>='a'&&c<='z')||(c>='A'&&c<='Z'); }
static bool wrt_encode(const U8*in,size_t n,const WrtParams&P,std::vector<U8>&OB,U8 role[256]){
  struct E{ U32 off,len; U64 cnt; int32_t code; };
  std::vector<E> H((size_t)1<<22); size_t HC=H.size(), HN=0; std::vector<U8> pool;
  auto hw=[&](const U8*s,size_t L){ U64 h=1469598103934665603ULL; for(size_t i=0;i<L;i++){h^=s[i];h*=1099511628211ULL;} return h; };
  std::function<E*(const U8*,size_t,bool)> find;
  find=[&](const U8*s,size_t L,bool ins)->E*{
    if(ins && HN*2>=HC){ std::vector<E> o; o.swap(H); HC*=2; H.assign(HC,E{0,0,0,-1}); for(auto&e:o) if(e.len){ size_t i=hw(pool.data()+e.off,e.len)&(HC-1); while(H[i].len) i=(i+1)&(HC-1); H[i]=e; } }
    size_t i=hw(s,L)&(HC-1);
    while(H[i].len){ if(H[i].len==L && !memcmp(pool.data()+H[i].off,s,L)) return &H[i]; i=(i+1)&(HC-1); }
    if(!ins) return nullptr; H[i].off=pool.size(); H[i].len=L; H[i].cnt=0; H[i].code=-1; pool.insert(pool.end(),s,s+L); HN++; return &H[i]; };
  for(auto&e:H){ e.len=0; e.code=-1; }
  std::vector<U8> tmp; U64 hist[256]={0};
  auto cls=[&](size_t i,size_t j,int&low,int&cap,int&up){ low=1;cap=(in[i]>='A'&&in[i]<='Z');up=1; tmp.resize(j-i);
    for(size_t k=i;k<j;k++){ int ch=in[k]; tmp[k-i]=(ch>='A'&&ch<='Z')?ch+32:ch; if(!(ch>='a'&&ch<='z')) low=0; if(!(ch>='A'&&ch<='Z')) up=0; if(k>i&&!(ch>='a'&&ch<='z')) cap=0; } };
  for(size_t i=0;i<n;){ if(isL(in[i])){ size_t j=i; while(j<n&&isL(in[j])) j++; if(j-i>=2 && j-i<(1<<16)){ int low,cap,up; cls(i,j,low,cap,up); if(low||cap||up) find(tmp.data(),j-i,true)->cnt++; } i=j; } else hist[in[i++]]++; }
  std::vector<E*> c; for(auto&e:H) if(e.len && (long)e.cnt>=P.minf) c.push_back(&e);
  std::sort(c.begin(),c.end(),[](E*x,E*y){ if(x->cnt!=y->cnt) return x->cnt>y->cnt; if(x->len!=y->len) return x->len>y->len; return x->off<y->off; });
  int ord[256],no=0; for(int b=0;b<256;b++){ if(isL(b)||(b>='0'&&b<='9')||b=='\n'||b==' ') continue; ord[no++]=b; }
  std::stable_sort(ord,ord+no,[&](int a,int b){ return hist[a]<hist[b]; });
  int NF=P.SP?4:3, K=NF+P.K1+P.K2+P.K3; if(K>no) return false;
  size_t cap1=P.K1, cap2=cap1+(size_t)P.K2*256, cap3=cap2+(size_t)P.K3*65536;
  size_t nd=std::min(c.size(),(size_t)std::min((size_t)P.maxd,cap3)); size_t w=0;
  for(size_t j=0;j<c.size()&&w<nd;j++){ size_t cl=w<cap1?1:(w<cap2?2:3); if(c[j]->len<=cl) continue; c[w++]=c[j]; } nd=w;
  for(size_t j=0;j<nd;j++) c[j]->code=(int32_t)j;
  U8 res[256]; memset(role,0,256); for(int k=0;k<K;k++) res[k]=ord[k];
  role[res[0]]=1; role[res[1]]=2; role[res[2]]=3; if(P.SP) role[res[3]]=7;
  for(int k=0;k<P.K1;k++) role[res[NF+k]]=4; for(int k=0;k<P.K2;k++) role[res[NF+P.K1+k]]=5; for(int k=0;k<P.K3;k++) role[res[NF+P.K1+P.K2+k]]=6;
  U8 ESC=res[0],CAP=res[1],UP=res[2],NS=res[3]; const U8*C1=res+NF,*C2=res+NF+P.K1,*C3=res+NF+P.K1+P.K2;
  OB.clear(); auto put=[&](U8 b){ OB.push_back(b); }; auto put32=[&](U32 v){ for(int i=0;i<4;i++) put((v>>(8*i))&255); };
  put('Q');put('W');put('R');put('T');put(P.SP?2:1);put(K);put(P.K1);put(P.K2);put(P.K3); for(int k=0;k<K;k++) put(res[k]);
  size_t dl=0; for(size_t j=0;j<nd;j++) dl+=c[j]->len+1; put32(nd); put32(dl);
  for(size_t j=0;j<nd;j++){ for(U32 k=0;k<c[j]->len;k++) put(pool[c[j]->off+k]); put('\n'); }
  size_t hdr=OB.size();
  for(size_t i=0;i<n;){
    if(isL(in[i])){ size_t j=i; while(j<n&&isL(in[j])) j++; bool done=false;
      if(j-i>=2 && j-i<(1<<16)){ int low,cap,up; cls(i,j,low,cap,up);
        if(low||cap||up){ E*e=find(tmp.data(),j-i,false); if(e&&e->code>=0){ size_t x=e->code;
          if(P.SP){ if(i>0&&in[i-1]==' '&&OB.size()>hdr&&OB.back()==' ') OB.pop_back(); else put(NS); }
          if(!low) put(cap?CAP:UP);
          if(x<cap1) put(C1[x]); else if(x<cap2){ x-=cap1; put(C2[x>>8]); put(x&255);} else { x-=cap2; put(C3[x>>16]); put((x>>8)&255); put(x&255);} done=true; } } }
      if(!done) for(size_t k=i;k<j;k++) put(in[k]); i=j;
    } else { U8 b=in[i++]; if(role[b]) put(ESC); put(b); }
  }
  return true;
}
static bool wrt_decode(const U8*in,size_t n,std::vector<U8>&OB){
  if(n<9||memcmp(in,"QWRT",4)) return false; int ver=in[4], NF=ver==2?4:3; size_t p=5; int K=in[p++],K1=in[p++],K2=in[p++],K3=in[p++];
  U8 res[256]; memcpy(res,in+p,K); p+=K;
  auto g32=[&](size_t q){ return (U32)in[q]|(U32)in[q+1]<<8|(U32)in[q+2]<<16|(U32)in[q+3]<<24; };
  U32 nd=g32(p); p+=4; U32 dl=g32(p); p+=4; std::vector<U32> wo(nd+1),wl(nd+1); size_t q=p;
  for(U32 j=0;j<nd;j++){ wo[j]=q; while(in[q]!='\n') q++; wl[j]=q-wo[j]; q++; } p+=dl;
  int role[256],idx[256]; memset(role,0,sizeof role); role[res[0]]=1; role[res[1]]=2; role[res[2]]=3; if(ver==2) role[res[3]]=7;
  for(int k=0;k<K1;k++){ role[res[NF+k]]=4; idx[res[NF+k]]=k; } for(int k=0;k<K2;k++){ role[res[NF+K1+k]]=5; idx[res[NF+K1+k]]=k; } for(int k=0;k<K3;k++){ role[res[NF+K1+K2+k]]=6; idx[res[NF+K1+K2+k]]=k; }
  size_t cap1=K1, cap2=cap1+(size_t)K2*256; int mode=0,ns=0; OB.clear();
  while(p<n){ U8 b=in[p++]; int r=role[b];
    if(r==0){ OB.push_back(b); continue; } if(r==1){ OB.push_back(in[p++]); continue; }
    if(r==7){ ns=1; continue; } if(r==2){ mode=1; continue; } if(r==3){ mode=2; continue; }
    size_t x; if(r==4) x=idx[b]; else if(r==5){ x=cap1+((size_t)idx[b]<<8)+in[p]; p++; } else { x=cap2+((size_t)idx[b]<<16)+((size_t)in[p]<<8)+in[p+1]; p+=2; }
    if(ver==2&&!ns) OB.push_back(' ');
    for(U32 k=0;k<wl[x];k++){ int ch=in[wo[x]+k]; if(mode==2||(mode==1&&k==0)) ch-=32; OB.push_back(ch); } mode=0; ns=0; }
  return true;
}
static void wrt_roles(const U8*t,U8 role[256]){ // rebuild role table from a QWRT header
  memset(role,0,256); int ver=t[4],NF=ver==2?4:3; int K=t[5],K1=t[6],K2=t[7],K3=t[8]; const U8*res=t+9; (void)K;
  role[res[0]]=1; role[res[1]]=2; role[res[2]]=3; if(ver==2) role[res[3]]=7;
  for(int k=0;k<K1;k++) role[res[NF+k]]=4; for(int k=0;k<K2;k++) role[res[NF+K1+k]]=5; for(int k=0;k<K3;k++) role[res[NF+K1+K2+k]]=6; }
static int text_like(const U8*in,size_t n){ size_t L=0,m=n<(1<<24)?n:(1<<24); for(size_t i=0;i<m;i++) L+=isL(in[i]); return m>=4096 && L*100>=m*45; }

// ---------------- file I/O ----------------
static std::vector<U8> readf(const char*p){ FILE*f=fopen(p,"rb"); if(!f){perror(p);exit(1);} std::vector<U8> v; fseek(f,0,SEEK_END); long s=ftell(f); fseek(f,0,SEEK_SET); v.resize(s); if(s&&fread(v.data(),1,s,f)!=(size_t)s){perror("read");exit(1);} fclose(f); return v; }
static void writef(const char*p,const std::vector<U8>&v){ FILE*f=fopen(p,"wb"); fwrite(v.data(),1,v.size(),f); fclose(f); }
static void put64(std::vector<U8>&o,U64 v){ for(int i=0;i<8;i++) o.push_back((v>>(8*i))&255); }
static U64 get64(const U8*p){ U64 v=0; for(int i=7;i>=0;i--) v=(v<<8)|p[i]; return v; }

#ifndef QZ_NO_MAIN
int main(int argc,char**argv){
  initAll();
  if(argc<4){ fprintf(stderr,"qzc c|d in out [hbits] [wrt:-1auto,0,1] [level 0=auto(L3 <=64MiB, else L2),1-3]\n"); return 1; }
  int hbits = argc>4? atoi(argv[4]) : 0; int wmode = argc>5? atoi(argv[5]) : 0; int level = argc>6? atoi(argv[6]) : 0;
  std::vector<U8> in=readf(argv[2]);
  if(level<=0) level = in.size() <= (64u<<20) ? 3 : 2;  // auto: full model when the input is small enough that its cost is seconds
  if(argv[1][0]=='c'){
    if(hbits<=0){ // auto: ~64 bytes of context table per input byte, capped at half of MemAvailable
      long long avail=0; FILE*mf=fopen("/proc/meminfo","r"); char ln[256]; while(mf&&fgets(ln,256,mf)) if(!strncmp(ln,"MemAvailable:",13)) avail=atoll(ln+13)*1024; if(mf) fclose(mf);
      hbits=16; while(hbits<34 && ((long long)16<<hbits) < (long long)in.size()*64) hbits++;
      while(hbits>16 && avail>0 && ((long long)16<<hbits) > avail/2) hbits--; fprintf(stderr,"hbits %d (%.1f GB table)\n",hbits,(16.0*(1ull<<hbits))/1e9); }
    G=level_cfg(level); std::vector<U8> o={'Q','Z','C',3,(U8)hbits}; put64(o,in.size());
    { U8 cb[sizeof(Cfg)]; memcpy(cb,&G,sizeof(Cfg)); o.insert(o.end(),cb,cb+sizeof(Cfg)); }
    U8 role[256]; std::vector<U8> t; bool useW = wmode==1 || (wmode<0 && text_like(in.data(),in.size()));
    if(useW){ WrtParams P; if(getenv("QZ_SP")) P.SP=atoi(getenv("QZ_SP")); if(getenv("QZ_MINF")) P.minf=atoi(getenv("QZ_MINF")); if(getenv("QZ_K1")) P.K1=atoi(getenv("QZ_K1")); if(getenv("QZ_K2")) P.K2=atoi(getenv("QZ_K2"));
      useW = wrt_encode(in.data(),in.size(),P,t,role); }
    o.push_back(useW?1:0);
    const U8*src=in.data(); size_t sn=in.size();
    if(useW){ put64(o,t.size()); src=t.data(); sn=t.size(); fprintf(stderr,"wrt %zu -> %zu\n",in.size(),t.size()); }
    std::vector<U8> e=cm_encode(src,sn,hbits,useW?role:nullptr); o.insert(o.end(),e.begin(),e.end()); writef(argv[3],o);
    fprintf(stderr,"%zu -> %zu\n",in.size(),o.size());
  } else {
    if(in.size()<14+sizeof(Cfg)||memcmp(in.data(),"QZC",3)||in[3]!=3){ fprintf(stderr,"bad\n"); return 1; }
    int hb=in[4]; U64 n=get64(in.data()+5); memcpy(&G,in.data()+13,sizeof(Cfg)); size_t p=13+sizeof(Cfg); int useW=in[p++]; U64 sn=n;
    if(useW){ sn=get64(in.data()+p); p+=8; }
    static U8 role_dummy[256];
    if(!useW){ std::vector<U8> d=cm_decode(in.data()+p,in.size()-p,sn,hb,nullptr); writef(argv[3],d); }
    else { // role table is known only after the QWRT header is decoded: decode with lazy roles
      std::vector<U8> d=cm_decode(in.data()+p,in.size()-p,sn,hb,(const U8*)role_dummy); std::vector<U8> o; if(!wrt_decode(d.data(),d.size(),o)){ fprintf(stderr,"wrt decode fail\n"); return 1; }
      if(o.size()!=n){ fprintf(stderr,"size mismatch\n"); return 1; } writef(argv[3],o); }
  }
  return 0;
}
#endif
