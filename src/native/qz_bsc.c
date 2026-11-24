/* QZip native text codecs. Copyright 2026 Elliott Hankinson. GPL-3.0-or-later (see LICENSE). */
/* qz_bsc.c: QZip in-process wrapper around vendored libbsc (Apache-2.0, Ilya Grebnov).
 * Container: "QZB1" u64 n, u32 block_bytes, u32 nblocks, then per block: u32 csize + libbsc block.
 * Blocks are compressed in parallel with OpenMP when threads>1 (each block is independent).
 */
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdio.h>
#include "libbsc/libbsc.h"
#ifdef _OPENMP
#include <omp.h>
#endif
static int inited=0;
void qz_free(void *p){ free(p); }
static void w32(unsigned char *p, uint32_t v){ for(int i=0;i<4;i++) p[i]=(v>>(8*i))&255; }
static uint32_t r32(const unsigned char *p){ return p[0]|p[1]<<8|p[2]<<16|(uint32_t)p[3]<<24; }
/* sorter: 1=BWT, 3..8 = STx; coder 1..3; lzp_hash 0 disables LZP; features: 1 fast, 2 MT */
int qz_bsc_compress(const unsigned char *in, size_t n, unsigned char **out, size_t *outn,
                    size_t block, int lzp_hash, int lzp_min, int sorter, int coder, int features, int threads){
  if(!inited){ if(bsc_init(features)!=LIBBSC_NO_ERROR) return -100; inited=1; }
  if(block==0||block>(size_t)1<<30) block=(size_t)1<<30;
  size_t nb=(n+block-1)/block; if(nb==0) nb=1;
  unsigned char **bo=calloc(nb,sizeof(void*)); int *bs=calloc(nb,sizeof(int)); int err=0;
#ifdef _OPENMP
  if(threads<1) threads=1;
  #pragma omp parallel for schedule(dynamic,1) num_threads(threads)
#endif
  for(long b=0;b<(long)nb;b++){
    size_t off=(size_t)b*block, len=n-off<block?n-off:block;
    bo[b]=malloc(len+LIBBSC_HEADER_SIZE+64);
    int r=bsc_compress(in+off,bo[b],(int)len,lzp_hash,lzp_min,sorter,coder,features);
    if(r==LIBBSC_NOT_COMPRESSIBLE) r=bsc_store(in+off,bo[b],(int)len,features);
    if(r<0){ err=r; bs[b]=0; } else bs[b]=r;
  }
  if(err){ for(size_t b=0;b<nb;b++) free(bo[b]); free(bo); free(bs); return err; }
  size_t tot=20; for(size_t b=0;b<nb;b++) tot+=4+bs[b];
  unsigned char *o=malloc(tot), *p=o; memcpy(p,"QZB1",4); p+=4; for(int i=0;i<8;i++) *p++=((uint64_t)n>>(8*i))&255;
  w32(p,(uint32_t)block); p+=4; w32(p,(uint32_t)nb); p+=4;
  for(size_t b=0;b<nb;b++){ w32(p,bs[b]); p+=4; memcpy(p,bo[b],bs[b]); p+=bs[b]; free(bo[b]); }
  free(bo); free(bs); *out=o; *outn=tot; return 0;
}
int qz_bsc_decompress(const unsigned char *in, size_t inn, unsigned char **out, size_t *outn, int features, int threads){
  if(!inited){ if(bsc_init(features)!=LIBBSC_NO_ERROR) return -100; inited=1; }
  if(inn<20||memcmp(in,"QZB1",4)) return -101;
  uint64_t n=0; for(int i=0;i<8;i++) n|=(uint64_t)in[4+i]<<(8*i);
  size_t block=r32(in+12), nb=r32(in+16);
  size_t *off=malloc(nb*sizeof(size_t)), p=20;
  for(size_t b=0;b<nb;b++){ if(p+4>inn){ free(off); return -102; } off[b]=p; p+=4+r32(in+p); }
  if(p!=inn){ free(off); return -103; }
  unsigned char *o=malloc(n?n:1); int err=0;
#ifdef _OPENMP
  if(threads<1) threads=1;
  #pragma omp parallel for schedule(dynamic,1) num_threads(threads)
#endif
  for(long b=0;b<(long)nb;b++){
    size_t ob=(size_t)b*block, len=n-ob<block?n-ob:block; uint32_t cs=r32(in+off[b]);
    int r=bsc_decompress(in+off[b]+4,(int)cs,o+ob,(int)len,features);
    if(r!=LIBBSC_NO_ERROR) err=r;
  }
  free(off);
  if(err){ free(o); return err; }
  *out=o; *outn=n; return 0;
}
