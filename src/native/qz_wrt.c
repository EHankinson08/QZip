/* QZip native text codecs. Copyright 2026 Elliott Hankinson. GPL-3.0-or-later (see LICENSE). */
/* qwrt: reversible word-replacing transform (WRT/XWRT-style) for text.
 * Usage: qwrt e in out [-n maxdict] [-m minfreq] [-k reserved] [-1 b1] [-2 b2]
 *        qwrt d in out
 * Format (all needed to decode is in the file):
 *  "QWRT" ver(1) nres(1) roles: esc,cap,upper(3 bytes) b1 b2 b3 (1 byte each) then
 *  b1+b2+b3 code bytes; u32 ndict; u32 dictlen; dict words '\n'-separated; body.
 * Words = maximal runs of ASCII letters. lowercase -> code; Capitalized -> CAP code;
 * ALLCAPS (len>=2) -> UPPER code; otherwise literal. Literal bytes that collide with
 * any reserved byte are prefixed by ESC. Every other byte passes through unchanged.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static unsigned char * __attribute__((unused)) readall(const char *p, size_t *n){
  FILE *f=fopen(p,"rb"); if(!f){perror(p);exit(1);} fseek(f,0,SEEK_END); long s=ftell(f); fseek(f,0,SEEK_SET);
  unsigned char *b=malloc(s+1); if(fread(b,1,s,f)!=(size_t)s){perror("read");exit(1);} fclose(f); *n=s; return b;
}
static int isL(int c){ return (c>='a'&&c<='z')||(c>='A'&&c<='Z'); }
static int lo(int c){ return (c>='A'&&c<='Z')?c+32:c; }

/* hash table of lowercase words */
typedef struct { uint32_t off, len; uint64_t cnt; int32_t code; } Ent;
static Ent *H; static size_t HCAP, HN; static unsigned char *POOL; static size_t PN, PCAP;
static uint64_t hw(const unsigned char *s, size_t n){ uint64_t h=1469598103934665603ULL; for(size_t i=0;i<n;i++){h^=s[i];h*=1099511628211ULL;} return h; }
static void grow(void);
static Ent *find(const unsigned char *s, size_t n, int ins){
  if(ins && HN*2>=HCAP) grow();
  size_t i=hw(s,n)&(HCAP-1);
  while(H[i].len){ if(H[i].len==n && !memcmp(POOL+H[i].off,s,n)) return &H[i]; i=(i+1)&(HCAP-1); }
  if(!ins) return NULL;
  if(PN+n>PCAP){ PCAP=(PCAP+n)*2; POOL=realloc(POOL,PCAP);} memcpy(POOL+PN,s,n);
  H[i].off=PN; H[i].len=n; H[i].cnt=0; H[i].code=-1; PN+=n; HN++; return &H[i];
}
static void grow(void){
  Ent *o=H; size_t oc=HCAP; HCAP=oc?oc*2:(1<<20); H=calloc(HCAP,sizeof(Ent));
  for(size_t j=0;j<oc;j++) if(o[j].len){ size_t i=hw(POOL+o[j].off,o[j].len)&(HCAP-1); while(H[i].len) i=(i+1)&(HCAP-1); H[i]=o[j]; }
  free(o);
}
static int cmpcnt(const void *a, const void *b){ const Ent *x=*(Ent**)a,*y=*(Ent**)b;
  /* benefit-weighted: cnt*(len) ; tie by len */
  uint64_t bx=x->cnt*(uint64_t)x->len, by=y->cnt*(uint64_t)y->len;
  if(x->cnt!=y->cnt) return x->cnt<y->cnt?1:-1; return bx<by?1:(bx>by?-1:0); }

static unsigned char *OB; static size_t ON, OCAP;
static inline void put(unsigned char c){ if(ON>=OCAP){OCAP=OCAP*2+1024;OB=realloc(OB,OCAP);} OB[ON++]=c; }
static void put32(uint32_t v){ for(int i=0;i<4;i++) put((v>>(8*i))&255); }


static void qwrt_reset(void){ free(H); H=NULL; HCAP=0; HN=0; free(POOL); POOL=NULL; PN=0; PCAP=0; OB=NULL; ON=0; OCAP=0; }
/* opts: maxd, minf, SP. returns 0 ok. output malloc'd in *out (caller frees with qz_free). Not thread-safe. */
int qz_wrt_encode(const unsigned char *in, size_t n, unsigned char **out, size_t *outn, long maxd_, long minf_, int SP_){
  qwrt_reset(); OCAP=n+n/8+(1<<20); OB=malloc(OCAP); ON=0;
  { long maxd=maxd_, minf=minf_; int K1=-1,K2=-1,K3=-1, SP=SP_;

    /* pass 1: count words (lowercased) */
    unsigned char *tmp=malloc(1<<20); grow();
    uint64_t hist[256]={0};
    for(size_t i=0;i<n;){
      if(isL(in[i])){ size_t j=i; while(j<n&&isL(in[j])) j++; size_t L=j-i;
        if(L>=2 && L<(1<<20)){ int up=0; for(size_t k=i;k<j;k++){ tmp[k-i]=lo(in[k]); }
          int allup=1, cap=(in[i]>='A'&&in[i]<='Z'); for(size_t k=i;k<j;k++){ if(!(in[k]>='A'&&in[k]<='Z')) allup=0; if(k>i && !(in[k]>='a'&&in[k]<='z')) cap=0; }
          int low=1; for(size_t k=i;k<j;k++) if(!(in[k]>='a'&&in[k]<='z')) low=0; (void)up;
          if(low||cap||allup){ Ent *e=find(tmp,L,1); e->cnt++; } }
        i=j; } else { hist[in[i]]++; i++; }
    }
    /* collect candidates */
    Ent **c=malloc(sizeof(Ent*)*HN); size_t nc=0;
    for(size_t j=0;j<HCAP;j++) if(H[j].len && (long)H[j].cnt>=minf) c[nc++]=&H[j];
    qsort(c,nc,sizeof(Ent*),cmpcnt);
    /* choose reserved bytes: rarest non-alnum bytes */
    int ord[256], no=0; for(int b=0;b<256;b++){ if(isL(b)||(b>='0'&&b<='9')||b=='\n'||b==' ') continue; ord[no++]=b; }
    for(int a=0;a<no;a++) for(int b=a+1;b<no;b++) if(hist[ord[b]]<hist[ord[a]]||(hist[ord[b]]==hist[ord[a]]&&ord[b]<ord[a])){int t=ord[a];ord[a]=ord[b];ord[b]=t;}
    if(K1<0) K1=100; if(K2<0) K2=40; if(K3<0) K3=8;
    int NF=SP?4:3; int K=NF+K1+K2+K3; if(K>no){fprintf(stderr,"too many reserved\n");{qwrt_reset();return 1;}}
    size_t cap1=K1, cap2=cap1+(size_t)K2*256, cap3=cap2+(size_t)K3*65536;
    size_t nd=nc; if(nd>(size_t)maxd) nd=maxd; if(nd>cap3) nd=cap3;
    /* drop words whose code isn't shorter than the word (len 2 with 2-byte code etc.) */
    size_t w=0; for(size_t j=0;j<nc && w<nd;j++){ size_t clen = w<cap1?1:(w<cap2?2:3); if(c[j]->len<=clen) continue; c[w++]=c[j]; }
    nd=w; for(size_t j=0;j<nd;j++) c[j]->code=(int32_t)j;
    unsigned char role[256]; memset(role,0,256); unsigned char res[256]; for(int k=0;k<K;k++){ res[k]=ord[k]; role[ord[k]]=1; }
    uint64_t esc=0; for(int k=0;k<K;k++) esc+=hist[ord[k]];
    if(getenv("QZIP_NATIVE_VERBOSE")) fprintf(stderr,"words distinct=%zu cand=%zu dict=%zu reserved=%d escapes=%llu\n",HN,nc,nd,K,(unsigned long long)esc);
    unsigned char ESC=res[0],CAP=res[1],UP=res[2],NS=res[3]; unsigned char *C1=res+NF,*C2=res+NF+K1,*C3=res+NF+K1+K2;
    put('Q');put('W');put('R');put('T');put(SP?2:1);put(K); put(K1);put(K2);put(K3); for(int k=0;k<K;k++) put(res[k]);
    size_t dl=0; for(size_t j=0;j<nd;j++) dl+=c[j]->len+1; put32(nd); put32(dl);
    for(size_t j=0;j<nd;j++){ for(uint32_t k=0;k<c[j]->len;k++) put(POOL[c[j]->off+k]); put('\n'); }
    size_t hdr=ON;
    for(size_t i=0;i<n;){
      if(isL(in[i])){ size_t j=i; while(j<n&&isL(in[j])) j++; size_t L=j-i; int done=0;
        if(L>=2 && L<(1<<20)){
          int low=1,cap=(in[i]>='A'&&in[i]<='Z'),allup=1;
          for(size_t k=i;k<j;k++){ int ch=in[k]; tmp[k-i]=lo(ch); if(!(ch>='a'&&ch<='z')) low=0; if(!(ch>='A'&&ch<='Z')) allup=0; if(k>i&&!(ch>='a'&&ch<='z')) cap=0; }
          if(low||cap||allup){ Ent *e=find(tmp,L,0);
            if(e && e->code>=0){ size_t x=e->code;
              if(SP){ if(i>0 && in[i-1]==' ' && ON>hdr && OB[ON-1]==' ') ON--; else put(NS); }
              if(!low) put(cap?CAP:UP);
              if(x<cap1) put(C1[x]); else if(x<cap2){ x-=cap1; put(C2[x>>8]); put(x&255);} else { x-=cap2; put(C3[x>>16]); put((x>>8)&255); put(x&255);} 
              done=1; } } }
        if(!done) for(size_t k=i;k<j;k++) put(in[k]);
        i=j;
      } else { unsigned char b=in[i++]; if(role[b]) put(ESC); put(b); }
    }
    if(getenv("QZIP_NATIVE_VERBOSE")) fprintf(stderr,"in=%zu hdr+dict=%zu out=%zu\n",n,hdr,ON);
    free(tmp); free(c);
  }
  *out=OB; *outn=ON; OB=NULL; free(H); H=NULL; HCAP=0; HN=0; free(POOL); POOL=NULL; PN=0; PCAP=0; return 0;
}
int qz_wrt_decode(const unsigned char *in, size_t n, unsigned char **out, size_t *outn){
  qwrt_reset(); OCAP=n*3+(1<<20); OB=malloc(OCAP); ON=0;
    if(memcmp(in,"QWRT",4)){fprintf(stderr,"bad magic\n");{free(OB);OB=NULL;return 1;}}
    int ver=in[4]; int NF=ver==2?4:3; size_t p=5; int K=in[p++]; int K1=in[p++],K2=in[p++],K3=in[p++]; unsigned char res[256]; memcpy(res,in+p,K); p+=K;
    uint32_t nd=in[p]|in[p+1]<<8|in[p+2]<<16|(uint32_t)in[p+3]<<24; p+=4; uint32_t dl=in[p]|in[p+1]<<8|in[p+2]<<16|(uint32_t)in[p+3]<<24; p+=4;
    uint32_t *wo=malloc(sizeof(uint32_t)*(nd+1)); uint32_t *wl=malloc(sizeof(uint32_t)*(nd+1)); size_t q=p; for(uint32_t j=0;j<nd;j++){ wo[j]=q; while(in[q]!='\n') q++; wl[j]=q-wo[j]; q++; }
    p+=dl;
    int role[256]; int idx[256]; memset(role,0,sizeof role);
    role[res[0]]=1; role[res[1]]=2; role[res[2]]=3; if(ver==2) role[res[3]]=7;
    for(int k=0;k<K1;k++){ role[res[NF+k]]=4; idx[res[NF+k]]=k; }
    for(int k=0;k<K2;k++){ role[res[NF+K1+k]]=5; idx[res[NF+K1+k]]=k; }
    for(int k=0;k<K3;k++){ role[res[NF+K1+K2+k]]=6; idx[res[NF+K1+K2+k]]=k; }
    size_t cap1=K1, cap2=cap1+(size_t)K2*256; int mode=0, ns=0;
    while(p<n){ unsigned char b=in[p++]; int r=role[b];
      if(r==0){ put(b); continue; }
      if(r==1){ put(in[p++]); continue; }
      if(r==7){ ns=1; continue; } if(r==2){ mode=1; continue; } if(r==3){ mode=2; continue; }
      size_t x; if(r==4) x=idx[b]; else if(r==5){ x=cap1+((size_t)idx[b]<<8)+in[p]; p++; } else { x=cap2+((size_t)idx[b]<<16)+((size_t)in[p]<<8)+in[p+1]; p+=2; }
      const unsigned char *s=in+wo[x]; uint32_t L=wl[x]; if(ver==2 && !ns) put(' ');
      for(uint32_t k=0;k<L;k++){ int ch=s[k]; if(mode==2||(mode==1&&k==0)) ch-=32; put(ch); }
      mode=0; ns=0; }
  
  free(wo); free(wl);
  *out=OB; *outn=ON; OB=NULL; return 0;
}
