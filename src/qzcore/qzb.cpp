// qzb — QZip fast mode: QZip word transform (in-process) + libbsc fork (BWT/libsais + QLFC, Apache-2.0).
// QZip parts: transform, container, parameter choice. License: GPL-3.0-or-later (QZip-authored);
// vendored libbsc keeps its Apache-2.0 license (see vendor/libbsc/LICENSE).
#define QZ_NO_MAIN
#include "qzc.cpp"
#include <chrono>
#include "vendor/libbsc/libbsc.h"
static double now(){ return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
static void p32(std::vector<U8>&o,U32 v){ for(int i=0;i<4;i++) o.push_back((v>>(8*i))&255); }
static U32 g32(const U8*p){ return p[0]|p[1]<<8|p[2]<<16|(U32)p[3]<<24; }
int main(int argc,char**argv){
  initAll();
  if(argc<4){ fprintf(stderr,"qzb c|d in out [wrt -1/0/1] [blockMB]\n"); return 1; }
  int feat=LIBBSC_FEATURE_FASTMODE|LIBBSC_FEATURE_MULTITHREADING;
  if(bsc_init(feat)!=LIBBSC_NO_ERROR){ fprintf(stderr,"bsc_init\n"); return 1; }
  std::vector<U8> in=readf(argv[2]); double t0=now();
  if(argv[1][0]=='c'){
    int wmode=argc>4?atoi(argv[4]):-1; size_t bmb=argc>5?atol(argv[5]):1024;
    int lzpH=envi("QZB_LZPH",16), lzpM=envi("QZB_LZPM",128), coder=envi("QZB_CODER",LIBBSC_CODER_QLFC_ADAPTIVE);
    std::vector<U8> t; U8 role[256]; bool useW = wmode==1 || (wmode<0 && text_like(in.data(),in.size()));
    if(useW){ WrtParams P; P.SP=envi("QZ_SP",0); P.maxd=envi("QZ_MAXD",20000); useW=wrt_encode(in.data(),in.size(),P,t,role); }
    if(!useW) t.swap(in);
    double t1=now();
    std::vector<U8> o={'Q','Z','B',1}; put64(o,useW?in.size():t.size()); o.push_back(useW?1:0); put64(o,t.size());
    size_t B=bmb<<20; if(B>(size_t)1<<30) B=(size_t)1<<30; size_t nb=t.empty()?0:(t.size()+B-1)/B; p32(o,(U32)nb);
    std::vector<U8> blk;
    for(size_t b=0;b<nb;b++){ size_t s=b*B, L=std::min(B,t.size()-s); blk.resize(L+LIBBSC_HEADER_SIZE+1024);
      int r=bsc_compress(t.data()+s,blk.data(),(int)L,lzpH,lzpM,LIBBSC_BLOCKSORTER_BWT,coder,feat);
      if(r==LIBBSC_NOT_COMPRESSIBLE) r=bsc_store(t.data()+s,blk.data(),(int)L,feat);
      if(r<0){ fprintf(stderr,"bsc err %d\n",r); return 1; } p32(o,(U32)r); o.insert(o.end(),blk.begin(),blk.begin()+r); }
    writef(argv[3],o);
    fprintf(stderr,"qzb %zu -> %zu (wrt %d %.2fs, bsc %.2fs)\n",useW?in.size():t.size(),o.size(),(int)useW,t1-t0,now()-t1);
  } else {
    if(in.size()<25||memcmp(in.data(),"QZB",3)){ fprintf(stderr,"bad\n"); return 1; }
    U64 n=get64(&in[4]); int useW=in[12]; U64 tn=get64(&in[13]); U32 nb=g32(&in[21]); size_t p=25;
    std::vector<U8> t(tn); size_t off=0;
    for(U32 b=0;b<nb;b++){ U32 cs=g32(&in[p]); p+=4; int bs=0,ds=0; bsc_block_info(&in[p],LIBBSC_HEADER_SIZE,&bs,&ds,feat);
      if(bsc_decompress(&in[p],cs,t.data()+off,ds,feat)!=LIBBSC_NO_ERROR){ fprintf(stderr,"bsc dec err\n"); return 1; } off+=ds; p+=cs; }
    if(useW){ std::vector<U8> o2; if(!wrt_decode(t.data(),t.size(),o2)||o2.size()!=n){ fprintf(stderr,"wrt fail\n"); return 1; } writef(argv[3],o2); }
    else writef(argv[3],t);
    fprintf(stderr,"qzb d %.2fs\n",now()-t0);
  }
  return 0;
}
