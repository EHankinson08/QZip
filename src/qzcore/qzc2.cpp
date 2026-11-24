// qzcm — QZip-owned context-mixing coder (clean-room, written from public
// literature on CM: bitwise prediction, hashed context bit-histories,
// indirect state->probability maps, match model, gated logistic mixing, SSE).
// No code from paq/lpaq/paq8/mcm/cmix/zpaq. License: GPL-3.0-or-later (QZip-authored).
// All arithmetic is integer => encoder/decoder are bit-exact across machines.
// The AVX2 mixer path computes exactly the same int32 results as the scalar path.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <cmath>
#include <vector>
#include <string>
#include <algorithm>
#include <functional>
#include <thread>
#ifdef __AVX2__
#include <immintrin.h>
#endif
#ifdef __linux__
#include <sys/mman.h>
#include <unistd.h>
#endif
typedef uint8_t U8; typedef uint16_t U16; typedef uint32_t U32; typedef uint64_t U64; typedef int64_t I64;

// ---------------- large zeroed allocations (huge pages when the OS offers them) ----------------
// Big random-access tables are TLB-bound with 4 KiB pages; THP via madvise removes most page walks.
static void* big_alloc(size_t bytes){
#ifdef __linux__
  size_t align=(size_t)2<<20; size_t sz=(bytes+align-1)&~(align-1);
  void*p=mmap(nullptr,sz,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
  if(p==MAP_FAILED){ fprintf(stderr,"oom (%zu MB)\n",bytes>>20); exit(1); }
#ifdef MADV_HUGEPAGE
  madvise(p,sz,MADV_HUGEPAGE);
#endif
  // A first *read* of a region maps the shared huge zero page and the later write splits it into 4 KiB
  // pages, so write-touch every 2 MiB region once: the fault then allocates a real huge page.
  for(size_t i=0;i<sz;i+=align) ((volatile char*)p)[i]=0;
  return p;
#else
  void*p=calloc(1,bytes); if(!p){ fprintf(stderr,"oom (%zu MB)\n",bytes>>20); exit(1); } return p;
#endif
}
static void big_free(void*p,size_t bytes){
#ifdef __linux__
  size_t align=(size_t)2<<20; size_t sz=(bytes+align-1)&~(align-1); if(p) munmap(p,sz);
#else
  free(p);
#endif
}
template<class T> static T* big_array(size_t n){ return (T*)big_alloc(n*sizeof(T)); }
#ifdef _WIN32
#include <malloc.h>
static inline void afree(void*p){ _aligned_free(p); }   // Windows has no aligned_alloc; _aligned_malloc needs _aligned_free
#define QZ_ALIGNED_ALLOC(a,b) _aligned_malloc(b,a)
#else
static inline void afree(void*p){ free(p); }
#define QZ_ALIGNED_ALLOC(a,b) aligned_alloc(a,b)
#endif
static void* aligned_zalloc(size_t bytes){ bytes=(bytes+63)&~(size_t)63; void*p=QZ_ALIGNED_ALLOC(64,bytes); if(!p){fprintf(stderr,"oom\n");exit(1);} memset(p,0,bytes); return p; }

// ---------------- logistic tables ----------------
static short SQ[4096]; static short STR[4096]; static int STR32[4096]; // STR32: int copy of STR for AVX2 gathers
static inline int squash(int d){ if(d>2047)d=2047; if(d<-2047)d=-2047; return SQ[d+2048]; }
static inline int squash_c(int d){ return SQ[d+2048]; } // d already clamped to +-2047
static inline int stretch(int p){ return STR[p]; }
static void initLog(){
  for(int i=0;i<4096;i++){ double x=(i-2048)/256.0; int v=(int)floor(4096.0/(1.0+exp(-x))+0.5); if(v>4095)v=4095; if(v<1)v=1; SQ[i]=v; }
  int pi=0; for(int x=-2047;x<=2047;x++){ int v=squash(x); for(int j=pi;j<=v;j++) STR[j]=x; pi=v+1; }
  for(int j=pi;j<4096;j++) STR[j]=2047;
  for(int j=0;j<4096;j++) STR32[j]=STR[j];
}

// ---------------- bit-history state machine (generated) ----------------
// state = (n0,n1) counts with nonstationary discounting of the opposite count.
static U8 NX[256][2]; static U8 SN0[256], SN1[256]; static int NSTATES;
static U8 SDET[256]; // 1 if one of the counts is zero (deterministic history)
static int SX2M[256]; // -1 if state is nonzero and deterministic (enables the confidence input), else 0
static U8 SPRI[256]; // n0+n1, replacement priority
static int STVAR=-1;
static void initStates(int fix=0){ STVAR=fix;
  int id[64][64]; memset(id,-1,sizeof id);
  std::vector<std::pair<int,int>> q; q.push_back({0,0}); id[0][0]=0;
  for(size_t k=0;k<q.size();k++){
    for(int y=0;y<2;y++){
      int a=q[k].first,b=q[k].second; // a=n0 b=n1
      if(y){ b++; if(a>2) a=(a+1)/2; else if(fix && ((a==2 && b>=6) || (a==1 && b>=16))) a--; } else { a++; if(b>2) b=(b+1)/2; else if(fix && ((b==2 && a>=6) || (b==1 && a>=16))) b--; }
      if(a==0){ if(b>40) b=40; } else if(b==0){ if(a>40) a=40; }
      else { if(a+b>17){ if(y){ b=17-a; if(b<1){b=1;a=16;} } else { a=17-b; if(a<1){a=1;b=16;} } } }
      if(id[a][b]<0){ id[a][b]=(int)q.size(); q.push_back({a,b}); }
      NX[k][y]=(U8)id[a][b];
    }
  }
  NSTATES=(int)q.size(); if(NSTATES>256){ fprintf(stderr,"too many states %d\n",NSTATES); exit(1); }
  for(int k=0;k<NSTATES;k++){ SN0[k]=q[k].first; SN1[k]=q[k].second; SDET[k]=(SN0[k]==0||SN1[k]==0); SPRI[k]=SN0[k]+SN1[k]; }
  for(int k=NSTATES;k<256;k++){ SDET[k]=1; SPRI[k]=0; }
  for(int k=0;k<256;k++) SX2M[k]=(k!=0&&SDET[k])?-1:0;
}

// ---------------- adaptive probability map ----------------
static int REC[1024];
struct SMap { // context -> p (22 bit) + count (10 bit)
  U32 *t=nullptr; size_t n=0; int lim; U32 *cur=nullptr; bool own=true;
  void init(size_t nn,int limit,bool states=false){ n=nn; t=(U32*)aligned_zalloc(n*4); lim=limit;
    for(size_t i=0;i<n;i++){ U32 p=1u<<21; if(states){ int s=i&255; if(s<NSTATES){ p=(U32)(((SN1[s]*2+1)<<22)/(SN0[s]*2+SN1[s]*2+2)); } } t[i]=p<<10; } }
  ~SMap(){ if(own) afree(t); }
  void alias(const SMap&o){ if(own) afree(t); t=o.t; n=o.n; lim=o.lim; own=false; }
  inline int p(size_t cx){ cur=&t[cx]; return (int)(*cur>>20); }
  // REC[n] <= 65536/1.6, so p moves at most 62.5% toward the target and never leaves [0, 2^22): no clamp needed
  static inline void upd_at(U32*e,int y,int lim){ U32 v=*e; int n=v&1023; int p=(int)(v>>10); int tg=y<<22; p+=(int)(((I64)(tg-p)*REC[n])>>16);
    n+=(n<lim); *e=((U32)p<<10)|n; }
  inline void upd(int y){ upd_at(cur,y,lim); }
};

// ---------------- APM / SSE ----------------
struct APM { U16 *t=nullptr; size_t sz=0; int idx=0, rate; bool own=true;
  void init(int n,int r){ rate=r; sz=(size_t)n*33*2; t=big_array<U16>((size_t)n*33); for(int i=0;i<n;i++) for(int j=0;j<33;j++) t[(size_t)i*33+j]=(U16)(squash((j-16)*128)*16); }
  ~APM(){ if(own) big_free(t,sz); }
  void alias(const APM&o){ if(own) big_free(t,sz); t=o.t; sz=o.sz; rate=o.rate; own=false; }
  inline int pp(int pr,int cx){ return pps(stretch(pr),cx); }
  inline int pps(int sv,int cx){ int s=sv+2048; int lo=s>>7, w=s&127; idx=cx*33+lo; int p=(t[idx]*(128-w)+t[idx+1]*w)>>7; if(w>=64) idx++; if(p<1)p=1; if(p>65535)p=65535; return p; }
  inline void upd(int y){ int g=(y<<16)+(y<<rate)-y-y; t[idx]+=(g-t[idx])>>rate; }
};

// ---------------- mixer: shared inputs, several selectable weight sets ----------------
// dot:    d = clamp( sum_i (x_i*w_i)>>8 ) >> 8
// train:  w_i += (x_i*err + 2^15) >> 16, clamped to +-(2^19-1)
// Inputs are padded with zeros to a multiple of 8 so the SIMD and scalar paths are identical.
#ifdef __AVX2__
// 8-lane table lookup. Hardware gathers are fast on recent big cores and slow on some small cores (Gracemont):
// -DQZ_NOGATHER uses 8 scalar loads instead (same values).
#ifdef QZ_NOGATHER
static inline __m256i qz_gather(const int*b,__m256i ix){ alignas(32) int i[8]; _mm256_store_si256((__m256i*)i,ix); return _mm256_setr_epi32(b[i[0]],b[i[1]],b[i[2]],b[i[3]],b[i[4]],b[i[5]],b[i[6]],b[i[7]]); }
#define QZ_GATHER(b,ix,sc) qz_gather((const int*)(b),ix)
#else
#define QZ_GATHER(b,ix,sc) _mm256_i32gather_epi32((const int*)(b),ix,sc)
#endif
#endif
static int envi(const char*k,int d){ const char*v=getenv(k); return v?atoi(v):d; }
static inline int mix_dot(const int*x,const int*w,int n){
  int s=0;
#ifdef __AVX2__
  __m256i acc=_mm256_setzero_si256();
  for(int i=0;i<n;i+=8){ __m256i p=_mm256_mullo_epi32(_mm256_load_si256((const __m256i*)(x+i)),_mm256_load_si256((const __m256i*)(w+i))); acc=_mm256_add_epi32(acc,_mm256_srai_epi32(p,8)); }
  __m128i h=_mm_add_epi32(_mm256_castsi256_si128(acc),_mm256_extracti128_si256(acc,1)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0x4e)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0xb1)); s=_mm_cvtsi128_si32(h);
#else
  for(int i=0;i<n;i++) s+=(x[i]*w[i])>>8;
#endif
  int d=s>>8; if(d>2047)d=2047; if(d<-2047)d=-2047; return d;
}
static inline void mix_train(const int*x,int*w,int n,int err){
#ifdef __AVX2__
  __m256i e=_mm256_set1_epi32(err), r=_mm256_set1_epi32(1<<15), hi=_mm256_set1_epi32(524287), lo=_mm256_set1_epi32(-524287);
  for(int i=0;i<n;i+=8){ __m256i p=_mm256_mullo_epi32(_mm256_load_si256((const __m256i*)(x+i)),e); p=_mm256_srai_epi32(_mm256_add_epi32(p,r),16);
    __m256i v=_mm256_add_epi32(_mm256_load_si256((const __m256i*)(w+i)),p); v=_mm256_max_epi32(_mm256_min_epi32(v,hi),lo); _mm256_store_si256((__m256i*)(w+i),v); }
#else
  for(int i=0;i<n;i++){ int v=w[i]+((x[i]*err+(1<<15))>>16); v=v>524287?524287:v; v=v<-524287?-524287:v; w[i]=v; }
#endif
}
struct Mixer {
  int n=0, nx=0; int *x=nullptr; bool own=true; int gm=16; // gm unused in the int32 mixer
  struct Set { int *w; int *cw; int pr; int lr; };
  std::vector<Set> S;
  void init(int ninputs){ n=(ninputs+7)&~7; x=(int*)aligned_zalloc((size_t)n*4); }
  ~Mixer(){ afree(x); if(own) for(auto&s:S) afree(s.w); }
  void alias(const Mixer&o){ if(own) for(auto&s:S) afree(s.w); S=o.S; for(auto&s:S){ s.cw=s.w; s.pr=2048; } own=false; }
  void addset(int nsel,int lr,int initw){ Set s; s.w=(int*)aligned_zalloc((size_t)n*nsel*4); for(size_t i=0;i<(size_t)n*nsel;i++) s.w[i]=initw; s.lr=lr; s.cw=s.w; s.pr=2048; S.push_back(s); }
  inline void add(int v){ x[nx++]=v; }
  inline int mix(int k,int sel){ Set&s=S[k]; s.cw=s.w+(size_t)sel*n; int d=mix_dot(x,s.cw,n); s.pr=squash_c(d); return d; }
  inline void upd(int y){ const int*xx=x; int nn=n; for(auto&s:S){ int err=((y<<12)-s.pr)*s.lr; mix_train(xx,s.cw,nn,err); } nx=0; }
};

// ---------------- 16-bit mixer (default; -DQZ_MIX32 selects the int32 mixer above) ----------------
// weights: int16 fixed point with QW fraction bits; inputs: int16 stretch values (|x|<=2047)
// dot:   d = ( sum over pairs ((x0*w0+x1*w1)>>4) ) >> (QW-4), clamped to +-2047
// train: w += (x*e + 2^14) >> 15 with saturation, e = (err*lr) >> (17-QW)  (same real-valued step as the int32 mixer)
#ifndef QZ_QW
#define QZ_QW 15
#endif
static inline int mix16_dot(const int16_t*x,const int16_t*w,int n,int qw=QZ_QW){
  int s=0;
#ifdef __AVX2__
  __m256i acc=_mm256_setzero_si256();
  for(int i=0;i<n;i+=16){ __m256i p=_mm256_madd_epi16(_mm256_load_si256((const __m256i*)(x+i)),_mm256_load_si256((const __m256i*)(w+i))); acc=_mm256_add_epi32(acc,_mm256_srai_epi32(p,4)); }
  __m128i h=_mm_add_epi32(_mm256_castsi256_si128(acc),_mm256_extracti128_si256(acc,1)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0x4e)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0xb1)); s=_mm_cvtsi128_si32(h);
#else
  for(int i=0;i<n;i+=2) s+=((int)x[i]*w[i]+(int)x[i+1]*w[i+1])>>4;
#endif
  int d=s>>(qw-4); if(d>2047)d=2047; if(d<-2047)d=-2047; return d;
}
static inline void mix16_train(const int16_t*x,int16_t*w,int n,int e){
#ifdef __AVX2__
  __m256i ev=_mm256_set1_epi16((short)e);
  for(int i=0;i<n;i+=16){ __m256i d=_mm256_mulhrs_epi16(_mm256_load_si256((const __m256i*)(x+i)),ev);
    _mm256_store_si256((__m256i*)(w+i),_mm256_adds_epi16(_mm256_load_si256((const __m256i*)(w+i)),d)); }
#else
  for(int i=0;i<n;i++){ int d=((int)x[i]*e+(1<<14))>>15; int v=w[i]+d; v=v>32767?32767:v; v=v<-32768?-32768:v; w[i]=(int16_t)v; }
#endif
}
struct Mixer16 {
  int n=0, nx=0; int16_t *x=nullptr; bool own=true; int qw=QZ_QW; // qw: weight fraction bits (v6 flag 2048 -> 14 for the first layer)
  struct Set { int16_t *w; int16_t *cw; int pr; int lr; };
  std::vector<Set> S;
  void init(int ninputs){ n=(ninputs+15)&~15; x=(int16_t*)aligned_zalloc((size_t)n*2); }
  ~Mixer16(){ afree(x); if(own) for(auto&s:S) afree(s.w); }
  void alias(const Mixer16&o){ if(own) for(auto&s:S) afree(s.w); S=o.S; for(auto&s:S){ s.cw=s.w; s.pr=2048; } own=false; }
  // initw is given in the int32 mixer's Q16 scale
  void addset(int nsel,int lr,int initw){ Set s; s.w=(int16_t*)aligned_zalloc((size_t)n*nsel*2); int16_t iw=(int16_t)(initw>>(16-qw)); for(size_t i=0;i<(size_t)n*nsel;i++) s.w[i]=iw; s.lr=lr; s.cw=s.w; s.pr=2048; S.push_back(s); }
  inline void add(int v){ x[nx++]=(int16_t)v; }
  inline int mix(int k,int sel){ Set&s=S[k]; s.cw=s.w+(size_t)sel*n; int d=mix16_dot(x,s.cw,n,qw); s.pr=squash_c(d); return d; }
  int gm=16; // learning-rate multiplier in 1/16 (16 = the set's own lr)
  int met=0; // v6 bits 20-22: skip the whole first-layer update when every set's |error| (12-bit) is below met (7-set mixer only)
  inline void upd(int y){ const int16_t*xx=x; int nn=n;
#ifdef __AVX2__
    if(S.size()==7){ upd_fused<7>(y); nx=0; return; }
#else
    if(S.size()==7 && met){ int mx=0; for(int k=0;k<7;k++){ int a=(y<<12)-S[k].pr; if(a<0)a=-a; if(a>mx)mx=a; } if(mx<met){ nx=0; return; } }
#endif
    for(auto&s:S){ int e=(((y<<12)-s.pr)*s.lr*gm)>>(17-qw+4); if(e>32767)e=32767; if(e<-32767)e=-32767; mix16_train(xx,s.cw,nn,e); } nx=0; }
#ifdef __AVX2__
  // all K weight sets in one pass over the inputs (same integer results as K separate mix()/train calls)
  template<int K> inline void mix_fused(const int*sel,int*out){
    const int16_t*w[K]; for(int k=0;k<K;k++){ S[k].cw=S[k].w+(size_t)sel[k]*n; w[k]=S[k].cw; }
    __m256i acc[K]; for(int k=0;k<K;k++) acc[k]=_mm256_setzero_si256();
    for(int i=0;i<n;i+=16){ __m256i xv=_mm256_load_si256((const __m256i*)(x+i));
      for(int k=0;k<K;k++) acc[k]=_mm256_add_epi32(acc[k],_mm256_srai_epi32(_mm256_madd_epi16(xv,_mm256_load_si256((const __m256i*)(w[k]+i))),4)); }
    for(int k=0;k<K;k++){ __m128i h=_mm_add_epi32(_mm256_castsi256_si128(acc[k]),_mm256_extracti128_si256(acc[k],1)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0x4e)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0xb1));
      int d=_mm_cvtsi128_si32(h)>>(qw-4); if(d>2047)d=2047; if(d<-2047)d=-2047; S[k].pr=squash_c(d); out[k]=d; } }
  template<int K> inline void upd_fused(int y){
    if(met){ int mx=0; for(int k=0;k<K;k++){ int a=(y<<12)-S[k].pr; if(a<0)a=-a; if(a>mx)mx=a; } if(mx<met) return; }
    __m256i ev[K]; int16_t*w[K];
    for(int k=0;k<K;k++){ Set&s=S[k]; int e=(((y<<12)-s.pr)*s.lr*gm)>>(17-qw+4); if(e>32767)e=32767; if(e<-32767)e=-32767; ev[k]=_mm256_set1_epi16((short)e); w[k]=s.cw; }
    for(int i=0;i<n;i+=16){ __m256i xv=_mm256_load_si256((const __m256i*)(x+i));
      for(int k=0;k<K;k++){ __m256i*p=(__m256i*)(w[k]+i); _mm256_store_si256(p,_mm256_adds_epi16(_mm256_load_si256(p),_mm256_mulhrs_epi16(xv,ev[k]))); } } }
#endif
};
#ifdef QZ_MIX32
typedef Mixer MixT; typedef int MixX;
#else
typedef Mixer16 MixT; typedef int16_t MixX;
#endif
#if defined(QZ_W8) && defined(__AVX2__) && !defined(QZ_MIX32)
// ---------------- experimental int8-weight first-layer mixer (build with -DQZ_W8; A/B only, not a stream format) ----------------
// weights int8 in Q6 (range +-2), inputs int16 stretch, products summed in int32 after widening the weights to int16.
// An 8-bit weight cannot hold the small learning steps, so updates use deterministic dithered rounding
// (xorshift, identical in encoder and decoder): dw = (x*e + r) >> 24, r uniform in [0, 2^24).
struct Mixer8 {
  int n=0, nx=0; int16_t *x=nullptr; bool own=true; int qw=6; int gm=16; U32 rs=2463534242u;
  struct Set { int8_t *w; int8_t *cw; int pr; int lr; };
  std::vector<Set> S;
  void init(int ninputs){ n=(ninputs+15)&~15; x=(int16_t*)aligned_zalloc((size_t)n*2); }
  ~Mixer8(){ afree(x); if(own) for(auto&s:S) afree(s.w); }
  void alias(const Mixer8&o){ if(own) for(auto&s:S) afree(s.w); S=o.S; for(auto&s:S){ s.cw=s.w; s.pr=2048; } own=false; }
  void addset(int nsel,int lr,int initw){ Set s; s.w=(int8_t*)aligned_zalloc((size_t)n*nsel); int8_t iw=(int8_t)(initw>>10); for(size_t i=0;i<(size_t)n*nsel;i++) s.w[i]=iw; s.lr=lr; s.cw=s.w; s.pr=2048; S.push_back(s); }
  inline void add(int v){ x[nx++]=(int16_t)v; }
  inline int mix(int k,int sel){ Set&s=S[k]; s.cw=s.w+(size_t)sel*n; __m256i acc=_mm256_setzero_si256();
    for(int i=0;i<n;i+=16){ __m256i w=_mm256_cvtepi8_epi16(_mm_load_si128((const __m128i*)(s.cw+i))); acc=_mm256_add_epi32(acc,_mm256_madd_epi16(_mm256_load_si256((const __m256i*)(x+i)),w)); }
    __m128i h=_mm_add_epi32(_mm256_castsi256_si128(acc),_mm256_extracti128_si256(acc,1)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0x4e)); h=_mm_add_epi32(h,_mm_shuffle_epi32(h,0xb1));
    int d=_mm_cvtsi128_si32(h)>>6; if(d>2047)d=2047; if(d<-2047)d=-2047; s.pr=squash_c(d); return d; }
  inline void upd(int y){
    const __m256i lanes=_mm256_setr_epi32(0,0x9E3779B1,0x3C6EF362,(int)0xDAA66D13,0x78DDE6C4,0x1715609D,(int)0xB54CDA56,0x5384540F), m24=_mm256_set1_epi32((1<<24)-1);
    for(auto&s:S){ int e=(((y<<12)-s.pr)*s.lr*gm)>>6; if(e>32767)e=32767; if(e<-32767)e=-32767; __m256i ev=_mm256_set1_epi16((short)e);
      for(int i=0;i<n;i+=16){ rs^=rs<<13; rs^=rs>>17; rs^=rs<<5; __m256i r=_mm256_and_si256(_mm256_add_epi32(_mm256_set1_epi32((int)rs),lanes),m24);
        __m256i xv=_mm256_load_si256((const __m256i*)(x+i)); __m256i lo=_mm256_mullo_epi16(xv,ev), hi=_mm256_mulhi_epi16(xv,ev);
        __m256i p0=_mm256_srai_epi32(_mm256_add_epi32(_mm256_unpacklo_epi16(lo,hi),r),24), p1=_mm256_srai_epi32(_mm256_add_epi32(_mm256_unpackhi_epi16(lo,hi),_mm256_shuffle_epi32(r,0x1b)),24);
        __m256i dw=_mm256_packs_epi32(p0,p1);
        __m256i w=_mm256_adds_epi16(_mm256_cvtepi8_epi16(_mm_load_si128((const __m128i*)(s.cw+i))),dw);
        __m256i pk=_mm256_permute4x64_epi64(_mm256_packs_epi16(w,w),0x08);
        _mm_store_si128((__m128i*)(s.cw+i),_mm256_castsi256_si128(pk)); } }
    nx=0; }
};
typedef Mixer8 MixM;
#else
typedef MixT MixM;
#endif
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

// ---------------- hashed context table ----------------
// 16-byte slot = 8-bit check + 15 bit-history states (one nibble tree); 4 slots = one 64-byte bucket.
struct Slot { U8 chk; U8 st[15]; };
struct HTable { Slot *t=nullptr; U64 mask=0, nb=0; size_t bytes=0; bool own=true; int frac=0;
  // frac 1..7 (v6): (8-frac)/8 of the 2^bits slots, bucket picked by multiply-shift instead of a mask
  void init(int bits,int fr=0){ frac=fr; size_t n=(size_t)1<<bits; if(frac) n=n/8*(8-frac); bytes=n*sizeof(Slot); t=(Slot*)big_alloc(bytes); nb=n/4; mask=(n/4)-1; }
  ~HTable(){ if(own) big_free(t,bytes); }
  void alias(const HTable&o){ if(own) big_free(t,bytes); t=o.t; mask=o.mask; nb=o.nb; frac=o.frac; bytes=o.bytes; own=false; }
  inline Slot* bucket(U64 h){ return frac? t+((((h>>8)&0xffffffffULL)*nb)>>32)*4 : t+((h>>8)&mask)*4; }
  inline U8* find(U64 h){ Slot*b=bucket(h); U8 c=(U8)(h>>56);
#ifdef __AVX2__
    { const __m256i cv=_mm256_set1_epi8((char)c); // tag test of the 4 slots at once (same first-match result as the scalar scan)
      U64 m=(U64)(U32)_mm256_movemask_epi8(_mm256_cmpeq_epi8(_mm256_load_si256((const __m256i*)b),cv)) | ((U64)(U32)_mm256_movemask_epi8(_mm256_cmpeq_epi8(_mm256_load_si256((const __m256i*)(b+2)),cv))<<32);
      m&=0x0001000100010001ULL; if(m) return b[__builtin_ctzll(m)>>4].st; }
#else
    for(int i=0;i<4;i++) if(b[i].chk==c) return b[i].st;
#endif
    int v=0,bp=999; for(int i=0;i<4;i++){ int pr=SPRI[b[i].st[0]]; if(pr<bp){bp=pr;v=i;} }
    memset(&b[v],0,sizeof(Slot)); b[v].chk=c; return b[v].st; }
};

static void wrt_roles(const U8*t,U8 role[256]);
// ---------------- predictor ----------------
// -DQZ_SMALLSET drops orders 6, 7, 20, the word trigram / skip contexts and the column+c1 context: 14% faster,
// +0.39% on enwik8 (neutral at <= 10 MB). Default is the full 26-context set (enwik8: 0.1% below the original).
#ifdef QZ_SMALLSET
#define QZ_NORD 7
#else
#define QZ_M67 1
#define QZ_M20 1
#define QZ_MW 1
#define QZ_MC 1
#define QZ_NORD 10
#endif
enum { MAXM=56, NRUN=4, NORD=QZ_NORD, SMS=272 }; // SMS: per-model state-map stride (256 + 16 pad: rows 4 KiB apart would alias in the store buffer) // NORD = number of leading pure order-N models (conf = highest one with a history)
static U32 QDROP=0, QDROPH=0; // QDROPH: drop mask for models 32..63 (v6 header) // ablation: bit k drops hashed model k
static U32 QXW=0, QXS=0; // v5: extra text contexts (bitmask, see compute_hashes) and an extra mixer weight set (1 = keyed by the previous word)
static int NTHR=1; static U32 Q6=0; static int QPF=0, QPFI=1, QPFW=1; // v6 flags: bit0 no confidence inputs, bit1 match APM, bit2 order-2 APM; QPF/QPFI: prefetch toggles (speed only)
static U32 QSETS=63, QAPM=7, QF2=1; static int LRM=16, LRA=0, LRD=1024, SLIM=1023, PW=1, APMR=7; // active mixer weight sets / APM stages / second final mixer
static const U64 MK=0x2F0F3; // match-model polynomial hash multiplier (odd => invertible mod 2^64)
static U64 MKINV, MK5, MK23;
static const U64 OK=0x100000001B3ULL; static U64 OK11, OK19; // order-12/20 rolling hash multiplier and K^(L-1)
static void initMatchConsts(){ OK11=1; for(int i=0;i<11;i++) OK11*=OK; OK19=1; for(int i=0;i<19;i++) OK19*=OK; U64 x=MK; for(int i=0;i<6;i++) x*=2-MK*x; MKINV=x; MK5=1; for(int i=0;i<5;i++) MK5*=MK; MK23=1; for(int i=0;i<23;i++) MK23*=MK; }
struct RunE { U8 chk, b, n, pad; };
struct Predictor {
  int nm=0; // hashed models
  HTable ht; U64 H[MAXM], H2[MAXM]; U8 *slot[MAXM]; U8 st[MAXM]; U32 *smt=nullptr; // smt: per-model state->probability maps, flattened
  SMap o0, o1d, mm, rsm[NRUN]; MixM M; MixT F, F2; APM a1,a2,a3,a4,a5,a6; U64 tsh=0, tfs=0, pfs[32], cfs[32]; int tfi=0, tlc=-1; U64 tstk=0; /* template model (v6 flag 131072) */ Slot *dt=nullptr; size_t dt_bytes=0; U32 dix[MAXM]; SMap bsm; int byp=0, bex=0, MLT=0; /* long-match bypass (v6 flags 16384/32768) */ int ac4=0, ac5=0, ac6=0; U64 wcc=0;
  // optional external (neural) predictor: per byte, nnp[c0] = P(next bit = 1 | partial byte c0), 16-bit, c0 in 1..255.
  // It enters as two mixer inputs, selects an extra weight set, and drives one more APM stage.
  bool nn=false; const U16 *nnp=nullptr; APM an; int nnb=0;
  // multi-head use (qzl API): several Predictors share every learned table and the input buffer, each keeps its own
  // context state and codes its own segment [segstart, ...) of the buffer. Reads of history before segstart see 0.
  bool own=true; U32 segstart=0, seglen=0; // seglen>0 only in multi-head mode
  inline int hist(U32 back){ return pos>=segstart+back? buf[pos-back] : 0; }
  RunE *rt=nullptr; size_t rt_bytes=0; U64 rmask; RunE *re[NRUN]; U8 rck[NRUN], rck2[NRUN]; int rbyte[NRUN], rcnt[NRUN]; U8 ract[NRUN]; int mact=0;
  U16 *ind1=nullptr, *ind2=nullptr; U32 *wn=nullptr; U32 *wn2=nullptr; U64 num=0; int conf=0; int nw=0; U32 sep=0, sepc=0; U32 shp=0, wlen=0, wcap=0; U64 lw=0, lwc=0; U8 nst[16]; U64 nsw[16]; int nd=0; U64 lfw=0; int lfwset=0;
  int c0=1,bp=0,node=0; U32 c4=0,c8=0; U64 w0=0,w1=0,w2=0,w3=0; int inword=0; int col=0; U32 lastnl=0, prevnl=0;
  U32 mk=0; // markup state
  U8 *buf=nullptr; size_t buf_bytes=0; U32 pos=0;
  U32 *mt1=nullptr, *mt2=nullptr; U32 mptr=0; int mlen=0; int mbits=22, tbits=22; U64 mh1=0, mh2=0, oh12=0, oh20=0; // mbits/tbits: match-table and run+indirect+word-table log2 sizes; rolling hashes (match 6/24, order 12/20) U64 mh1=0, mh2=0, oh12=0, oh20=0; // rolling polynomial hashes of the last 12 / 20 bytes
  const U8 *role=nullptr; bool wrt=false; U8 rl[256];
  int codepend=0, escpend=0; U64 codeh=0; int wev=0, cev=0, cfl=0; /* cfl (v6 flag 512): 1 = byte inside a word code, 2 = right after one */ U64 lastw1=0, lastw1x=0, lastw1n=0, lastw1l=0;
  int prf=32768, hits=0, ml=0; U32 mi1=0, mi2=0; int mpend=0;
  ~Predictor(){ F2.x=nullptr; if(!own) return; if(dt) big_free(dt,dt_bytes); afree(smt); big_free(rt,rt_bytes); big_free(ind1,65536*2); big_free(ind2,(size_t)2<<tbits); big_free(wn,(size_t)4<<tbits); if(wn2) big_free(wn2,(size_t)4<<tbits); big_free(buf,buf_bytes);
    big_free(mt1,(size_t)4<<mbits); big_free(mt2,(size_t)4<<mbits); }
  void init(int hbits,int mb,int tb,size_t bufsize,const U8*r){
    mbits=mb; tbits=tb; wrt=(r!=nullptr); memset(pfs,0,sizeof pfs); memset(cfs,0,sizeof cfs); ht.init(hbits,(Q6>>5)&7); if(Q6&65536){ dt_bytes=(size_t)(1024+4*65536)*17*sizeof(Slot); dt=(Slot*)big_alloc(dt_bytes); } buf_bytes=bufsize+8; buf=big_array<U8>(buf_bytes);
    const int lim=1023;
    smt=(U32*)aligned_zalloc((size_t)MAXM*SMS*4);
    for(int i=0;i<MAXM*SMS;i++){ int s=i%SMS; if(s>255) continue; U32 p=1u<<21; if(s<NSTATES) p=(U32)(((SN1[s]*2+1)<<22)/(SN0[s]*2+SN1[s]*2+2)); smt[i]=p<<10; }
    o0.init(256,lim); o1d.init(65536,lim); mm.init(64*2,lim); for(int i=0;i<NRUN;i++) rsm[i].init(64*2,lim);
    rt_bytes=(size_t)sizeof(RunE)<<tbits; rt=big_array<RunE>((size_t)1<<tbits); rmask=((U64)1<<tbits)-1;
    ind1=big_array<U16>(65536); ind2=big_array<U16>((size_t)1<<tbits); wn=big_array<U32>((size_t)1<<tbits); if(QXW&2) wn2=big_array<U32>((size_t)1<<tbits);
    mt1=big_array<U32>((size_t)1<<mbits); mt2=big_array<U32>((size_t)1<<mbits);
    for(int r=0;r<NRUN;r++) ract[r]=0;
    wcc=wc_cur(); compute_hashes(); lookup_slots();
    int ni=((Q6&(1|4096))?nm:2*nm)+NRUN+4+(nn?2:0); M.init(ni);
#ifndef QZ_MIX32
    if(Q6&2048) M.qw=14;
    M.met=(Q6>>20)&7? 16<<((Q6>>20)&7) : 0; // 32..2048
#endif // per model: stretch(p) + confidence; runs; o0, o1d, match, bias [; nn stretch, nn confidence]
    const int lr=12, iw=4096; // init weight 1/16 (Q16) so the 48 inputs start near a plain average; tuned on e1m/e10m
    // weight sets selected by: c0 | c1,bp | hits,ml | c2,bp | markup,bp | conf,bp  (a match-length-selected set measured as a net loss and was removed)
    { const int ns[6]={(Q6&512)?768:256,2048,1024,2048,2048,128}; for(int k=0;k<6;k++) if(QSETS>>k&1) M.addset(ns[k],lr,iw); }
    if(QXS) M.addset(QXS==1? 4096 : 8192,lr,iw);
    if(nn){ M.addset(64*8,lr,iw); an.init(4096*64,7); }
    int nf=(int)M.S.size(); F.init(nf+1); F.addset((Q6&512)?768:256,8,(1<<16)/8); F2.init(nf+1); afree(F2.x); F2.x=F.x; F2.addset(2048,8,(1<<16)/8); // final mixers: lr 8, init 1/8 (tuned)
    a1.init(65536,APMR); a2.init(65536,APMR); a3.init(65536,APMR); if(Q6&2) a4.init(68*256,APMR); if(Q6&4) a5.init(65536,APMR); if(Q6&8192) a6.init(11*256*8,APMR); MLT=(Q6>>14)&3? 16<<((Q6>>14)&3) : 0; if(MLT) bsm.init(64*8,1023);
  }
  // extra head: share every learned table and the buffer with head `o`; private context state; segment starts at seg0
  void init_head(Predictor&o,U32 seg0,U32 seglen_){
    nn=o.nn; mbits=o.mbits; tbits=o.tbits; nm=o.nm; own=false; dt=o.dt; segstart=seg0; seglen=seglen_; pos=seg0;
    ht.alias(o.ht); smt=o.smt; rt=o.rt; rmask=o.rmask; ind1=o.ind1; ind2=o.ind2; wn=o.wn; wn2=o.wn2; buf=o.buf; mt1=o.mt1; mt2=o.mt2;
    o0.alias(o.o0); o1d.alias(o.o1d); mm.alias(o.mm); for(int r=0;r<NRUN;r++) rsm[r].alias(o.rsm[r]);
    M.init(o.M.n); M.alias(o.M); F.init(o.F.n); F.alias(o.F); F2.init(o.F2.n); afree(F2.x); F2.x=F.x; F2.alias(o.F2); a1.alias(o.a1); a2.alias(o.a2); a3.alias(o.a3); if(nn) an.alias(o.an);
    for(int r=0;r<NRUN;r++) ract[r]=0;
    wcc=wc_cur(); compute_hashes(); lookup_slots(); }
  inline int mlb(){ if(mlen==0) return 0; if(mlen<16) return mlen; if(mlen<32) return 16+((mlen-16)>>2); if(mlen<64) return 20+((mlen-32)>>3); if(mlen<512) return 24+((mlen-64)>>6); return 31; }
  // byte-boundary context hashes; prefetch their buckets now, look them up later (lookup_slots)
  void compute_hashes(){
    U32 c1=c4&255; int i=0;
    // orders 1-8, 12, 20 (QZ_SMALLSET: 1-5, 8, 12)
    H[i++]=hh(1,(c4&0xff)|(cfl<<8)); H[i++]=hh(2,c4&0xffff); H[i++]=hh(3,c4&0xffffff); H[i++]=hh(4,c4);
    H[i++]=hh(5,hh(c4,c8&0xff));
#ifdef QZ_M67
    H[i++]=hh(6,hh(c4,c8&0xffff)); H[i++]=hh(7,hh(c4,c8&0xffffff));
#endif
    H[i++]=hh(8,hh(c4,c8)); H[i++]=hh(9,oh12);
#ifdef QZ_M20
    H[i++]=hh(10,oh20);
#endif
    // word contexts: current word, word bigram, word skip-bigram, [trigram, bigram+skip], previous word + 2 bytes
    U64 wc = inword? w0 : hh(w0, c1); U64 wb=hh(wc,w1);
    H[i++]=hh(11,wc); H[i++]=hh(12,wb); H[i++]=hh(14,hh(wc,w2));
#ifdef QZ_MW
    H[i++]=hh(13,hh(wb,w2)); H[i++]=hh(15,hh(wb,hh(w2,w3)));
#endif
    H[i++]=hh(16,hh(w1,c4&0xffff));
    H[i++]=hh(17,c4&0xff00ff00); H[i++]=hh(18,c4&0xffff0000); H[i++]=hh(19,(c4&0xff00)|(c8&0xff000000ULL));
    U32 ab=0; if(lastnl>0){ U32 q=prevnl+col; ab=(q<lastnl-1 && q>=segstart)?buf[q]:0; }
#ifdef QZ_MC
    H[i++]=hh(20,hh(ab,c1));
#endif
    H[i++]=hh(21,hh(ab,col<64?col:64));
    // indirect: what followed the last occurrences of this bigram / trigram
    U16 t1=ind1[c4&0xffff]; U16 t2=ind2[(c4&0xffffff)*0x9E3779B1u>>(32-tbits)];
    H[i++]=hh(22,hh(c1,t1)); H[i++]=hh(23,hh(c4&0xffff,t2));
    // markup state + word
    H[i++]=hh(24,hh(mk,wc));
    H[i++]=hh(25,hh(wn[(w1*0x9E3779B97F4A7C15ULL)>>(64-tbits)],wc));
    H[i++]=hh(26,hh(num,w1));
    if(QXW){
      if(QXW&1) H[i++]=hh(27,hh(wc,nw));                                        // word + position in sentence
      if(QXW&2) H[i++]=hh(28,hh(wn2[(hh(w1,w2)*0x9E3779B97F4A7C15ULL)>>(64-tbits)],wc)); // word predicted by the last bigram
      if(QXW&4) H[i++]=hh(29,c4&0x00ff00ff);                                     // sparse: bytes 1 and 3
      if(QXW&8) H[i++]=hh(30,hh(lastnl<pos?buf[lastnl]:0,wc));                   // first byte of the line + word
      if(QXW&16) H[i++]=hh(31,hh(wc,hh(w1,sep)));                                // word + previous word + separator
      if(QXW&32) H[i++]=hh(32,hh(wc,w3));                                        // word + word 3 back (skip 2)
      if(QXW&64) H[i++]=hh(33,hh(wc,sepc));                                      // word + count of separators since line start
      if(QXW&128) H[i++]=hh(34,hh(wc,shp));                                      // word + shape (caps, length) of last 2 words
      if(QXW&256) H[i++]=hh(35,hh(c4&0xffffff,w1));                              // order 3 + previous word
      if(QXW&512) H[i++]=hh(36,hh(wc,nd?nst[(nd-1)&15]*16+(nd>15?15:nd):0));     // word + innermost open bracket and depth
      if(QXW&1024) H[i++]=hh(37,hh(wc,hh(w1,w3)));                               // word + words 1 and 3 back (skip w2)
      if(QXW&2048) H[i++]=hh(38,hh(c4&0xffff,col<63?col:63));                    // order 2 + column
      if(QXW&4096) H[i++]=hh(39,hh(wc,lw));                                      // word + last long word before the previous word
      if(QXW&8192) H[i++]=hh(40,hh(wc,nd?hh(nst[(nd-1)&15],nsw[(nd-1)&15]):0));  // word + innermost bracket + first word inside it
      if(QXW&16384){ U32 l2=lastnl+1<pos? buf[lastnl]|buf[lastnl+1]<<8 : 0; H[i++]=hh(41,hh(l2,c4&0xffff)); } // first 2 line bytes + order 2
      if(QXW&32768) H[i++]=hh(42,hh(wc,ab));                                     // word + byte above
      if(QXW&65536) H[i++]=hh(43,hh(wc,sep));                                    // word + last 2 separators
      U64 nk = nd? hh(nst[(nd-1)&15],nsw[(nd-1)&15]) : 0;
      if(QXW&131072) H[i++]=hh(44,hh(c4&0xffff,nk));                             // order 2 + bracket + its first word
      if(QXW&262144) H[i++]=hh(45,hh(wb,nk));                                    // word bigram + bracket + its first word
      if(QXW&524288) H[i++]=hh(46,hh(wc,nd>1?hh(nst[(nd-2)&15],nsw[(nd-2)&15]):0)); // word + enclosing bracket + its first word
      if(QXW&1048576) H[i++]=hh(47,hh(wc,lfw));                                  // word + first word of the line
      if(QXW&2097152){ U32 l2=lastnl+1<pos? buf[lastnl]|buf[lastnl+1]<<8 : 0; H[i++]=hh(48,hh(l2,wc)); } // first 2 line bytes + word
    }
    // run-model contexts: orders 2, 3, 6, word trigram (order 4 and word bigram runs measured at 0.01% and dropped)
    // template / structure model (v6 flag 131072) replaces three weak text contexts: line shape so far (letters, digits and
    // word codes abstracted, runs collapsed) + last byte; the same field of the previous line + this field's shape so far +
    // field index; the nesting of the two innermost brackets with their first words + depth + current word
    if((Q6&131072) && i>43){ H[29]=hh(50,hh(tsh,c4&0xff)); H[32]=hh(51,hh(pfs[tfi&31],hh(tfs, (Q6&262144)? (U32)(ab+256) : (U32)tfi))); H[43]=hh(52,hh(tstk,(Q6&524288)? (U64)(c4&0xffff) : wc)); }
    U64 RH[NRUN]={H[1],H[2],hh(6,hh(c4,c8&0xffff)),hh(13,hh(wb,w2))};
    // direct tables (v6 flag 65536): orders 1, 2 and the three sparse 2-byte contexts index a plain array, no hashing/eviction
    for(int k=0;k<i;k++) dix[k]=0;
    if(dt && NORD==10){ U32 c1x=(c4&255)|(cfl<<8); dix[0]=1+c1x; dix[1]=1+1024+(c4&0xffff);
      dix[16]=1+1024+65536+(((c4>>8)&255)|((c4>>24)<<8)); dix[17]=1+1024+2*65536+(c4>>16); dix[18]=1+1024+3*65536+(((c4>>8)&255)|((c8>>24)<<8)); }
    if(QDROP||QDROPH){ int j=0; for(int k=0;k<i;k++) if(!(k<32 ? (QDROP>>k&1) : (QDROPH>>(k-32)&1))){ dix[j]=dix[k]; H[j++]=H[k]; } i=j; }
    nm=i;
    for(int k=0;k<nm;k++) __builtin_prefetch(dix[k]? (void*)dt[(size_t)(dix[k]-1)*17].st : (void*)ht.bucket(H[k]));
    for(int r=0;r<NRUN;r++){ U64 h=RH[r]*0x9E3779B97F4A7C15ULL; re[r]=&rt[(h>>20)&rmask]; rck[r]=(U8)(h>>56); rck2[r]=(Q6&1024)?(U8)(h>>48):0; __builtin_prefetch(re[r]); }
  }
  void lookup_slots(){
    for(int k=0;k<nm;k++) slot[k]=dix[k]? dt[(size_t)(dix[k]-1)*17].st : ht.find(H[k]);
    for(int r=0;r<NRUN;r++){ RunE*e=re[r]; U8 ck=rck[r];
      if(e->chk==ck && e->pad==rck2[r] && e->n){ rbyte[r]=e->b; rcnt[r]=e->n; } else { rbyte[r]=-1; rcnt[r]=0; } }
  }
  inline int pp(){
    if(byp){ bex=(buf[mptr]>>(7-bp))&1; int p=bsm.p(((mlb()*2+bex)<<3)+bp)*16+8; if(p<8)p=8; if(p>65535-8)p=65535-8; prf=p; return p; }
    node = (bp<4)? ((1<<bp)-1 + (c0&((1<<bp)-1))) : ((1<<(bp-4))-1 + (c0&((1<<(bp-4))-1)));
    int c1=c4&255, c2=(c4>>8)&255;
    // APM rows (66 bytes each) are selected by context only; fetch them while the models run
    int ac1=c0|(c1<<8), ac2=(int)(U32)(hh(c4&0xffffff,c0)>>48), ac3=(int)(U32)(hh(wcc,c0)>>48);
    __builtin_prefetch(a1.t+(size_t)ac1*33); __builtin_prefetch(a1.t+(size_t)ac1*33+32);
    __builtin_prefetch(a2.t+(size_t)ac2*33); __builtin_prefetch(a2.t+(size_t)ac2*33+32);
    __builtin_prefetch(a3.t+(size_t)ac3*33); __builtin_prefetch(a3.t+(size_t)ac3*33+32);
    int hts=0, cf=0;
    { MixX*__restrict xp=M.x; const U32*__restrict smp=smt; U8*__restrict stp=st; U8*const*__restrict sl=slot; const int nd=node, n=nm;
      if(Q6&1){ for(int k=0;k<n;k++,smp+=SMS){ U8 s=sl[k][nd]; stp[k]=s; xp[k]=stretch((int)(smp[s]>>20)); hts+=(s!=0); } }
      else {
        int k=0;
#if defined(__AVX2__) && !defined(QZ_MIX32)
        // 8 models at a time: gather p from the state maps, stretch, confidence; same int16 values as the scalar loop
        for(int j=0;j<n;j++) stp[j]=sl[j][nd];
        const __m256i lane=_mm256_setr_epi32(0,SMS,2*SMS,3*SMS,4*SMS,5*SMS,6*SMS,7*SMS), c2048=_mm256_set1_epi32(2048), m16=_mm256_set1_epi32(0xffff);
        for(;k+8<=n;k+=8){ __m256i sv=_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(stp+k)));
          __m256i v=QZ_GATHER((const int*)(smt+(size_t)k*SMS),_mm256_add_epi32(sv,lane),4);
          __m256i pk=_mm256_srli_epi32(v,20);
          __m256i sx=QZ_GATHER(STR32,pk,4);
          __m256i cm=QZ_GATHER(SX2M,sv,4);
          __m256i cfv=_mm256_and_si256(_mm256_srai_epi32(_mm256_sub_epi32(pk,c2048),2),cm);
          if(Q6&4096){ __m256i sm=_mm256_add_epi32(sx,cfv); _mm_storeu_si128((__m128i*)(xp+k),_mm256_castsi256_si128(_mm256_permute4x64_epi64(_mm256_packs_epi32(sm,sm),0x08))); }
          else _mm256_storeu_si256((__m256i*)(xp+2*k),_mm256_or_si256(_mm256_and_si256(sx,m16),_mm256_slli_epi32(cfv,16)));
          hts+=8-__builtin_popcount(_mm256_movemask_ps(_mm256_castsi256_ps(_mm256_cmpeq_epi32(sv,_mm256_setzero_si256())))); }
        smp+=(size_t)k*SMS;
#endif
        if(Q6&4096) for(;k<n;k++,smp+=SMS){ U8 s=sl[k][nd]; stp[k]=s; int pk=(int)(smp[s]>>20); xp[k]=stretch(pk)+(((pk-2048)>>2)&SX2M[s]); hts+=(s!=0); }
        else for(;k<n;k++,smp+=SMS){ U8 s=sl[k][nd]; stp[k]=s; int pk=(int)(smp[s]>>20); xp[2*k]=stretch(pk); xp[2*k+1]=((pk-2048)>>2)&SX2M[s]; hts+=(s!=0); } }
      for(int k=0;k<NORD;k++) if(stp[k]) cf=k+1; }
    hits=hts; conf=cf;
    MixX*__restrict xr=M.x+((Q6&(1|4096))?nm:2*nm); const int ex=8-bp, sh=7-bp;
    for(int r=0;r<NRUN;r++){ int x=0; ract[r]=0; int rb=rbyte[r]; if(rb>=0 && ((rb|256)>>ex)==c0){ int e=(rb>>sh)&1; int n=rcnt[r]>31?31:rcnt[r]; x=stretch(rsm[r].p(n*2+e)); ract[r]=1; } xr[r]=(MixX)x; }
    xr[NRUN]=(MixX)stretch(o0.p(c0)); xr[NRUN+1]=(MixX)stretch(o1d.p(c0|(c1<<8)));
    int mx_in=0; ml=0; mact=0;
    if(mlen>0){ int eb=buf[mptr]; if(((eb|256)>>ex)==c0){ int meb=(eb>>sh)&1; ml=mlb(); mx_in=stretch(mm.p(ml*2+meb)); mact=1; } else { mlen=0; } }
    xr[NRUN+2]=(MixX)mx_in; xr[NRUN+3]=256;
    int pnn=2048, snn=0;
    if(nn){ pnn=nnp[c0]>>4; if(pnn<1)pnn=1; if(pnn>4095)pnn=4095; snn=stretch(pnn); xr[NRUN+4]=(MixX)snn; xr[NRUN+5]=(MixX)((pnn-2048)>>1); nnb=(snn+2048)>>6; }
    int hh5 = hits>31?31:hits;
    MixX*__restrict fx=F.x;
    int nf=0;
#if defined(__AVX2__) && !defined(QZ_MIX32) && !defined(QZ_W8)
    if(QSETS==63 && QXS==1 && !nn && M.S.size()==7){
      int sel[7]={c0+256*cfl,(c1<<3)+bp,(hh5<<5)|(ml>31?31:ml),(c2<<3)+bp,(int)((mk&255)<<3)+bp,(conf<<3)+bp,(int)(U32)(w1>>52)}, d[7];
      M.mix_fused<7>(sel,d); for(int k=0;k<7;k++) fx[k]=(MixX)d[k]; nf=7; fx[nf]=256; goto mixed; }
#endif
    if(QSETS==63){
      fx[0]=(MixX)M.mix(0,c0+256*cfl); fx[1]=(MixX)M.mix(1,(c1<<3)+bp); fx[2]=(MixX)M.mix(2,(hh5<<5)|(ml>31?31:ml));
      fx[3]=(MixX)M.mix(3,(c2<<3)+bp); fx[4]=(MixX)M.mix(4,((mk&255)<<3)+bp); fx[5]=(MixX)M.mix(5,(conf<<3)+bp); nf=6;
    } else {
      const int sel[6]={c0+256*cfl,(c1<<3)+bp,(hh5<<5)|(ml>31?31:ml),(c2<<3)+bp,(int)(((mk&255)<<3)+bp),(conf<<3)+bp};
      for(int k=0;k<6;k++) if(QSETS>>k&1){ fx[nf]=(MixX)M.mix(nf,sel[k]); nf++; }
    }
    if(QXS){ U32 sel = QXS==1? (U32)(w1>>52) : (((U32)(wcc>>54))<<3)+bp; fx[nf]=(MixX)M.mix(nf,sel); nf++; }
    if(nn){ fx[nf]=(MixX)M.mix(nf,(nnb<<3)+bp); fx[nf+1]=256; } else fx[nf]=256;
    mixed:
    int p0;
    if(QF2){ F.mix(0,c0+256*cfl); F2.mix(0,(c1<<3)+bp); p0=squash((stretch(F.S[0].pr)+stretch(F2.S[0].pr))>>1); }
    else { F.mix(0,c0+256*cfl); p0=F.S[0].pr; }
    int sp=stretch(p0);
    int pf;
    if(Q6&(6|8192)){ int sa=a1.pps(sp,ac1)+a2.pps(sp,ac2)+a3.pps(sp,ac3), na=3;
      if(Q6&2){ ac4=(mact? (ml*2+((buf[mptr]>>(7-bp))&1)+2) : (hits>0?1:0))*256+c0; sa+=a4.pps(sp,ac4); na++; }
      if(Q6&4){ ac5=(int)(U32)(hh(c4&0xffff,c0)>>48); sa+=a5.pps(sp,ac5); na++; }
      // calibration row (v6 flag 8192): observed bit rate for the bit history of the longest context that has one, per bit position
      if(Q6&8192){ ac6=((conf*256+(conf?st[conf-1]:0))<<3)+bp; sa+=a6.pps(sp,ac6); na++; }
      pf=(p0*16+sa)/(na+1); }
    else if(QAPM==7){ int pa1=a1.pps(sp,ac1), pa2=a2.pps(sp,ac2), pa3=a3.pps(sp,ac3); pf=PW==1? (p0*16+pa1+pa2+pa3)>>2 : (p0*48*PW+(4-PW)*(pa1+pa2+pa3))/12; }
    else { int na=0, sa=0; if(QAPM&1){ sa+=a1.pps(sp,ac1); na++; } if(QAPM&2){ sa+=a2.pps(sp,ac2); na++; } if(QAPM&4){ sa+=a3.pps(sp,ac3); na++; } pf=(p0*16*(4-na)+sa)>>2; }
    if(nn) pf=(pf*3+an.pps(sp,pnn*64+(hits>63?63:hits)))>>2; // SSE keyed by the NN probability and the CM hit count
    if(pf<8)pf=8; if(pf>65535-8)pf=65535-8; prf=pf; return prf;
  }
  // the mixer rows selected by byte-constant contexts (c1, c2, markup, previous word) for all 8 bits of the next byte
  void prefetch_rows(){
    const size_t rb=(size_t)M.n*sizeof(MixX); int c1=c4&255, c2=(c4>>8)&255; int j=0;
    for(int k=0;k<6;k++) if(QSETS>>k&1){ int base=k==1? c1<<3 : k==3? c2<<3 : k==4? (int)((mk&255)<<3) : -1;
      if(base>=0){ const char*p=(const char*)(M.S[j].w+(size_t)base*M.n); for(size_t o=0;o<8*rb;o+=64) __builtin_prefetch(p+o); } j++; }
    if(QXS==1){ const char*p=(const char*)(M.S[j].w+(size_t)(w1>>52)*M.n); for(size_t o=0;o<rb;o+=64) __builtin_prefetch(p+o); }
    if(QF2){ const char*p=(const char*)(F2.S[0].w+(size_t)(c1<<3)*F2.n); for(size_t o=0;o<8*(size_t)F2.n*sizeof(MixX);o+=64) __builtin_prefetch(p+o); } }
  inline U64 wc_cur(){ return inword? w0 : hh(w0, c4&255); }
  // bypassed byte: a long verified match predicts every bit from one small adaptive map; the context models are not
  // looked up, trained or mixed. A wrong bit ends the bypass and the skipped lookups are done for the rest of the byte.
  void update_bypass(int y){
    bsm.upd(y); int c0n=(c0<<1)|y, bpn=bp+1;
    if(bpn==8) byte_state_update(c0n&255);
    c0=c0n; bp=bpn;
    if(bp==8){ if(mpend) match_update(); byp=0; if(y==bex && mlen>=MLT) byp=1; else lookup_slots(); c0=1; bp=0; return; }
    if(y!=bex){ byp=0;
      if(bp<4){ for(int k=0;k<nm;k++) slot[k]=dix[k]? dt[(size_t)(dix[k]-1)*17].st : ht.find(H[k]); }
      else { U64 nk=(U64)((c0>>(bp-4))+1)*0x9E3779B97F4A7C15ULL; nk^=nk>>29; for(int k=0;k<nm;k++){ H2[k]=H[k]^nk; slot[k]=dix[k]? dt[(size_t)(dix[k]-1)*17+1+((c0>>(bp-4))&15)].st : ht.find(H2[k]); } }
      for(int r=0;r<NRUN;r++){ RunE*e=re[r]; if(e->chk==rck[r] && e->pad==rck2[r] && e->n){ rbyte[r]=e->b; rcnt[r]=e->n; } else { rbyte[r]=-1; rcnt[r]=0; } } } }
  void update(int y){
    if(byp){ update_bypass(y); return; }
    int c0n=(c0<<1)|y, bpn=bp+1;
    // issue the next lookups' prefetches first so the memory latency overlaps the model updates below
    if(QPFI && bpn==7){ for(int yy=0;yy<2;yy++){ U32 cn=((c4<<8)|(((c0n<<1)|yy)&255))&0xffffff; __builtin_prefetch(ind2+((cn*0x9E3779B1u)>>(32-tbits))); } }
    if(QPF && bpn==3){ for(int yy=0;yy<2;yy++){ U64 nk=(U64)(((c0n<<1)|yy)+1)*0x9E3779B97F4A7C15ULL; nk^=nk>>29; for(int k=0;k<nm;k++) __builtin_prefetch(ht.bucket(H[k]^nk)); } }
    if(bpn==4){ U64 nk=(U64)(c0n+1)*0x9E3779B97F4A7C15ULL; nk^=nk>>29; for(int k=0;k<nm;k++){ H2[k]=H[k]^nk; __builtin_prefetch(dix[k]? (void*)dt[(size_t)(dix[k]-1)*17+1+(c0n&15)].st : (void*)ht.bucket(H2[k])); } }
    else if(bpn==8) byte_state_update(c0n&255);
    { U32*__restrict smp=smt; const U8*__restrict stp=st; U8*const*__restrict sl=slot; const int nd=node, n=nm; const U8*__restrict nx=&NX[0][y];
      int k=0; const int smq=(Q6>>23)&3; const bool sms = smq && (y? 65536-prf : prf) < (1024<<smq); // v6 bits 23-24: skip the state-map updates on a well-predicted bit
#ifdef __AVX2__
      // state-map updates first (8 models at a time), then the bit-history transitions in model order. The two touch
      // disjoint memory (maps vs hash slots), so this equals the interleaved scalar loop. (tg-p)*REC>>16 is computed
      // exactly in 32-bit lanes as ((tg-p)*(R>>8) + (((tg-p)*(R&255))>>8)) >> 8 (|tg-p| < 2^22, R < 2^16).
      { const __m256i lane=_mm256_setr_epi32(0,SMS,2*SMS,3*SMS,4*SMS,5*SMS,6*SMS,7*SMS), tg=_mm256_set1_epi32(y<<22), m1023=_mm256_set1_epi32(1023), m255=_mm256_set1_epi32(255), lim=_mm256_set1_epi32(SLIM), one=_mm256_set1_epi32(1);
        alignas(32) int nvo[8], ixo[8];
        if(!sms) for(;k+8<=n;k+=8){ __m256i ix=_mm256_add_epi32(_mm256_cvtepu8_epi32(_mm_loadl_epi64((const __m128i*)(stp+k))),_mm256_add_epi32(lane,_mm256_set1_epi32(k*SMS)));
          __m256i v=QZ_GATHER((const int*)smt,ix,4);
          __m256i cnt=_mm256_and_si256(v,m1023), p=_mm256_srli_epi32(v,10), a=_mm256_sub_epi32(tg,p);
          __m256i r=QZ_GATHER(REC,cnt,4);
          __m256i X=_mm256_mullo_epi32(a,_mm256_srli_epi32(r,8)), Y=_mm256_mullo_epi32(a,_mm256_and_si256(r,m255));
          p=_mm256_add_epi32(p,_mm256_srai_epi32(_mm256_add_epi32(X,_mm256_srai_epi32(Y,8)),8));
          cnt=_mm256_add_epi32(cnt,_mm256_and_si256(_mm256_cmpgt_epi32(lim,cnt),one));
          _mm256_store_si256((__m256i*)nvo,_mm256_or_si256(_mm256_slli_epi32(p,10),cnt)); _mm256_store_si256((__m256i*)ixo,ix);
          for(int j=0;j<8;j++) smt[ixo[j]]=(U32)nvo[j]; }
        smp+=(size_t)k*SMS; }
#endif
      if(!sms) for(int j=k;j<n;j++,smp+=SMS) SMap::upd_at(smp+stp[j],y,SLIM);
      for(int j=0;j<n;j++){ U8*sp=sl[j]+nd; *sp=nx[2*(*sp)]; } }
    for(int r=0;r<NRUN;r++) if(ract[r]) rsm[r].upd(y);
    o0.upd(y); o1d.upd(y); if(mact) mm.upd(y);
    M.upd(y);
    if(!((Q6>>25)&1) || (y? 65536-prf : prf) >= 1024){ // v6 bit 25: skip the final mixers + APM updates on a very well-predicted bit
      F.upd(y); if(QF2) F2.upd(y); if(QAPM&1) a1.upd(y); if(QAPM&2) a2.upd(y); if(QAPM&4) a3.upd(y); if(Q6&2) a4.upd(y); if(Q6&4) a5.upd(y); if(Q6&8192) a6.upd(y); if(nn) an.upd(y); }
    else { F.nx=0; F2.nx=0; }
    c0=c0n; bp=bpn;
    if(bp==4){ for(int k=0;k<nm;k++) slot[k]=dix[k]? dt[(size_t)(dix[k]-1)*17+1+(c0&15)].st : ht.find(H2[k]); }
    else if(bp==8){ if(mpend) match_update(); if(MLT && mlen>=MLT) byp=1; else lookup_slots(); c0=1; bp=0; }
  }
  // match-table lookup + insert for the byte just completed (deferred so its cache misses overlap the model updates)
  inline void match_try(U32 cand){ U32 cs=seglen? (cand/seglen)*seglen : 0; int lim=400; if((int)(pos-segstart)<lim) lim=pos-segstart; if((int)(cand-cs)<lim) lim=cand-cs;
    int l=0; while(l+8<=lim){ U64 a,b; memcpy(&a,buf+cand-8-l,8); memcpy(&b,buf+pos-8-l,8); U64 x=a^b; if(x){ l+=__builtin_clzll(x)>>3; goto dn; } l+=8; }
    while(l<lim && buf[cand-1-l]==buf[pos-1-l]) l++;
    dn: if(l>=6){ mlen=l; mptr=cand; } }
  inline void match_update(){
    mpend=0; const U32 i1=mi1, i2=mi2;
    if(mlen==0){ U32 cand=mt2[i2]; if(cand==0) cand=mt1[i1];
      if(Q6&8){ U32 c2=mt1[i1]; U32 cs[2]={cand,c2!=cand?c2:0}; for(int t=0;t<2&&mlen==0;t++) if(cs[t]) match_try(cs[t]); }
      // verify backwards, never past the start of our segment nor of the candidate's (those bytes may not be decoded yet)
      else if(cand>0){ U32 cs=seglen? (cand/seglen)*seglen : 0; int lim=400; if((int)(pos-segstart)<lim) lim=pos-segstart; if((int)(cand-cs)<lim) lim=cand-cs;
        int l=0;
        while(l+8<=lim){ U64 a,b; memcpy(&a,buf+cand-8-l,8); memcpy(&b,buf+pos-8-l,8); U64 x=a^b; if(x){ l+=__builtin_clzll(x)>>3; goto done; } l+=8; }
        while(l<lim && buf[cand-1-l]==buf[pos-1-l]) l++;
        done: if(l>=6){ mlen=l; mptr=cand; } } }
    mt1[i1]=pos; mt2[i2]=pos;
  }
  // Everything that depends on the completed byte except the hash-table lookups (which must follow the
  // model updates of the last bit, since a lookup may evict the slot being updated).
  void byte_state_update(int c){
    // run table update (contexts of the byte just coded)
    for(int r=0;r<NRUN;r++){ RunE*e=re[r]; U8 ck=rck[r];
      if(e->chk==ck && e->pad==rck2[r] && e->b==c){ if(e->n<255) e->n++; } else { e->chk=ck; e->pad=rck2[r]; e->b=c; e->n=1; } }
    ind1[c4&0xffff]=(U16)((ind1[c4&0xffff]<<8)|c); { U32 k=(c4&0xffffff)*0x9E3779B1u>>(32-tbits); ind2[k]=(U16)((ind2[k]<<8)|c); }
    { U64 o12=hist(12), o20=hist(20); oh12=(U64)c+OK*(oh12-o12*OK11); oh20=(U64)c+OK*(oh20-o20*OK19); }
    buf[pos++]=c; if(wrt && !role && pos>=9 && pos==9u+buf[5]){ wrt_roles(buf,rl); role=rl; }
    c8=(c8<<8)|(c4>>24); c4=(c4<<8)|c;
    wev=0; cev=0; const bool LT=(Q6&16)!=0;
    bool letter=(c>='a'&&c<='z')||(c>='A'&&c<='Z'); bool used=false;
    if(role){
      if(escpend){ escpend=0; used=true; }
      else if(codepend>0){ codepend--; codeh=hh(codeh,c); used=true; if(codepend==0){ w3=w2; w2=w1; w1=codeh; w0=0; inword=0; wev=1; cev=1; } }
      else if(role[c]>=4 && role[c]<=6){ if(inword){ w3=w2; w2=w1; w1=w0; w0=0; inword=0; wev=1; } codeh=hh(77,c); codepend=role[c]-4; used=true; if(codepend==0){ w3=w2; w2=w1; w1=codeh; w0=0; wev=1; cev=1; } }
      else if(role[c]==1){ escpend=1; used=true; }
      else if(role[c]) used=true;
    }
    if(!used){ if(letter){ w0=hh(w0+1,c|32); inword=1; } else if(inword){ w3=w2; w2=w1; w1=w0; w0=0; inword=0; wev=1; } }
    if(LT? wev : w1!=lastw1){ wn[(w2*0x9E3779B97F4A7C15ULL)>>(64-tbits)]=(U32)w1; if(wn2) wn2[(hh(w2,w3)*0x9E3779B97F4A7C15ULL)>>(64-tbits)]=(U32)w1; lastw1=w1; nw=nw<15?nw+1:15; }
    if(QXW&(128|4096)){
      if(!used && letter){ if(!inword||wlen==0) wcap=(c<'a'); wlen++; }
      if(LT? wev : w1!=lastw1x){ shp=((shp<<4)|(wcap<<3)|(wlen>7?7:wlen))&0xff; if(wlen>=4){ lw=lwc; lwc=w1; } wlen=0; wcap=0; lastw1x=w1; } }
    if((QXW&8192) && nd>0 && (LT? wev : w1!=lastw1n)){ if(!nsw[(nd-1)&15]) nsw[(nd-1)&15]=w1|1; } lastw1n=w1;
    if(QXW&1048576){ if(c=='\n'){ lfw=0; lfwset=0; } else if(!lfwset && (LT? wev : w1!=lastw1l)){ lfw=w1; lfwset=1; } lastw1l=w1; }
    if((QXW&(512|8192)) && !(LT&&used)){ if(c=='('||c=='['||c=='{'||c=='<'){ nst[nd&15]=c; nsw[nd&15]=0; nd++; } else if((c==')'||c==']'||c=='}'||c=='>')&&nd>0) nd--; else if(c=='\n'&&nd>0&&nst[(nd-1)&15]=='(') nd=0; }
    if(QXW && !(LT&&used)){ if(c=='.'||c=='!'||c=='?'||c=='\n') nw=0; if(!letter && !used && c!=' '){ sep=(sep<<8|c)&0xffff; } if(c=='\n') sepc=0; else if(c=='|'||c==','||c=='\t'||c==';') sepc=sepc<31?sepc+1:31; }
    if(LT&&used){} else if(c>='0'&&c<='9') num=hh(num+1,c); else if(c!='.'&&c!=',') num=0;
    // markup state: last structural opener and nesting hint
    if(LT&&used){} else if(c=='['||c=='{'||c=='<'||c=='|'||c=='='||c=='&'||c=='\n'||c=='('||c=='"'||c=='\'') mk=(mk<<8)|c; else if(c==']'||c=='}'||c=='>'||c==')') mk=(mk<<8)|c;
    mk&=0xffff;
    if(c=='\n'){ prevnl=lastnl; lastnl=pos; col=0; } else { col++; }
    if(Q6&131072){
      int cl = (used||letter)? 'a' : (c>='0'&&c<='9')? '0' : c;
      if(c=='\n'){ cfs[tfi&31]=tfs; for(int f=0;f<32;f++){ pfs[f]=cfs[f]; cfs[f]=0; } tsh=0; tfs=0; tfi=0; tlc=-1; }
      else { if(cl!=tlc || (cl!='a'&&cl!='0')){ tsh=hh(tsh,cl); tfs=hh(tfs,cl); } tlc=cl;
        if(!used && (c=='|'||c=='='||c==','||c=='\t'||c==';'||c=='!')){ cfs[tfi&31]=tfs; tfi=tfi<31?tfi+1:31; tfs=0; } }
      tstk = nd? hh(hh(nst[(nd-1)&15],nsw[(nd-1)&15]), hh(nd>1? hh(nst[(nd-2)&15],nsw[(nd-2)&15]) : 0, nd>15?15:nd)) : 0; }
    if(mlen>0){ if(buf[mptr]==c){ mlen++; mptr++; if(mlen>65535) mlen=65535; } else mlen=0; }
    M.gm = LRA? LRM + (int)((I64)LRA*LRD/(LRD+(pos>>10))) : LRM; // mixer learning-rate schedule (default constant)
    // hashes first so the bucket prefetches overlap the match-model work below
    if(Q6&512) cfl = codepend>0 ? 1 : cev ? 2 : 0;
    wcc=wc_cur(); compute_hashes();
    if(QPFW) prefetch_rows();
    if(pos-segstart>=24){
      // rolling polynomial hashes of the last 6 / 24 bytes: h = sum buf[pos-k]*MK^(L-k), rolled exactly via MK^-1 mod 2^64
      if(pos-segstart==24){ mh1=0; for(int k=1;k<=6;k++) mh1=mh1*MK+buf[pos-k]; mh2=0; for(int k=1;k<=24;k++) mh2=mh2*MK+buf[pos-k]; }
      else { mh1=(mh1-buf[pos-7])*MKINV+buf[pos-1]*MK5; mh2=(mh2-buf[pos-25])*MKINV+buf[pos-1]*MK23; }
      mi1=(U32)((mh1*0x9E3779B1u)>>(64-mbits))&((1u<<mbits)-1);
      mi2=(U32)((mh2*0x9E3779B97F4A7C15ULL)>>(64-mbits));
      __builtin_prefetch(mt2+mi2,1); __builtin_prefetch(mt1+mi1,1); mpend=1; // looked up in match_update(), after the bit-7 model updates
    }
  }
};
// ---------------- model configuration (stored in v4 stream headers) ----------------
// Defaults (= every field at its initial value above) reproduce the PR #2 / v3 model exactly.
struct QCfg { U32 drop; U8 sets, apm, f2, lrm, lra, apmr, pw; U16 lrd, slim; U32 xw; U8 xs; U32 f6, droph; };
static const int QCFG_BYTES=4+7+4, QCFG5_BYTES=QCFG_BYTES+5, QCFG6_BYTES=QCFG5_BYTES+8; // v5 adds xw (4 bytes) + xs
static QCfg cfg_get(){ QCfg c; c.drop=QDROP; c.sets=QSETS; c.apm=QAPM; c.f2=QF2; c.lrm=LRM; c.lra=LRA; c.apmr=APMR; c.pw=PW; c.lrd=LRD; c.slim=SLIM; c.xw=QXW; c.xs=QXS; c.f6=Q6; c.droph=QDROPH; return c; }
static void cfg_set(const QCfg&c){ QDROP=c.drop; QSETS=c.sets; QAPM=c.apm; QF2=c.f2; LRM=c.lrm; LRA=c.lra; APMR=c.apmr; PW=c.pw; LRD=c.lrd; SLIM=c.slim; QXW=c.xw; QXS=c.xs; Q6=c.f6; QDROPH=c.droph; }
static void cfg_put(std::vector<U8>&o,const QCfg&c){ for(int i=0;i<4;i++) o.push_back(c.drop>>(8*i)); o.push_back(c.sets); o.push_back(c.apm); o.push_back(c.f2); o.push_back(c.lrm); o.push_back(c.lra); o.push_back(c.apmr); o.push_back(c.pw);
  o.push_back(c.lrd&255); o.push_back(c.lrd>>8); o.push_back(c.slim&255); o.push_back(c.slim>>8);
  if(c.xw||c.xs||c.f6){ for(int i=0;i<4;i++) o.push_back(c.xw>>(8*i)); o.push_back(c.xs); } if(c.f6){ for(int i=0;i<4;i++) o.push_back(c.f6>>(8*i)); for(int i=0;i<4;i++) o.push_back(c.droph>>(8*i)); } }
static QCfg cfg_read(const U8*p){ QCfg c; c.drop=(U32)p[0]|(U32)p[1]<<8|(U32)p[2]<<16|(U32)p[3]<<24; c.sets=p[4]; c.apm=p[5]; c.f2=p[6]; c.lrm=p[7]; c.lra=p[8]; c.apmr=p[9]; c.pw=p[10]; c.lrd=p[11]|p[12]<<8; c.slim=p[13]|p[14]<<8; c.xw=0; c.xs=0; c.f6=0; c.droph=0; return c; }
static QCfg cfg_read5(const U8*p){ QCfg c=cfg_read(p); c.xw=(U32)p[15]|(U32)p[16]<<8|(U32)p[17]<<16|(U32)p[18]<<24; c.xs=p[19]; return c; }
static QCfg cfg_read6(const U8*p){ QCfg c=cfg_read5(p); c.f6=(U32)p[20]|(U32)p[21]<<8|(U32)p[22]<<16|(U32)p[23]<<24; c.droph=(U32)p[24]|(U32)p[25]<<8|(U32)p[26]<<16|(U32)p[27]<<24; return c; }
// presets: 2 (default) and 3 = PR #2 model (all 26 contexts); 1 = lean: 16 contexts (greedy ablation on e10m), match/run tables capped
// at 2^24. enwik8: lean is 1.47% larger, ~25% faster, 2.6 GB vs 9.6 GB. The learning-rate / SSE knobs (QZ_LRM/LRA/LRD/APMR/PW) gained 0.12% on e10m
// but lost 0.02% on enwik8, so no preset uses them.
static void cfg_preset(int lv){
  QDROP=0; QSETS=63; QAPM=7; QF2=1; LRM=16; LRA=0; LRD=1024; SLIM=1023; PW=1; APMR=7; QXW=0; QXS=0; Q6=0; QDROPH=0;
  if(lv<=1){ QDROP=21881408; }
}
static void cfg_env(){ if(getenv("QZ_DROP")) QDROP=(U32)strtoul(getenv("QZ_DROP"),0,0); QSETS=envi("QZ_SETS",QSETS); QAPM=envi("QZ_APM",QAPM); QF2=envi("QZ_F2",QF2);
  QXW=envi("QZ_XW",QXW); QXS=envi("QZ_XS",QXS); Q6=envi("QZ_Q6",Q6); QDROPH=(U32)envi("QZ_DROPH",QDROPH); if(QDROPH) Q6|=1u<<31; // bit 31 = "v6 header carries QDROPH"
  LRM=envi("QZ_LRM",LRM); LRA=envi("QZ_LRA",LRA); LRD=envi("QZ_LRD",LRD); SLIM=envi("QZ_SLIM",SLIM); PW=envi("QZ_PW",PW); APMR=envi("QZ_APMR",APMR); }
static void cfg_speed(){ QPF=envi("QZ_PF",QPF); QPFI=envi("QZ_PFI",QPFI); QPFW=envi("QZ_PFW",QPFW); }
static void initAll(){ cfg_speed(); initLog(); initStates(); initMatchConsts(); for(int i=0;i<1024;i++) REC[i]=(int)(65536.0/(i+1.6)); }

// bit-history machine: v6 flag 256 = variant that lets (2,n)/(n,2) histories decay to (1,n)/(n,1) on long runs
static void states_for_cfg(){ int v=(Q6>>8)&1; if(STVAR!=v) initStates(v); }
// ---------------- CM encode/decode of a buffer ----------------
struct Sizes { int hbits, mbits, tbits; };
static std::vector<U8> cm_encode(const U8*in,size_t n,Sizes z,const U8*role,bool preset=false){
  states_for_cfg(); Predictor *P=new Predictor(); P->init(z.hbits,z.mbits,z.tbits,n,role); if(preset&&role){ memcpy(P->rl,role,256); P->role=P->rl; }
  std::vector<U8> out; out.reserve(n/3+1024); Coder C; C.initEnc(&out);
  for(size_t i=0;i<n;i++){ int c=in[i]; for(int b=7;b>=0;b--){ int y=(c>>b)&1; C.code(y,P->pp()); P->update(y); } }
  C.flush(); delete P; return out;
}
static std::vector<U8> cm_decode(const U8*in,size_t inl,size_t n,Sizes z,const U8*role,bool preset=false){
  states_for_cfg(); Predictor *P=new Predictor(); P->init(z.hbits,z.mbits,z.tbits,n,role); if(preset&&role){ memcpy(P->rl,role,256); P->role=P->rl; }
  std::vector<U8> out(n); Coder C; C.initDec(in,inl);
  for(size_t i=0;i<n;i++){ int c=0; for(int b=0;b<8;b++){ int y=C.code(0,P->pp()); P->update(y); c=(c<<1)|y; } out[i]=c; }
  delete P; return out;
}

// stream format version: 2 = original int32 mixer, 3 = 16-bit mixer (incompatible streams)
#ifdef QZ_MIX32
#define QZC_VER 2
#ifdef QZ_MIX32_CFG // experimental A/B build: int32 mixer with the v5/v6 model config (stream ids 0x85/0x86, not a release format)
#define TEXT_XW 3374591u
#define QZC_VER5 0x85
#define QZC_VER6 0x86
#endif
#else
#define QZC_VER 4 // v4 = v3 + 15-byte model config (v3 streams still decode, as preset 3)
// prose default (wrt 2, -l 2): extra contexts chosen on e10m (greedy add, then ablation), enwik8 -1.99%:
// bits 1 sentence pos, 2 bigram->next word, 4 sparse b1/b3, 8 line-start byte, 16 separator, 32 skip-2 word, 64 separator count,
// 128 word shapes, 256 order3+word, 1024 skip w2, 2048 order2+column, 4096 last long word, 8192 bracket + its first word,
// 16384 line prefix + order2, 65536 last separators, 131072 order2 + bracket word, 1048576 first word of line, 2097152 line prefix + word
#define TEXT_XW 3374591u
#define QZC_VER5 5
// v5 = v4 + extra text contexts (xw, xs); written only when they are on, so other streams stay v4
#define QZC_VER6 6 // v6 = v5 + 4-byte model flags (Q6) + 4-byte drop mask for models 32..63; written only when Q6 != 0
// prose default since v6 (wrt 2, -l 2): Q6 = 16 logical-token word/bracket/markup events + 4 order-2 APM + 512 code-aware
// order-1 / c0 selectors + 1024 16-bit run-table check + 2048 Q14 first-layer weights (enwik8 numbers in bench/speed/)
#define TEXT_Q6 6753812u // 3604 + template model (131072) + vertical-byte field context (262144) + direct low-order tables (65536) + mixer-train skip below 1024/4096 error (6<<20)
#define TEXT_DROP 25184u // drop models 5,6,9,13,14 (greedy ablation, e3m)
#define TEXT_DROPH 44u // drop models 34,35,37
#endif
// ---------------- C API for lockstep use from another process/language (qznn.py) ----------------
// One coder, `nheads` CM heads sharing all learned tables; head s codes segment s of the n-byte buffer
// (segments of `seg` bytes, the last one shorter). The caller codes bytes in any order it likes as long as the
// decoder repeats it (qznn uses time-major order across streams). nnp[256] per byte: P(bit=1 | partial byte c0) in
// 16 bits, index c0=1..255, from an external model (may be NULL when the head was created with nn=0).
struct QzL { std::vector<Predictor*> H; Coder C; std::vector<U8> out; bool dec; };
extern "C" {
void* qzl_new(int hbits,int mbits,int tbits,unsigned long long n,int nn,int dec,const unsigned char*in,unsigned long long inl,int nheads,unsigned long long seg){
  static bool inited=false; if(!inited){ initAll(); inited=true; }
  QzL*q=new QzL(); q->dec=dec!=0; if(nheads<1) nheads=1;
  for(int s=0;s<nheads;s++){ Predictor*P=new Predictor(); P->nn=nn!=0; P->mbits=mbits; P->tbits=tbits;
    if(s==0){ P->init(hbits,mbits,tbits,(size_t)n,nullptr); P->seglen=nheads>1?(U32)seg:0; }
    else P->init_head(*q->H[0],(U32)(seg*s),(U32)seg);
    q->H.push_back(P); }
  if(dec) q->C.initDec(in,(size_t)inl); else { q->out.reserve((size_t)n/3+1024); q->C.initEnc(&q->out); }
  return q; }
void qzl_encode_byte(void*h,int head,int c,const unsigned short*nnp){ QzL*q=(QzL*)h; Predictor&P=*q->H[head]; P.nnp=nnp; for(int b=7;b>=0;b--){ int y=(c>>b)&1; q->C.code(y,P.pp()); P.update(y); } }
int qzl_decode_byte(void*h,int head,const unsigned short*nnp){ QzL*q=(QzL*)h; Predictor&P=*q->H[head]; P.nnp=nnp; int c=0; for(int b=0;b<8;b++){ int y=q->C.code(0,P.pp()); P.update(y); c=(c<<1)|y; } return c; }
unsigned long long qzl_finish(void*h,const unsigned char**out){ QzL*q=(QzL*)h; if(!q->dec) q->C.flush(); *out=q->out.data(); return q->out.size(); }
void qzl_free(void*h){ QzL*q=(QzL*)h; for(size_t s=q->H.size();s-->0;) delete q->H[s]; delete q; }
}

// ---------------- table sizing ----------------
// Hash table: 2^hbits slots of 16 bytes. ~log2(n)+5 saturates compression (16 slots per input byte);
// capped by the memory limit (default: 3/4 of physical RAM, override with -m MB). hbits is stored in
// the stream header so the decoder allocates exactly the same tables.
static size_t phys_ram_bytes(){
#ifdef __linux__
  long pages=sysconf(_SC_PHYS_PAGES), psz=sysconf(_SC_PAGESIZE); if(pages>0&&psz>0) return (size_t)pages*(size_t)psz;
#endif
  return (size_t)8<<30;
}
static int ceil_log2(size_t n){ int b=0; while(b<40 && ((size_t)1<<b)<n) b++; return b; }
static size_t fixed_tables_bytes(size_t n,int mbits,int tbits,bool wn2=false){ return n+8 + ((size_t)8<<mbits) + ((size_t)(wn2?14:10)<<tbits) + 3*(65536ull*33*2) + ((size_t)10<<20) + (wn2? (size_t)(1024+4*65536)*17*16 : 0); } // prose default also allocates the S4 direct tables
static Sizes auto_sizes(size_t n,size_t memlimit,int hbits_req,bool wn2=false){
  Sizes z; int L=ceil_log2(n);
  z.mbits=std::max(18,std::min(26,L+1));  // match tables: 2 x 4 B x 2^mbits
  z.tbits=std::max(18,std::min(26,L+1));  // run table (4 B), indirect (2 B), word-successor (4 B) x 2^tbits
  if(getenv("QZ_MBITS")) z.mbits=atoi(getenv("QZ_MBITS")); if(getenv("QZ_TBITS")) z.tbits=atoi(getenv("QZ_TBITS"));
  int hb=hbits_req>0?hbits_req:std::max(16,std::min(32,L+5)); // 16 slots per input byte saturates compression
  if(hbits_req<=0) while(hb>16 && (((size_t)16<<hb)+fixed_tables_bytes(n,z.mbits,z.tbits,wn2))>memlimit) hb--;
  z.hbits=hb; return z;
}

// ---------------- QZip word transform (qwrt v2 logic, in-process) ----------------
// roles: 1=ESC 2=CAP 3=UPPER 7=NOSPACE 4/5/6 = lead byte of 1/2/3-byte word code
struct WrtParams { long maxd=8000000, minf=4; int K1=100,K2=40,K3=8, SP=1, ord=0; };
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
  if(P.ord){ // order words inside each code-length class so similar words get neighbouring codes (decoder reads the order from the header)
    auto key=[&](E*e){ std::string k((const char*)pool.data()+e->off,e->len); if(P.ord==2) std::reverse(k.begin(),k.end()); return k; };
    size_t b[4]={0,std::min(nd,cap1),std::min(nd,cap2),nd};
    for(int r=0;r<3;r++) std::sort(c.begin()+b[r],c.begin()+b[r+1],[&](E*x,E*y){ return key(x)<key(y); }); }
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
static void writef(const char*p,const std::vector<U8>&v){ FILE*f=fopen(p,"wb"); if(!f){perror(p);exit(1);} fwrite(v.data(),1,v.size(),f); fclose(f); }
static void put64(std::vector<U8>&o,U64 v){ for(int i=0;i<8;i++) o.push_back((v>>(8*i))&255); }
static U64 get64(const U8*p){ U64 v=0; for(int i=7;i>=0;i--) v=(v<<8)|p[i]; return v; }

#ifndef QZ_NO_MAIN
static void usage(){ fprintf(stderr,"qzc c in out [hbits|auto] [wrt: 0 off (default), 1 on, 2 prose dictionary] [-m MB] [-l 1|2|3]\n  -l: model preset, 1 lean (16 contexts, faster), 2 default, 3 = PR #2 model\n  hbits: log2 of hash-table slots (16 B each); default auto = log2(n)+5 capped by the memory limit\n  -m MB: memory limit for auto sizing (default 3/4 of physical RAM)\nqzc d in out\n"); }
// self-test of the bit-history machine: from every state, a long run of one bit value must reach a state whose
// prior confidence exceeds 88% and that is deterministic (no absorbing mixed state). Returns failures.
static int state_selftest(int fix,bool verbose){
  initStates(fix); int fails=0;
  for(int s=0;s<NSTATES;s++) for(int y=0;y<2;y++){ int t=s; for(int i=0;i<300;i++) t=NX[t][y];
    double n0=SN0[t], n1=SN1[t]; double conf=(y? n1*2+1 : n0*2+1)/(n0*2+n1*2+2); bool det = y? SN0[t]==0 : SN1[t]==0;
    if(conf<=0.88 || !det){ fails++; if(verbose && fails<=8) fprintf(stderr,"  state %d (%d,%d) run of %d -> (%d,%d) conf %.3f\n",s,SN0[s],SN1[s],y,SN0[t],SN1[t],conf); } }
  initStates(0); return fails; }
int main(int argc,char**argv){
  initAll();
  if(argc>=2 && !strcmp(argv[1],"selftest")){ int f0=state_selftest(0,true), f1=state_selftest(1,true);
    printf("states v0 (v4/v5 streams): %d absorbing/low-confidence runs\nstates v1 (v6 flag 256): %d\n",f0,f1); return f1?1:0; }
  if(argc<4){ usage(); return 1; }
  int hbits=0, wmode=-1, npos=0, level=2; size_t memlimit=phys_ram_bytes()/4*3;
  for(int i=4;i<argc;i++){
    if(!strncmp(argv[i],"-m",2)){ const char*v=argv[i][2]?argv[i]+2:(i+1<argc?argv[++i]:"0"); memlimit=(size_t)atol(v)<<20; }
    else if(!strncmp(argv[i],"-l",2)){ const char*v=argv[i][2]?argv[i]+2:(i+1<argc?argv[++i]:"2"); level=atoi(v); if(level<1||level>3){ usage(); return 1; } }
    else if(!strcmp(argv[i],"auto")){ npos++; }
    else if(npos==0){ hbits=atoi(argv[i]); npos++; }
    else if(npos==1){ wmode=atoi(argv[i]); npos++; }
    else { usage(); return 1; }
  }
  std::vector<U8> in=readf(argv[2]);
  if(argv[1][0]=='c'){
    Sizes z=auto_sizes(in.size(),memlimit,hbits,wmode==2 && level==2); // prose v5 also allocates wn2
    if(level==1){ if(!getenv("QZ_MBITS")) z.mbits=std::min(z.mbits,24); if(!getenv("QZ_TBITS")) z.tbits=std::min(z.tbits,24); }
    if(z.hbits<16||z.hbits>32){ fprintf(stderr,"hbits must be 16..32\n"); return 1; }
    std::vector<U8> o={'Q','Z','C',QZC_VER,(U8)z.hbits,(U8)z.mbits,(U8)z.tbits}; put64(o,in.size());
#if !defined(QZ_MIX32) || defined(QZ_MIX32_CFG)
    cfg_preset(level); if(wmode==2 && level==2){ QXW=TEXT_XW; QXS=1;
#ifdef TEXT_Q6
      Q6=TEXT_Q6; QDROP=TEXT_DROP; QDROPH=TEXT_DROPH; if(QDROPH) Q6|=1u<<31;
#endif
    } cfg_env();
    // segment-parallel mode (QZ_THREADS=T > 1, v6 flag bit 30): the coded buffer is cut into T equal segments, each coded by its own
    // model in its own thread (encoder and decoder); every segment gets 1/T of the memory budget. Faster, but each model
    // learns only its segment: size cost grows with T (measured in progress.md). T=1 (default) = the single-model stream.
    NTHR=envi("QZ_THREADS",1); if(NTHR<1) NTHR=1; if(NTHR>64) NTHR=64; while(NTHR>1 && in.size()<(size_t)NTHR*(2u<<20)) NTHR--;
    if(NTHR>1) Q6|=1u<<30;
    if(QXW||QXS) o[3]=QZC_VER5; if(Q6) o[3]=QZC_VER6; cfg_put(o,cfg_get());
#endif
    // word transform is opt-in. wrt 1 = PR #2 dictionary (measured 2.4% worse on enwik8 than raw text).
    // wrt 2 = prose dictionary: codes sorted alphabetically inside each code-length class (similar words get
    // neighbouring codes), no merged space, words seen >= 128 times. Measured (-m 12288, same session on a loaded
    // i9-14900KF): enwik8 17,781,803 B vs 17,833,950 raw (-0.29%), compress 123 s vs 191 s, decompress 196 s vs
    // 261 s, same RAM; e10m -0.11%; worse on code / JSON / token lists (+0.2..3%), so the router
    // only asks for it on prose. The decoder reads the dictionary order from the stream: no decoder change.
    U8 role[256]; std::vector<U8> t; bool useW = wmode==1 || wmode==2; (void)text_like;
    if(useW){ WrtParams P; if(wmode==2){ P.ord=1; P.SP=0; P.minf=128; }
      if(getenv("QZ_WORD_ORD")) P.ord=atoi(getenv("QZ_WORD_ORD")); if(getenv("QZ_SP")) P.SP=atoi(getenv("QZ_SP")); if(getenv("QZ_MINF")) P.minf=atoi(getenv("QZ_MINF")); if(getenv("QZ_K1")) P.K1=atoi(getenv("QZ_K1")); if(getenv("QZ_K2")) P.K2=atoi(getenv("QZ_K2"));
      useW = wrt_encode(in.data(),in.size(),P,t,role); }
    o.push_back(useW?1:0);
    const U8*src=in.data(); size_t sn=in.size();
    if(useW){ put64(o,t.size()); src=t.data(); sn=t.size(); fprintf(stderr,"wrt %zu -> %zu\n",in.size(),t.size()); }
    fprintf(stderr,"hbits %d (hash table %zu MB) mbits %d tbits %d\n",z.hbits,((size_t)16<<z.hbits)>>20,z.mbits,z.tbits);
    if(NTHR>1){
      U8 rl2[256]; if(useW) wrt_roles(t.data(),rl2);
      int T=NTHR; std::vector<size_t> off(T+1); for(int k=0;k<=T;k++) off[k]=sn*(size_t)k/T;
      std::vector<Sizes> zs(T); std::vector<std::vector<U8>> es(T); states_for_cfg();
      for(int k=0;k<T;k++){ zs[k]=auto_sizes(off[k+1]-off[k],memlimit/T,hbits>0? std::max(16,hbits-ceil_log2(T)) : 0,wmode==2 && level==2);
        if(level==1){ zs[k].mbits=std::min(zs[k].mbits,24); zs[k].tbits=std::min(zs[k].tbits,24); } }
      std::vector<std::thread> th; for(int k=0;k<T;k++) th.emplace_back([&,k]{ es[k]=cm_encode(src+off[k],off[k+1]-off[k],zs[k],useW?rl2:nullptr,true); });
      for(auto&x:th) x.join();
      o.push_back((U8)T); if(useW) o.insert(o.end(),rl2,rl2+256);
      for(int k=0;k<T;k++){ put64(o,off[k+1]-off[k]); o.push_back((U8)zs[k].hbits); o.push_back((U8)zs[k].mbits); o.push_back((U8)zs[k].tbits); put64(o,es[k].size()); }
      for(int k=0;k<T;k++) o.insert(o.end(),es[k].begin(),es[k].end());
      fprintf(stderr,"threads %d (segments of %zu B, hbits %d)\n",T,off[1],zs[0].hbits); writef(argv[3],o);
    } else { std::vector<U8> e=cm_encode(src,sn,z,useW?role:nullptr); o.insert(o.end(),e.begin(),e.end()); writef(argv[3],o); }
    fprintf(stderr,"%zu -> %zu\n",in.size(),o.size());
  } else {
    int ver=in.size()>=16? in[3] : 0; bool okv = ver==QZC_VER;
#ifndef QZ_MIX32
    okv = okv || ver==3 || ver==QZC_VER5 || ver==QZC_VER6;
#elif defined(QZ_MIX32_CFG)
    okv = okv || ver==QZC_VER5 || ver==QZC_VER6;
#endif
    if(in.size()<16||memcmp(in.data(),"QZC",3)||!okv){ fprintf(stderr,"bad\n"); return 1; }
    Sizes z{in[4],in[5],in[6]}; U64 n=get64(in.data()+7); size_t p=15;
#if !defined(QZ_MIX32) || defined(QZ_MIX32_CFG)
    if(ver==4 && QZC_VER==4){ if(in.size()<16+QCFG_BYTES){ fprintf(stderr,"bad\n"); return 1; } cfg_set(cfg_read(in.data()+p)); p+=QCFG_BYTES; }
    else if(ver==QZC_VER5){ if(in.size()<16+QCFG5_BYTES){ fprintf(stderr,"bad\n"); return 1; } cfg_set(cfg_read5(in.data()+p)); p+=QCFG5_BYTES; }
    else if(ver==QZC_VER6){ if(in.size()<16+QCFG6_BYTES){ fprintf(stderr,"bad\n"); return 1; } cfg_set(cfg_read6(in.data()+p)); p+=QCFG6_BYTES; }
    else cfg_preset(3);
#endif
    int useW=in[p++]; U64 sn=n;
    if(useW){ sn=get64(in.data()+p); p+=8; }
    static U8 role_dummy[256];
    if(Q6>>30&1){ // segment-parallel stream
      if(p>=in.size()){ fprintf(stderr,"bad\n"); return 1; } int T=in[p++]; U8 rl2[256]; if(useW){ memcpy(rl2,in.data()+p,256); p+=256; }
      std::vector<U64> sl(T), cl(T); std::vector<Sizes> zs(T);
      for(int k=0;k<T;k++){ sl[k]=get64(in.data()+p); p+=8; zs[k]=Sizes{in[p],in[p+1],in[p+2]}; p+=3; cl[k]=get64(in.data()+p); p+=8; }
      std::vector<size_t> so(T+1,0), co(T+1,p); for(int k=0;k<T;k++){ so[k+1]=so[k]+sl[k]; co[k+1]=co[k]+cl[k]; }
      if(so[T]!=sn || co[T]>in.size()){ fprintf(stderr,"bad\n"); return 1; }
      std::vector<U8> d(sn); states_for_cfg();
      std::vector<std::thread> th; for(int k=0;k<T;k++) th.emplace_back([&,k]{ std::vector<U8> s=cm_decode(in.data()+co[k],cl[k],sl[k],zs[k],useW?rl2:nullptr,true); memcpy(d.data()+so[k],s.data(),sl[k]); });
      for(auto&x:th) x.join();
      if(!useW){ writef(argv[3],d); return 0; }
      std::vector<U8> o; if(!wrt_decode(d.data(),d.size(),o)){ fprintf(stderr,"wrt decode fail\n"); return 1; }
      if(o.size()!=n){ fprintf(stderr,"size mismatch\n"); return 1; } writef(argv[3],o); return 0; }
    if(!useW){ std::vector<U8> d=cm_decode(in.data()+p,in.size()-p,sn,z,nullptr); writef(argv[3],d); }
    else { // role table is known only after the QWRT header is decoded: decode with lazy roles
      std::vector<U8> d=cm_decode(in.data()+p,in.size()-p,sn,z,(const U8*)role_dummy); std::vector<U8> o; if(!wrt_decode(d.data(),d.size(),o)){ fprintf(stderr,"wrt decode fail\n"); return 1; }
      if(o.size()!=n){ fprintf(stderr,"size mismatch\n"); return 1; } writef(argv[3],o); }
  }
  return 0;
}
#endif
