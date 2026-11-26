/* qze: QZip "everyday" tiers (fast / balanced). Copyright 2026 Elliott Hankinson. GPL-3.0-or-later.
 *
 *   qze c [-1|-2] [-T threads] [-b blockMB] [-L zstd_level] [-x] <in|-> <out|->   compress (-1 fast, default; -2 balanced)
 *   qze d [-T threads] <in|-> <out|->                                           decompress
 *
 * The input is cut into independent blocks (CSV/TSV: at line ends), each block is optionally run through QZip's CSV
 * column transform (transpose to column-major, numeric columns -> zigzag-delta varints, fixed decimals kept exact)
 * and then coded by one backend: zstd (vendored 1.5.7, BSD), libbsc (vendored 3.4.1, Apache-2.0; BWT + QLFC),
 * or LZMA (vendored LZMA SDK 26.06, public domain; Igor Pavlov). Quick may also apply a SQLite page-type grouping
 * transform before LZMA. Blocks are compressed AND decompressed in parallel. Every block carries the XXH64 of its
 * raw bytes; the decoder checks it. Portable C11 + libbsc (C++); threads via pthreads or Win32. LZMA itself is
 * built single-thread (Z7_ST); parallelism is at block level. No x86-only required intrinsics.
 *
 * Format "QZE1": magic[4] u8 ver=1 u8 tier u8 delim(0=none) u8 flags | u64 n | u32 nblocks
 *   per block: u8 backend(0 stored,1 zstd,2 bsc,3 lzma,4 lzma2,5 lzma shared-window,6 nested QZE1 stream = one tar
 *              segment, top level only) u8 transform(0 none,1 csv,2 sqlite-pages,3 log,4 json-keys,5 bcj)
 *              u32 rawlen u32 tlen u32 clen u64 xxh64
 *   LZMA payload is 5-byte props + packed bytes. SQLite transform is self-contained per block (QZSQL1).
 *   payloads follow in block order. */
#define _FILE_OFFSET_BITS 64
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <time.h>
#define ZSTD_STATIC_LINKING_ONLY
#include "zstd/zstd.h"
#define XXH_INLINE_ALL
#include "zstd/common/xxhash.h"
#include "../libbsc/libbsc.h"
#define Z7_ST 1
#include "LzmaLib.h"
#include "LzmaEnc.h"
#include "Lzma2Enc.h"
#include "Lzma2Dec.h"
#include "Alloc.h"
#include "xform.h"
#ifdef _WIN32
#include <windows.h>
#include <io.h>
#include <fcntl.h>
#else
#include <pthread.h>
#include <unistd.h>
#endif

typedef struct { uint8_t *b; size_t n, cap; } buf;
static void die(const char *m) { fprintf(stderr, "qze: %s\n", m); exit(1); }
static void bres(buf *x, size_t k) { if (x->n + k > x->cap) { x->cap = (x->n + k) * 2 + 4096; x->b = (uint8_t *)realloc(x->b, x->cap); if (!x->b) die("out of memory"); } }
static void put(buf *x, const void *s, size_t k) { bres(x, k); memcpy(x->b + x->n, s, k); x->n += k; }
static void pc(buf *x, int c) { bres(x, 1); x->b[x->n++] = (uint8_t)c; }
static void pv(buf *x, uint64_t v) { bres(x, 10); while (v >= 128) { x->b[x->n++] = (uint8_t)((v & 127) | 128); v >>= 7; } x->b[x->n++] = (uint8_t)v; }
static int gv(const uint8_t *b, size_t n, size_t *p, uint64_t *v) { uint64_t r = 0; int s = 0; for (;;) { if (*p >= n || s > 63) return -1; uint8_t c = b[(*p)++]; r |= (uint64_t)(c & 127) << s; if (c < 128) break; s += 7; } *v = r; return 0; }
static void w32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static void w64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static uint32_t r32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t r64(const uint8_t *p) { uint64_t v = 0; for (int i = 7; i >= 0; i--) v = v << 8 | p[i]; return v; }
static void w32be(uint8_t *p, uint32_t v) { p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v; }
static uint32_t r32be(const uint8_t *p) { return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3]; }
static uint64_t zz(int64_t v) { return ((uint64_t)v << 1) ^ (uint64_t)(v >> 63); }
static int64_t uz(uint64_t v) { return (int64_t)(v >> 1) ^ -(int64_t)(v & 1); }

/* ------------------------------------------------------------------ CSV column transform */
#define MAXK 4096
static const int64_t P10[19] = {1LL,10LL,100LL,1000LL,10000LL,100000LL,1000000LL,10000000LL,100000000LL,1000000000LL,
  10000000000LL,100000000000LL,1000000000000LL,10000000000000LL,100000000000000LL,1000000000000000LL,
  10000000000000000LL,100000000000000000LL,1000000000000000000LL};
/* canonical number: -?(0|[1-9]\d*)(\.\d+)?, <= 18 digits, not negative zero. returns decimals (0..9) or -1 */
static int parse_num(const uint8_t *s, size_t k, int64_t *val) {
  size_t i = 0; int neg = 0;
  if (k == 0) return -1;
  if (s[0] == '-') { neg = 1; i = 1; }
  if (i >= k || s[i] < '0' || s[i] > '9') return -1;
  if (s[i] == '0' && i + 1 < k && s[i + 1] >= '0' && s[i + 1] <= '9') return -1;
  int64_t v = 0; int nd = 0, d = -1;
  for (; i < k; i++) {
    uint8_t c = s[i];
    if (c >= '0' && c <= '9') { if (++nd > 18) return -1; v = v * 10 + (c - '0'); if (d >= 0) d++; }
    else if (c == '.' && d < 0) { d = 0; if (i + 1 >= k) return -1; }
    else return -1;
  }
  if (d > 9) return -1;
  if (d < 0) d = 0;
  if (neg) { if (v == 0) return -1; v = -v; }
  *val = v; return d;
}
static size_t fmt_num(int64_t v, int d, char *o) {
  char t[32]; size_t n = 0; uint64_t a = v < 0 ? (uint64_t)(-v) : (uint64_t)v; int k = 0;
  do { t[k++] = (char)('0' + a % 10); a /= 10; } while (a || k <= d);
  if (v < 0) o[n++] = '-';
  for (int i = k - 1; i >= 0; i--) { o[n++] = t[i]; if (i == d && d > 0) o[n++] = '.'; }
  return n;
}
/* delimiter for the whole file from a sample: the candidate with the most lines sharing one nonzero count */
static int detect_delim(const uint8_t *in, size_t n) {
  const char cand[] = {',', '\t', ';', '|'}; size_t lim = n < (1 << 18) ? n : (1 << 18); int best = 0; double bestsc = 0;
  for (int c = 0; c < 4; c++) {
    int cnt[64] = {0}, lines = 0, cur = 0;
    for (size_t i = 0; i < lim; i++) { if (in[i] == '\n') { if (cur < 64) cnt[cur]++; lines++; cur = 0; } else if (in[i] == (uint8_t)cand[c]) cur++; }
    if (lines < 8) continue;
    int m = 0; for (int k = 1; k < 64; k++) if (cnt[k] > cnt[m]) m = k;
    double sc = (double)cnt[m] / lines;
    if (m >= 1 && sc >= 0.8 && sc * (m + 1) > bestsc) { bestsc = sc * (m + 1); best = cand[c]; }
  }
  return best;
}
/* encode one block of whole lines; returns 0 on success, -1 if the block does not fit the model */
static int csv_encode(const uint8_t *in, size_t n, int delim, int first, int usenum, buf *out) {
  size_t nl = 0; for (size_t i = 0; i < n; i++) nl += in[i] == '\n';
  int lastnl = n && in[n - 1] == '\n'; size_t nlines = nl + (lastnl ? 0 : 1);
  if (nlines < 16) return -1;
  /* field count mode */
  uint32_t *hist = (uint32_t *)calloc(MAXK + 2, 4); size_t p = 0;
  uint32_t *fc = (uint32_t *)malloc(nlines * 4);
  /* quote-aware: a delimiter inside "..." is part of the field; a line that ends inside quotes is an exception */
  for (size_t L = 0; L < nlines; L++) { uint32_t c = 1; int iq = 0; size_t q = p; while (q < n && in[q] != '\n') { if (in[q] == '"') iq ^= 1; else if (!iq) c += in[q] == delim; q++; } fc[L] = iq ? 0 : c; if (fc[L] <= MAXK) hist[fc[L]]++; p = q + 1; }
  hist[0] = 0; uint32_t K = 1; for (uint32_t k = 2; k <= MAXK; k++) if (hist[k] > hist[K]) K = k;
  free(hist);
  if (K < 2) { free(fc); return -1; }
  /* line starts, exceptions, crlf */
  size_t *ls = (size_t *)malloc((nlines + 1) * sizeof(size_t)); p = 0;
  for (size_t L = 0; L < nlines; L++) { ls[L] = p; while (p < n && in[p] != '\n') p++; p++; }
  ls[nlines] = n + (lastnl ? 0 : 1);
  uint8_t *exc = (uint8_t *)calloc(nlines, 1); size_t nexc = 0, ncr = 0;
  for (size_t L = 0; L < nlines; L++) {
    if (fc[L] != K || (first && L == 0)) { exc[L] = 1; nexc++; continue; }
    size_t e = ls[L + 1] - 1; if (e > ls[L] && in[e - 1] == '\r') ncr++;
  }
  size_t ngood = nlines - nexc;
  if (ngood < 8 || nexc * 4 > nlines) { free(fc); free(ls); free(exc); return -1; }
  int crlf = ncr == ngood;
  /* cell offsets: for good row r, field j = [off[r*(K+1)+j], off[r*(K+1)+j+1]-1) */
  uint32_t *off = (uint32_t *)malloc(ngood * (K + 1) * sizeof(uint32_t)); size_t r = 0;
  for (size_t L = 0; L < nlines; L++) {
    if (exc[L]) continue;
    uint32_t *o = off + r * (K + 1); size_t e = ls[L + 1] - 1 - (crlf ? 1 : 0); uint32_t j = 0; o[j++] = (uint32_t)ls[L];
    { int iq = 0; for (size_t q = ls[L]; q < e; q++) { if (in[q] == '"') iq ^= 1; else if (!iq && in[q] == delim) o[j++] = (uint32_t)(q + 1); } }
    o[K] = (uint32_t)(e + 1); r++;
  }
  free(fc);
  pc(out, 'C'); pc(out, delim); pc(out, (lastnl ? 1 : 0) | (crlf ? 2 : 0)); pv(out, nlines); pv(out, K); pv(out, nexc);
  buf ex = {0};
  { size_t prev = 0; for (size_t L = 0; L < nlines; L++) if (exc[L]) { pv(out, L - prev); prev = L; size_t e = ls[L + 1] - 1; put(&ex, in + ls[L], e - ls[L]); pc(&ex, '\n'); } }
  pv(out, ex.n); put(out, ex.b, ex.n); free(ex.b);
  buf cs = {0}, es = {0};
  for (uint32_t j = 0; j < K; j++) {
    int dh[10] = {0}; size_t nempty = 0; int64_t v;
    for (r = 0; r < ngood; r++) { uint32_t a = off[r * (K + 1) + j], b = off[r * (K + 1) + j + 1] - 1; if (a == b) { nempty++; continue; } int d = parse_num(in + a, b - a, &v); if (d >= 0) dh[d]++; }
    int bd = 0; for (int d = 1; d < 10; d++) if (dh[d] > dh[bd]) bd = d;
    int numeric = usenum && (double)(dh[bd] + nempty) >= 0.9 * ngood && dh[bd] > 0;
    cs.n = 0; es.n = 0;
    if (numeric) {
      int64_t prev = 0;
      for (r = 0; r < ngood; r++) {
        uint32_t a = off[r * (K + 1) + j], b = off[r * (K + 1) + j + 1] - 1;
        if (a == b) { pv(&cs, 0); continue; }
        int d = parse_num(in + a, b - a, &v);
        if (d != bd) { pv(&cs, 1); put(&es, in + a, b - a); pc(&es, '\n'); continue; }
        pv(&cs, zz(v - prev) + 2); prev = v;
      }
      pc(out, 1 + bd); pv(out, cs.n); put(out, cs.b, cs.n); pv(out, es.n); put(out, es.b, es.n);
    } else {
      for (r = 0; r < ngood; r++) { uint32_t a = off[r * (K + 1) + j], b = off[r * (K + 1) + j + 1] - 1; put(&cs, in + a, b - a); pc(&cs, '\n'); }
      pc(out, 0); pv(out, cs.n); put(out, cs.b, cs.n);
    }
  }
  free(cs.b); free(es.b); free(off); free(ls); free(exc);
  return 0;
}
typedef struct { const uint8_t *p, *e; } rd_t;
static int csv_decode(const uint8_t *t, size_t tn, uint8_t *o, size_t on) {
  size_t p = 0; uint64_t nlines, K, nexc, exn, v;
  if (tn < 3 || t[0] != 'C') return -1;
  int delim = t[1], lastnl = t[2] & 1, crlf = (t[2] >> 1) & 1; p = 3;
  if (gv(t, tn, &p, &nlines) || gv(t, tn, &p, &K) || gv(t, tn, &p, &nexc) || K < 1 || K > MAXK || nexc > nlines) return -1;
  size_t *ei = (size_t *)malloc((nexc + 1) * sizeof(size_t)); size_t prev = 0;
  for (size_t i = 0; i < nexc; i++) { if (gv(t, tn, &p, &v)) { free(ei); return -1; } prev += v; ei[i] = prev; }
  ei[nexc] = (size_t)-1;
  if (gv(t, tn, &p, &exn) || p + exn > tn) { free(ei); return -1; }
  rd_t ex = {t + p, t + p + exn}; p += exn;
  uint8_t *ty = (uint8_t *)malloc(K); rd_t *cr = (rd_t *)malloc(K * sizeof(rd_t)), *er = (rd_t *)malloc(K * sizeof(rd_t)); int64_t *pv_ = (int64_t *)calloc(K, 8);
  int bad = 0;
  for (size_t j = 0; j < K && !bad; j++) {
    if (p >= tn) { bad = 1; break; }
    ty[j] = t[p++]; uint64_t ln;
    if (gv(t, tn, &p, &ln) || p + ln > tn) { bad = 1; break; }
    cr[j].p = t + p; cr[j].e = t + p + ln; p += ln;
    if (ty[j]) { if (gv(t, tn, &p, &ln) || p + ln > tn) { bad = 1; break; } er[j].p = t + p; er[j].e = t + p + ln; p += ln; }
  }
  size_t w = 0, ne = 0; char nb[40];
#define OUT(src, k) do { if (w + (k) > on) { bad = 1; goto done; } memcpy(o + w, src, k); w += (k); } while (0)
#define LINE(rdr) do { const uint8_t *s_ = (rdr).p; while ((rdr).p < (rdr).e && *(rdr).p != '\n') (rdr).p++; if ((rdr).p >= (rdr).e) { bad = 1; goto done; } OUT(s_, (size_t)((rdr).p - s_)); (rdr).p++; } while (0)
  for (size_t L = 0; L < nlines && !bad; L++) {
    if (L == ei[ne]) { LINE(ex); ne++; }
    else for (size_t j = 0; j < K; j++) {
      if (j) { uint8_t dc = (uint8_t)delim; OUT(&dc, 1); }
      if (!ty[j]) { LINE(cr[j]); continue; }
      size_t q = 0; uint64_t c; size_t avail = (size_t)(cr[j].e - cr[j].p);
      if (gv(cr[j].p, avail, &q, &c)) { bad = 1; goto done; } cr[j].p += q;
      if (c == 0) continue;
      if (c == 1) { LINE(er[j]); continue; }
      pv_[j] += uz(c - 2); size_t k = fmt_num(pv_[j], ty[j] - 1, nb); OUT(nb, k);
    }
    if (crlf && !(ne > 0 && ei[ne - 1] == L)) { uint8_t c = '\r'; OUT(&c, 1); }
    if (L + 1 < nlines || lastnl) { uint8_t c = '\n'; OUT(&c, 1); }
  }
done:
  free(ei); free(ty); free(cr); free(er); free(pv_);
  return bad || w != on ? -1 : 0;
}

/* ------------------------------------------------------------------ threads */
typedef struct job_s job_t;
typedef void (*work_fn)(job_t *, size_t);
struct job_s { work_fn fn; size_t n, next; void *ctx;
#ifdef _WIN32
  CRITICAL_SECTION mu;
#else
  pthread_mutex_t mu;
#endif
};
static size_t take(job_t *j) { size_t i;
#ifdef _WIN32
  EnterCriticalSection(&j->mu); i = j->next++; LeaveCriticalSection(&j->mu);
#else
  pthread_mutex_lock(&j->mu); i = j->next++; pthread_mutex_unlock(&j->mu);
#endif
  return i; }
#ifdef _WIN32
static DWORD WINAPI worker(LPVOID a) { job_t *j = (job_t *)a; size_t i; while ((i = take(j)) < j->n) j->fn(j, i); return 0; }
#else
static void *worker(void *a) { job_t *j = (job_t *)a; size_t i; while ((i = take(j)) < j->n) j->fn(j, i); return NULL; }
#endif
static void run_par(work_fn fn, void *ctx, size_t n, int T) {
  job_t j; j.fn = fn; j.n = n; j.next = 0; j.ctx = ctx; if (T < 1) T = 1; if ((size_t)T > n) T = (int)n;
  if (T <= 1) { for (size_t i = 0; i < n; i++) fn(&j, i); return; }
#ifdef _WIN32
  InitializeCriticalSection(&j.mu); HANDLE *h = (HANDLE *)malloc(T * sizeof(HANDLE));
  for (int t = 0; t < T; t++) h[t] = CreateThread(NULL, 0, worker, &j, 0, NULL);
  WaitForMultipleObjects(T, h, TRUE, INFINITE); for (int t = 0; t < T; t++) CloseHandle(h[t]); free(h); DeleteCriticalSection(&j.mu);
#else
  pthread_mutex_init(&j.mu, NULL); pthread_t *h = (pthread_t *)malloc(T * sizeof(pthread_t));
  for (int t = 0; t < T; t++) pthread_create(&h[t], NULL, worker, &j);
  for (int t = 0; t < T; t++) pthread_join(h[t], NULL); free(h); pthread_mutex_destroy(&j.mu);
#endif
}
static int ncpu(void) {
#ifdef _WIN32
  SYSTEM_INFO si; GetSystemInfo(&si); return (int)si.dwNumberOfProcessors;
#else
  long c = sysconf(_SC_NPROCESSORS_ONLN); return c > 0 ? (int)c : 1;
#endif
}

/* ------------------------------------------------------------------ blocks */
enum { B_STORED = 0, B_ZSTD = 1, B_BSC = 2, B_LZMA = 3, B_LZMA2 = 4, B_LZMAP = 5, B_NEST = 6 };
typedef struct { const uint8_t *raw; size_t rawlen; uint8_t backend, transform; buf t; buf c; uint64_t h; int err; } blk_t;
typedef struct { blk_t *b; int tier, delim, zlevel, first_csv; int lzma_level; unsigned lzma_dict; unsigned pagesz; int lzp_min; int xf; int linecut; int pdf; const uint8_t *base; int primed; } cctx_t;
#ifndef LZMA_SOLID_MAX
#define LZMA_SOLID_MAX ((size_t)32 << 20)   /* solid lzma-9 only on files up to this size (Quick speed cap) */
#endif

static int is_texty(const uint8_t *s, size_t n) {
  size_t k = n < 65536 ? n : 65536, bad = 0; for (size_t i = 0; i < k; i++) { uint8_t c = s[i]; bad += (c < 9 || (c > 13 && c < 32) || c == 127); }
  return bad * 100 <= k;
}
static size_t zstd_into(buf *o, const uint8_t *s, size_t n, int level, int wlog) {
  ZSTD_CCtx *cc = ZSTD_createCCtx(); ZSTD_CCtx_setParameter(cc, ZSTD_c_compressionLevel, level);
  if (wlog) ZSTD_CCtx_setParameter(cc, ZSTD_c_windowLog, wlog);
  size_t cap = ZSTD_compressBound(n); o->n = 0; bres(o, cap);
  size_t r = ZSTD_compress2(cc, o->b, cap, s, n); ZSTD_freeCCtx(cc);
  if (ZSTD_isError(r)) return 0; o->n = r; return r;
}
static size_t bsc_into2(buf *o, const uint8_t *s, size_t n, int sorter, int coder, int lzpmin) {
  o->n = 0; bres(o, n + LIBBSC_HEADER_SIZE + 64);
  int lzph = 16;
  int r = bsc_compress(s, o->b, (int)n, lzpmin ? lzph : 0, lzpmin ? lzpmin : 0, sorter, coder, LIBBSC_FEATURE_FASTMODE);
  if (r < 0) return 0; o->n = (size_t)r; return (size_t)r;
}
static size_t bsc_into(buf *o, const uint8_t *s, size_t n, int coder) {
  o->n = 0; bres(o, n + LIBBSC_HEADER_SIZE + 64);
  int r = bsc_compress(s, o->b, (int)n, 16, 128, LIBBSC_BLOCKSORTER_BWT, coder, LIBBSC_FEATURE_FASTMODE);
  if (r < 0) return 0; o->n = (size_t)r; return (size_t)r;
}
/* Encoder-only LZMA overrides (0 = SDK level default): nice length, match cycles, lc0/lp2/pb2.
   lc/lp/pb travel in the 5-byte props header, so the decoder needs nothing new. */
static int g_lzma_fb = 0, g_lzma_mc = 0, g_lzma_lclppb = 0;
static size_t lzma_into(buf *o, const uint8_t *s, size_t n, int level, unsigned dict) {
  size_t cap = n + n / 3 + 65536 + LZMA_PROPS_SIZE;
  o->n = 0; bres(o, cap);
  size_t destLen = cap - LZMA_PROPS_SIZE, propsSize = LZMA_PROPS_SIZE;
  CLzmaEncProps pr; LzmaEncProps_Init(&pr); pr.level = level; pr.dictSize = dict; pr.numThreads = 2;
  if (g_lzma_fb) pr.fb = g_lzma_fb;
  if (g_lzma_mc) pr.mc = (unsigned)g_lzma_mc;
  if (g_lzma_lclppb) { pr.lc = 0; pr.lp = 2; pr.pb = 2; }
  int r = LzmaEncode(o->b + LZMA_PROPS_SIZE, &destLen, s, n, &pr, o->b, &propsSize, 0, NULL, &g_Alloc, &g_Alloc);
  if (r != SZ_OK || propsSize != LZMA_PROPS_SIZE) return 0;
  o->n = LZMA_PROPS_SIZE + destLen; return o->n;
}
static int lzma_from(uint8_t *dst, size_t want, const uint8_t *c, size_t cl) {
  if (cl < LZMA_PROPS_SIZE) return 1;
  size_t destLen = want, srcLen = cl - LZMA_PROPS_SIZE;
  int r = LzmaUncompress(dst, &destLen, c + LZMA_PROPS_SIZE, &srcLen, c, LZMA_PROPS_SIZE);
  return !(r == SZ_OK && destLen == want);
}
/* Shared-window parallel LZMA (B_LZMAP): block i is raw LZMA whose dictionary is preset to all payload bytes
   before it, so 8 parallel blocks keep nearly all of a solid stream's long-range matches. The encoder's tree
   match finder covers only the last `dense` bytes before the block; older bytes are reached through a
   content-defined 12-byte hash table (see LzmaEnc_MemEncodePrimed). lc4 lp0 pb0, nice 273, mc 48.
   The decoder needs nothing new except order: block i decodes after blocks 0..i-1 (sequential pass). */
#define LZMAP_BLOCKS 8
static size_t g_lzmap_blocks = LZMAP_BLOCKS;
static size_t lzmap_into(buf *o, const uint8_t *base, size_t pre, size_t n, size_t total) {
  size_t cap = n + n / 3 + 65536 + LZMA_PROPS_SIZE;
  o->n = 0; bres(o, cap);
  size_t dense = total / LZMAP_BLOCKS; if (dense < ((size_t)2 << 20)) dense = (size_t)2 << 20; if (dense > ((size_t)12 << 20)) dense = (size_t)12 << 20;
  unsigned lb = 16; while (lb < 25 && ((size_t)1 << (lb + 2)) < total) lb++;
  CLzmaEncHandle h = LzmaEnc_Create(&g_Alloc);
  if (!h) return 0;
  CLzmaEncProps pr; LzmaEncProps_Init(&pr); pr.level = 9; pr.dictSize = (UInt32)(pre + n < 4096 ? 4096 : pre + n);
  pr.lc = 4; pr.lp = 0; pr.pb = 0; pr.fb = 273; pr.mc = 48; pr.numThreads = 1;
  size_t ps = LZMA_PROPS_SIZE, dl = cap - LZMA_PROPS_SIZE;
  int r = LzmaEnc_SetProps(h, &pr);
  if (r == SZ_OK) r = LzmaEnc_WriteProperties(h, o->b, &ps);
  if (r == SZ_OK) r = LzmaEnc_MemEncodePrimed(h, o->b + LZMA_PROPS_SIZE, &dl, base, pre, pre + n, dense, lb, &g_Alloc, &g_Alloc);
  LzmaEnc_Destroy(h, &g_Alloc, &g_Alloc);
  if (r != SZ_OK || ps != LZMA_PROPS_SIZE) return 0;
  o->n = LZMA_PROPS_SIZE + dl; return o->n;
}
static int lzmap_from(uint8_t *base, size_t pre, size_t want, const uint8_t *c, size_t cl) {
  if (cl < LZMA_PROPS_SIZE) return 1;
  CLzmaDec d; LzmaDec_Construct(&d);
  if (LzmaDec_AllocateProbs(&d, c, LZMA_PROPS_SIZE, &g_Alloc) != SZ_OK) return 1;
  if (pre + want > d.prop.dicSize && pre != 0) { LzmaDec_FreeProbs(&d, &g_Alloc); return 1; }
  d.dic = base; d.dicBufSize = pre + want; LzmaDec_Init(&d); d.dicPos = pre;
  if (pre) d.checkDicSize = d.prop.dicSize;   /* preset dictionary: base[0..pre) */
  SizeT sl = cl - LZMA_PROPS_SIZE; ELzmaStatus st;
  SRes r = LzmaDec_DecodeToDic(&d, pre + want, c + LZMA_PROPS_SIZE, &sl, LZMA_FINISH_END, &st);
  int bad = !(r == SZ_OK && d.dicPos == pre + want && sl == cl - LZMA_PROPS_SIZE);
  LzmaDec_FreeProbs(&d, &g_Alloc); return bad;
}
/* LZMA2 (raw-chunk fallback: incompressible 64 KB chunks are stored, unlike LZMA1). One prop byte + stream.
   Used for PDF split blocks: deflate bodies are mostly incompressible. Single-threaded per block. */
static size_t lzma2_into(buf *o, const uint8_t *s, size_t n, int text) {
  size_t cap = n + n / 16 + 65536;
  o->n = 0; bres(o, cap);
  CLzma2EncHandle h = Lzma2Enc_Create(&g_Alloc, &g_Alloc);
  if (!h) return 0;
  CLzma2EncProps pr; Lzma2EncProps_Init(&pr);
  /* text block (PDF objects + uncompressed streams): lc2 pb0, nice 64, mc 16, two-thread match finder.
     deflate-body block: lc0 pb0 (bytes are near-random), level-9 defaults otherwise. */
  pr.lzmaProps.level = 9; pr.lzmaProps.pb = 0;
  if (text) { pr.lzmaProps.lc = 2; pr.lzmaProps.fb = 64; pr.lzmaProps.mc = 16; pr.lzmaProps.numThreads = 2; }
  else { pr.lzmaProps.lc = 0; pr.lzmaProps.numThreads = 1; }
  pr.lzmaProps.reduceSize = n; pr.blockSize = LZMA2_ENC_PROPS_BLOCK_SIZE_SOLID; pr.numBlockThreads_Max = 1; pr.numTotalThreads = 1;
  if (Lzma2Enc_SetProps(h, &pr) != SZ_OK) { Lzma2Enc_Destroy(h); return 0; }
  Lzma2Enc_SetDataSize(h, n);
  o->b[0] = Lzma2Enc_WriteProperties(h);
  size_t dl = cap - 1;
  SRes r = Lzma2Enc_Encode2(h, NULL, o->b + 1, &dl, NULL, s, n, NULL);
  Lzma2Enc_Destroy(h);
  if (r != SZ_OK) return 0;
  o->n = 1 + dl; return o->n;
}
/* v1.1.1: solid LZMA2 at the SDK level-9 literal settings (lc3 lp0 pb2) for near-random binaries (sao): LZMA2
   stores chunks that do not compress, where LZMA1 pays a little on every one. Same B_LZMA2 stream format. */
static int g_solid2 = 0;
static size_t lzma2_bin_into(buf *o, const uint8_t *s, size_t n) {
  size_t cap = n + n / 16 + 65536;
  o->n = 0; bres(o, cap);
  CLzma2EncHandle h = Lzma2Enc_Create(&g_Alloc, &g_Alloc);
  if (!h) return 0;
  CLzma2EncProps pr; Lzma2EncProps_Init(&pr);
  pr.lzmaProps.level = 9; pr.lzmaProps.numThreads = 2;
  pr.lzmaProps.reduceSize = n; pr.blockSize = LZMA2_ENC_PROPS_BLOCK_SIZE_SOLID; pr.numBlockThreads_Max = 1; pr.numTotalThreads = 2;
  if (Lzma2Enc_SetProps(h, &pr) != SZ_OK) { Lzma2Enc_Destroy(h); return 0; }
  Lzma2Enc_SetDataSize(h, n);
  o->b[0] = Lzma2Enc_WriteProperties(h);
  size_t dl = cap - 1;
  SRes r = Lzma2Enc_Encode2(h, NULL, o->b + 1, &dl, NULL, s, n, NULL);
  Lzma2Enc_Destroy(h);
  if (r != SZ_OK) return 0;
  o->n = 1 + dl; return o->n;
}
static int lzma2_from(uint8_t *dst, size_t want, const uint8_t *c, size_t cl) {
  if (cl < 1) return 1;
  size_t dl = want, sl = cl - 1; ELzmaStatus st;
  SRes r = Lzma2Decode(dst, &dl, c + 1, &sl, c[0], LZMA_FINISH_END, &st, &g_Alloc);
  return !(r == SZ_OK && dl == want);
}
static unsigned sqlite_pagesz(const uint8_t *s, size_t n) {
  if (n < 100 || memcmp(s, "SQLite format 3", 15) || s[15] != 0) return 0;
  unsigned ps = ((unsigned)s[16] << 8) | s[17];
  if (ps == 1) ps = 65536;
  if (ps < 512 || ps > 65536 || (ps & (ps - 1))) return 0;
  return ps;
}
static int sqlite_encode(const uint8_t *s, size_t n, unsigned ps, int first_special, buf *o) {
  if (!ps || n < (size_t)ps) return 0;
  size_t np = n / ps, tlen = n % ps;
  uint8_t *types = (uint8_t *)malloc(np ? np : 1);
  if (!types) return 0;
  for (size_t i = 0; i < np; i++) {
    const uint8_t *p = s + i * ps;
    types[i] = (i == 0 && first_special && ps >= 101) ? p[100] : p[0];
  }
  o->n = 0;
  put(o, "QZSQL1", 6);
  uint8_t hdr[12]; w32be(hdr, ps); w32be(hdr + 4, (uint32_t)np); w32be(hdr + 8, (uint32_t)tlen); put(o, hdr, 12);
  put(o, types, np);
  for (int ty = 0; ty < 256; ty++)
    for (size_t i = 0; i < np; i++)
      if (types[i] == (uint8_t)ty) put(o, s + i * ps, ps);
  if (tlen) put(o, s + np * ps, tlen);
  free(types);
  return 1;
}
static int sqlite_decode(const uint8_t *s, size_t n, uint8_t *dst, size_t want) {
  if (n < 18 || memcmp(s, "QZSQL1", 6)) return -1;
  unsigned ps = r32be(s + 6); size_t np = r32be(s + 10), tlen = r32be(s + 14);
  if ((size_t)ps * np + tlen != want) return -1;
  if (18 + np + (size_t)ps * np + tlen != n) return -1;
  const uint8_t *types = s + 18, *body = s + 18 + np;
  size_t off[256], cnt[256];
  memset(cnt, 0, sizeof cnt);
  for (size_t i = 0; i < np; i++) cnt[types[i]]++;
  size_t acc = 0;
  for (int ty = 0; ty < 256; ty++) { off[ty] = acc; acc += cnt[ty]; }
  for (size_t i = 0; i < np; i++) {
    unsigned ty = types[i];
    memcpy(dst + i * ps, body + off[ty] * ps, ps);
    off[ty]++;
  }
  if (tlen) memcpy(dst + np * ps, body + np * ps, tlen);
  return 0;
}
#ifndef B2LV
#define B2LV 6
#endif
#ifndef B2MB
#define B2MB 8
#endif
#ifndef B2BIN
#define B2BIN 97   /* bsc must win by 3% on the sample (zstd decodes much faster) */
#endif
#define SAMPLE ((size_t)512 << 10)
static int try_csv(blk_t *b, int delim, int first, int usenum, buf *t) {
  t->n = 0;
  if (csv_encode(b->raw, b->rawlen, delim, first, usenum, t)) return 0;
  uint8_t *chk = (uint8_t *)malloc(b->rawlen + 1);   /* the transform must decode back; it is cheap to check */
  int ok = csv_decode(t->b, t->n, chk, b->rawlen) == 0 && memcmp(chk, b->raw, b->rawlen) == 0;
  free(chk); return ok;
}

static int try_xf(int (*enc)(const uint8_t*, size_t, uint8_t**, size_t*),
                  int (*dec)(const uint8_t*, size_t, uint8_t*, size_t),
                  blk_t *b, buf *t) {
  uint8_t *ob = NULL; size_t on = 0;
  if (!enc(b->raw, b->rawlen, &ob, &on) || !ob) return 0;
  uint8_t *chk = (uint8_t *)malloc(b->rawlen + 1);
  int ok = dec(ob, on, chk, b->rawlen) == 0 && memcmp(chk, b->raw, b->rawlen) == 0;
  if (ok) { t->b = ob; t->n = on; t->cap = on; }
  else free(ob);
  free(chk); return ok;
}
static void comp_block(job_t *j, size_t i) {
  cctx_t *C = (cctx_t *)j->ctx; blk_t *b = &C->b[i];
  b->h = XXH64(b->raw, b->rawlen, 0); b->transform = 0;
  if (C->tier == 1) {
    const uint8_t *src = b->raw; size_t sn = b->rawlen;
    if (C->delim && try_csv(b, C->delim, i == 0, 1, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = 1; }
    /* default levels: 3 after the CSV transform (the transform already did the heavy lifting), 5 otherwise */
    int lv = C->zlevel > 0 ? C->zlevel : (b->transform ? 3 : 5);
    b->backend = B_ZSTD; if (!zstd_into(&b->c, src, sn, lv, 0)) b->err = 1;
    if (!b->err && b->c.n >= sn) { b->backend = B_STORED; b->c.n = 0; put(&b->c, src, sn); }
    return;
  }
  if (C->tier == 2 && C->primed) {
    b->backend = B_LZMAP;
    if (!lzmap_into(&b->c, C->base, (size_t)(b->raw - C->base), b->rawlen, (size_t)C->primed)) b->err = 1;
    if (!b->err && b->c.n >= b->rawlen) { b->backend = B_STORED; b->c.n = 0; put(&b->c, b->raw, b->rawlen); }
    return;
  }
  if (C->tier == 2) {
    /* balanced: libbsc BWT + static QLFC on small parallel blocks for text, zstd -B2LV for binary. For CSV the
       variant (raw / transposed text / transposed + numeric) is picked by coding a ~512 KB head sample of each,
       so the full block is coded once. */
    int cand = 0;
    if (C->delim) {
      size_t sl = b->rawlen < SAMPLE ? b->rawlen : SAMPLE;
      while (sl < b->rawlen && b->raw[sl - 1] != '\n') sl++;
      blk_t sb = *b; sb.rawlen = sl; sb.t.b = NULL; sb.t.n = sb.t.cap = 0;
      size_t bestn = (size_t)-1; buf c = {0};
      for (int k = 0; k < 3; k++) {
        const uint8_t *src = b->raw; size_t sn = sl;
        if (k > 0) { if (!try_csv(&sb, C->delim, i == 0, k == 2, &sb.t)) continue; src = sb.t.b; sn = sb.t.n; }
        if (bsc_into2(&c, src, sn, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, C->lzp_min) && c.n < bestn) { bestn = c.n; cand = k; }
      }
      free(c.b); free(sb.t.b);
    }
    const uint8_t *src = b->raw; size_t sn = b->rawlen;
    if (C->xf == XF_LOG && try_xf(xf_log_encode, xf_log_decode, b, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = XF_LOG; }
    else if (C->xf == XF_JSON && try_xf(xf_json_encode, xf_json_decode, b, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = XF_JSON; }
    else if (C->xf == XF_BCJ && try_xf(xf_bcj_encode, xf_bcj_decode, b, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = XF_BCJ; }
    else if (cand > 0 && try_csv(b, C->delim, i == 0, cand == 2, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = 1; }
    else if (C->pagesz && sqlite_encode(b->raw, b->rawlen, C->pagesz, i == 0, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = 2; }
    if (C->pdf) {   /* PDF split: block 0 = header + rest (text-like), block 1 = deflate bodies */
      b->backend = B_LZMA2;
      if (!lzma2_into(&b->c, src, sn, i == 0)) b->err = 1;
      if (!b->err && b->c.n >= sn) { b->backend = B_STORED; b->c.n = 0; put(&b->c, src, sn); }
      return;
    }
    if (C->lzma_level > 0 && g_solid2 && !b->transform) {
      b->backend = B_LZMA2; if (!lzma2_bin_into(&b->c, src, sn)) b->err = 1;
      if (!b->err && b->c.n >= sn) { b->backend = B_STORED; b->c.n = 0; put(&b->c, src, sn); }
      return;
    }
    if (C->lzma_level > 0) {
      int lv = C->lzma_level; unsigned dict = C->lzma_dict;
      b->backend = B_LZMA; if (!lzma_into(&b->c, src, sn, lv, dict)) b->err = 1;
      if (!b->err && b->c.n >= sn) { b->backend = B_STORED; b->c.n = 0; put(&b->c, src, sn); }
      return;
    }
    int usebsc = b->transform || is_texty(src, sn);
    if (!usebsc) {   /* binary: BWT often beats LZ on tables / databases / executables; decide on a head sample */
      size_t sl = sn < SAMPLE ? sn : SAMPLE; buf c1 = {0}, c2 = {0}; int lg = 10; while (((size_t)1 << lg) < sl && lg < 27) lg++;
      size_t a1 = bsc_into2(&c1, src, sl, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, C->lzp_min), a2 = zstd_into(&c2, src, sl, C->zlevel, lg);
      usebsc = a1 && (!a2 || a1 * 100 < a2 * B2BIN); free(c1.b); free(c2.b);
    }
    if (usebsc) { b->backend = B_BSC; if (!bsc_into2(&b->c, src, sn, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, C->lzp_min)) b->err = 1; }
    else { int lg = 10; while (((size_t)1 << lg) < sn && lg < 27) lg++;
           b->backend = B_ZSTD; if (!zstd_into(&b->c, src, sn, C->zlevel, lg)) b->err = 1; }
    if (!b->err && b->c.n >= sn) { b->backend = B_STORED; b->c.n = 0; put(&b->c, src, sn); }
    return;
  }
  /* max (tier 3, the v1.1 prototype's balanced): candidates raw / CSV transposed text / CSV transposed + numeric;
     libbsc adaptive on big blocks for text, zstd -19 for binary; keep the smallest (up to 3 backend runs) */
  int lg = 10; while (((size_t)1 << lg) < b->rawlen && lg < 27) lg++;
  buf best = {0}; int bbe = -1, btr = 0; buf bt = {0};
  for (int cand = 0; cand < 3; cand++) {
    buf t = {0}; const uint8_t *src = b->raw; size_t sn = b->rawlen;
    if (cand > 0) { if (!C->delim || !try_csv(b, C->delim, i == 0, cand == 2, &t)) { free(t.b); continue; } src = t.b; sn = t.n; }
    buf c = {0}; int be;
    if (cand > 0 || is_texty(src, sn)) { be = B_BSC; if (!bsc_into(&c, src, sn, LIBBSC_CODER_QLFC_ADAPTIVE)) be = -1; }
    else { be = B_ZSTD; if (!zstd_into(&c, src, sn, C->zlevel, lg)) be = -1; }
    if (be >= 0 && c.n >= sn) { be = B_STORED; c.n = 0; put(&c, src, sn); }
    if (be >= 0 && (bbe < 0 || c.n < best.n)) { free(best.b); best = c; bbe = be; btr = cand > 0; free(bt.b); bt = t; }
    else { free(c.b); free(t.b); }
  }
  if (bbe < 0) { b->err = 1; return; }
  b->c = best; b->backend = (uint8_t)bbe; b->transform = (uint8_t)btr; free(b->t.b); b->t = bt;
}
typedef struct { const uint8_t *in; size_t inn; uint8_t *out; size_t *pay, *ooff; int err; size_t hdr; int pass; int depth, T2; } dctx_t;
static int qze_decompress(const uint8_t *in, size_t n, int T, int depth, uint8_t **outp, size_t *onp);
static void dec_block(job_t *j, size_t i) {
  dctx_t *D = (dctx_t *)j->ctx; const uint8_t *h = D->in + D->hdr + i * 22;
  int be = h[0], tr = h[1]; size_t raw = r32(h + 2), tl = r32(h + 6), cl = r32(h + 10); uint64_t xh = r64(h + 14);
  if ((be == B_LZMAP) != (D->pass == 1)) return;   /* shared-window blocks: second, sequential pass */
  const uint8_t *c = D->in + D->pay[i]; uint8_t *dst = D->out + D->ooff[i];
  uint8_t *tb = tr ? (uint8_t *)malloc(tl + 1) : dst; size_t want = tr ? tl : raw; int bad = 0;
  if (be == B_STORED) { if (cl != want) bad = 1; else memcpy(tb, c, cl); }
  else if (be == B_ZSTD) { size_t r = ZSTD_decompress(tb, want, c, cl); if (ZSTD_isError(r) || r != want) bad = 1; }
  else if (be == B_BSC) { if (bsc_decompress(c, (int)cl, tb, (int)want, LIBBSC_FEATURE_FASTMODE) != LIBBSC_NO_ERROR) bad = 1; }
  else if (be == B_LZMA) { if (lzma_from(tb, want, c, cl)) bad = 1; }
  else if (be == B_LZMA2) { if (lzma2_from(tb, want, c, cl)) bad = 1; }
  else if (be == B_LZMAP) { if (tr || lzmap_from(D->out, D->ooff[i], want, c, cl)) bad = 1; }
  else if (be == B_NEST) {   /* tar member segment: a complete inner QZE1 stream (one level only) */
    uint8_t *o2 = NULL; size_t n2 = 0;
    if (tr || D->depth > 0 || qze_decompress(c, cl, D->T2, D->depth + 1, &o2, &n2) || n2 != want) bad = 1;
    else memcpy(tb, o2, n2);
    free(o2);
  }
  else bad = 1;
  if (!bad && tr == 1) { if (csv_decode(tb, tl, dst, raw)) bad = 1; }
  else if (!bad && tr == 2) { if (sqlite_decode(tb, tl, dst, raw)) bad = 1; }
  else if (!bad && tr == XF_LOG) { if (xf_log_decode(tb, tl, dst, raw)) bad = 1; }
  else if (!bad && tr == XF_JSON) { if (xf_json_decode(tb, tl, dst, raw)) bad = 1; }
  else if (!bad && tr == XF_BCJ) { if (xf_bcj_decode(tb, tl, dst, raw)) bad = 1; }
  else if (!bad && tr) bad = 1;
  if (tr) free(tb);
  if (!bad && XXH64(dst, raw, 0) != xh) bad = 2;
  if (bad) D->err = bad;
}

/* Encoded size of [s, s+n) with a fixed block size (no thread-shrink). Used to pick -b. */
static size_t measure_enc(const uint8_t *s, size_t n, size_t bs, int tier, int delim, int zl, int T, int lzp) {
  if (bs == 0) bs = n ? n : 1;
  size_t cap = n / bs + 2, nb = 0, p = 0;
  blk_t *B = (blk_t *)calloc(cap + 1, sizeof(blk_t));
  while (p < n || nb == 0) {
    size_t e = p + bs < n ? p + bs : n;
    if (delim && e < n) { size_t q = e; while (q < n && s[q] != '\n') q++; e = q < n ? q + 1 : n; }
    if (nb >= cap) { cap *= 2; B = (blk_t *)realloc(B, (cap + 1) * sizeof(blk_t)); memset(B + nb, 0, (cap + 1 - nb) * sizeof(blk_t)); }
    B[nb].raw = s + p; B[nb].rawlen = e - p; nb++; p = e; if (n == 0) break;
  }
  cctx_t C = {B, tier, delim, zl, 1, 0, 0, 0, lzp, 0, 0};
  run_par(comp_block, &C, nb, T);
  size_t tot = 20 + nb * 22; int bad = 0;
  for (size_t i = 0; i < nb; i++) { if (B[i].err) bad = 1; tot += B[i].c.n; free(B[i].c.b); free(B[i].t.b); }
  free(B); return bad ? (size_t)-1 : tot;
}
static uint8_t *readall(const char *path, size_t *n) {
  FILE *f = strcmp(path, "-") ? fopen(path, "rb") : stdin; if (!f) die("cannot open input");
  buf b = {0}; uint8_t tmp[1 << 16]; size_t k; while ((k = fread(tmp, 1, sizeof tmp, f)) > 0) put(&b, tmp, k);
  if (f != stdin) fclose(f); *n = b.n; return b.b ? b.b : (uint8_t *)calloc(1, 1);
}
static void writeall(const char *path, const uint8_t *b, size_t n) {
  FILE *f = strcmp(path, "-") ? fopen(path, "wb") : stdout; if (!f) die("cannot open output");
  if (n && fwrite(b, 1, n, f) != n) die("write failed"); if (f != stdout) fclose(f); else fflush(f);
}

/* Shared-window route: ustar archives of 8 MB..1 GB that are not mostly .pyc or RISC executables
   (those keep their tuned solid LZMA). Header checks only, so other files pay nothing. */
static int lzmap_gate(const uint8_t *in, size_t n) {
  if (n < ((size_t)8 << 20) || n > ((size_t)1 << 30) || memcmp(in + 257, "ustar", 5)) return 0;
  if (xf_tar_ext_bytes(in, n, "pyc") * 4 >= n || xf_tar_risc_bytes(in, n) * 2 >= n) return 0;
  return 1;
}


/* ------------------------------------------------------------------ tar member segmentation (Quick)
   A ustar archive whose bytes are mostly large members (>= TARSEG_BIG each, >= half the archive, >= 2 of them)
   is cut at member boundaries and every segment is coded as if it were its own file (an inner QZE1 stream,
   block backend B_NEST). Each big member's data starts a segment, so it gets the same route (BWT text, solid
   LZMA, log/CSV transforms, ...) as the standalone file; the next header + padding ride at its tail. Runs of
   small members (>= TARSEG_RUN bytes) start at their first header, so they route like a small tarball.
   Header-only decision (no trial coding). Segments are coded one after another, each with all T threads. */
typedef struct { int tier, T, bmb, zl, nocsv, force_lzma, lzp_min, depth; } copt_t;
static void qze_compress(const uint8_t *in, size_t n, copt_t opt, buf *outb);
#ifndef TARSEG_BIG
#define TARSEG_BIG ((size_t)4 << 20)
#endif
#ifndef TARSEG_RUN
#define TARSEG_RUN ((size_t)1 << 20)
#endif
static int tar_num(const uint8_t *p, int k, size_t *v) {
  size_t r = 0; int i = 0, any = 0;
  while (i < k && p[i] == ' ') i++;
  for (; i < k && p[i] >= '0' && p[i] <= '7'; i++) { r = r * 8 + (size_t)(p[i] - '0'); any = 1; if (r > ((size_t)1 << 40)) return -1; }
  if (i < k && p[i] != 0 && p[i] != ' ') return -1;
  if (!any) r = 0;
  *v = r; return 0;
}
/* fills cuts[] (segment starts, cuts[0] = 0); returns the number of segments, 0 = do not segment */
static size_t tarseg_plan(const uint8_t *in, size_t n, size_t **cutsp) {
  if (n < ((size_t)16 << 20) || n > ((size_t)0xF0000000u) || memcmp(in + 257, "ustar", 5)) return 0;
  size_t cap = 1024, nm = 0, *hs = (size_t *)malloc(cap * sizeof(size_t)), *ds = (size_t *)malloc(cap * sizeof(size_t)), *dl = (size_t *)malloc(cap * sizeof(size_t));
  size_t p = 0, big = 0, nbig = 0; int ok = 1;
  while (p + 512 <= n) {
    const uint8_t *h = in + p; int zero = 1;
    for (int i = 0; i < 512; i++) if (h[i]) { zero = 0; break; }
    if (zero) break;
    size_t chk, sz; unsigned sum = 0;
    for (int i = 0; i < 512; i++) sum += (i >= 148 && i < 156) ? ' ' : h[i];
    if (tar_num(h + 148, 8, &chk) || chk != sum || tar_num(h + 124, 12, &sz)) { ok = 0; break; }
    size_t d0 = p + 512; if (d0 + sz > n || d0 + sz < d0) { ok = 0; break; }
    uint8_t ty = h[156];
    if (nm == cap) { cap *= 2; hs = (size_t *)realloc(hs, cap * sizeof(size_t)); ds = (size_t *)realloc(ds, cap * sizeof(size_t)); dl = (size_t *)realloc(dl, cap * sizeof(size_t)); }
    hs[nm] = p; ds[nm] = d0; dl[nm] = (ty == '0' || ty == 0 || ty == '7') ? sz : 0;
    if (dl[nm] >= TARSEG_BIG) { big += dl[nm]; nbig++; }
    nm++;
    p = d0 + ((sz + 511) & ~(size_t)511);
  }
  size_t ns = 0;
  if (ok && nbig >= 2 && big * 2 >= n) {
    size_t *cuts = (size_t *)malloc((2 * nm + 2) * sizeof(size_t)); cuts[ns++] = 0;
    int prev_big = 0;
    for (size_t m = 0; m < nm; m++) {
      int isbig = dl[m] >= TARSEG_BIG;
      if (isbig) { if (ds[m] > cuts[ns - 1]) cuts[ns++] = ds[m]; }
      else if (prev_big) {
        size_t e = n; for (size_t k = m; k < nm; k++) if (dl[k] >= TARSEG_BIG) { e = hs[k]; break; }
        if (e - hs[m] >= TARSEG_RUN && hs[m] > cuts[ns - 1]) cuts[ns++] = hs[m];
      }
      prev_big = isbig;
    }
    if (ns >= 2) *cutsp = cuts; else { free(cuts); ns = 0; }
  }
  free(hs); free(ds); free(dl);
  return ns;
}
static int tarseg_compress(const uint8_t *in, size_t n, copt_t opt, buf *outb) {
  size_t *cuts = NULL, ns = tarseg_plan(in, n, &cuts);
  if (ns < 2) return 0;
  buf *S = (buf *)calloc(ns, sizeof(buf));
  copt_t io = opt; io.depth = opt.depth + 1;
  for (size_t i = 0; i < ns; i++) {
    size_t a = cuts[i], e = i + 1 < ns ? cuts[i + 1] : n;
    qze_compress(in + a, e - a, io, &S[i]);
  }
  buf o = {0}; uint8_t hd[20]; memcpy(hd, "QZE1", 4); hd[4] = 1; hd[5] = (uint8_t)opt.tier; hd[6] = 0; hd[7] = 0; w64(hd + 8, n); w32(hd + 16, (uint32_t)ns);
  put(&o, hd, 20);
  for (size_t i = 0; i < ns; i++) {
    size_t a = cuts[i], e = i + 1 < ns ? cuts[i + 1] : n;
    uint8_t h[22]; h[0] = B_NEST; h[1] = 0; w32(h + 2, (uint32_t)(e - a)); w32(h + 6, 0); w32(h + 10, (uint32_t)S[i].n); w64(h + 14, XXH64(in + a, e - a, 0)); put(&o, h, 22);
  }
  for (size_t i = 0; i < ns; i++) { put(&o, S[i].b, S[i].n); free(S[i].b); }
  free(S); free(cuts);
  fprintf(stderr, "qze: %zu -> %zu B, %zu tar segments, tier %d\n", n, o.n, ns, opt.tier);
  *outb = o; return 1;
}

/* ------------------------------------------------------------------ long-match LZMA route (Quick)
   Logs with long repeated runs (Zookeeper / Apache / HPC / Android-class) code smaller with LZMA at nice 273 over a
   window covering everything before (the shared-window B_LZMAP blocks) than with BWT. A cheap header check
   (looks_logish) decides whether to run the one extra sample coder; the sample then decides. */
static int looks_logish(const uint8_t *s, size_t n) {
  size_t k = n < 65536 ? n : 65536, i = 0; int tot = 0, ok = 0;
  while (i < k && tot < 400) {
    uint8_t c = s[i];
    size_t e = i; while (e < k && s[e] != '\n') e++;
    if (e >= k && tot > 0) break;
    if (e > i) { tot++; if ((c >= '0' && c <= '9') || c == '[' || (e - i >= 4 && s[i+3] == ' ' && c >= 'A' && c <= 'Z')) ok++; }
    i = e + 1;
  }
  return tot >= 8 && ok * 5 >= tot * 4;
}
/* the same LZMA settings as one B_LZMAP block with no prefix (sample size estimate) */
static size_t lzma_long_into(buf *o, const uint8_t *s, size_t n) {
  size_t cap = n + n / 3 + 65536 + LZMA_PROPS_SIZE;
  o->n = 0; bres(o, cap);
  size_t destLen = cap - LZMA_PROPS_SIZE, propsSize = LZMA_PROPS_SIZE;
  CLzmaEncProps pr; LzmaEncProps_Init(&pr); pr.level = 9; pr.dictSize = (UInt32)(n < 4096 ? 4096 : n);
  pr.lc = 4; pr.lp = 0; pr.pb = 0; pr.fb = 273; pr.mc = 48; pr.numThreads = 2;
  int r = LzmaEncode(o->b + LZMA_PROPS_SIZE, &destLen, s, n, &pr, o->b, &propsSize, 0, NULL, &g_Alloc, &g_Alloc);
  if (r != SZ_OK || propsSize != LZMA_PROPS_SIZE) return 0;
  o->n = LZMA_PROPS_SIZE + destLen; return o->n;
}
/* shared-window block count for the log route: small files keep one or two blocks (each block restarts the
   LZMA models, which costs ~1% on a 10 MB log); 16 MB and up use the 8 parallel blocks */
static size_t log_lzmap_blocks(size_t n) { return n < ((size_t)8 << 20) ? 1 : n < ((size_t)16 << 20) ? 2 : LZMAP_BLOCKS; }
/* sample coders run in parallel (each is single-block; outputs only feed routing decisions) */
enum { SJ_BSC = 0, SJ_ZSTD = 1, SJ_LZMA8 = 2, SJ_LONG = 3 };
typedef struct { int kind; const uint8_t *s; size_t n; int zl, lg; size_t out; } sjob_t;
static void sample_job(job_t *j, size_t i) {
  sjob_t *J = &((sjob_t *)j->ctx)[i]; buf c = {0};
  if (J->kind == SJ_BSC) J->out = bsc_into2(&c, J->s, J->n, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, 0);
  else if (J->kind == SJ_ZSTD) J->out = zstd_into(&c, J->s, J->n, J->zl, J->lg);
  else if (J->kind == SJ_LZMA8) J->out = lzma_into(&c, J->s, J->n, 9, 8u << 20);
  else J->out = lzma_long_into(&c, J->s, J->n);
  free(c.b);
}
static void run_samples(sjob_t *J, size_t nj, int T) { run_par(sample_job, J, nj, T < (int)nj ? T : (int)nj); }
static size_t line_cut(const uint8_t *s, size_t n, size_t want) { size_t sl = n < want ? n : want; while (sl < n && s[sl - 1] != '\n') sl++; return sl; }

static void qze_compress(const uint8_t *in, size_t n, copt_t opt, buf *outb) {
  int tier = opt.tier, T = opt.T, bmb = opt.bmb, zl = opt.zl, nocsv = opt.nocsv, force_lzma = opt.force_lzma, lzp_min = opt.lzp_min;
  g_lzma_fb = 0; g_lzma_mc = 0; g_lzma_lclppb = 0; g_lzmap_blocks = LZMAP_BLOCKS; g_solid2 = 0;
  if (tier == 2 && opt.depth == 0 && !bmb && !force_lzma && !lzp_min && tarseg_compress(in, n, opt, outb)) return;
  {
    if (zl < 0) zl = tier == 1 ? 0 : tier == 2 ? B2LV : 19;   /* tier 1: 0 = per block (3 CSV-transformed, 5 other) */
    size_t bs = (size_t)(bmb > 0 ? bmb : (tier == 1 ? 4 : tier == 2 ? B2MB : 32)) << 20;
    int delim = nocsv ? 0 : detect_delim(in, n);
    int lzma_level = 0; unsigned lzma_dict = 0, pagesz = 0; int solid = 0, primed = 0;
    int xf = 0, linecut = 0, file_xf = 0; size_t pdf_cut = 0;
    uint8_t *xf_payload = NULL; size_t xf_len = 0; uint64_t orig_xxh = 0;
    /* Quick (tier 2): SQLite always gets page-type grouping + LZMA (8 MB blocks, 8 MB dict, level 9).
       Other files at or under LZMA_SOLID_MAX: a short sample decides. Text that prefers LZMA uses the same
       8 MB blocked path (healthapp-class logs). Binary uses solid lzma-9. Containers (ustar/PDF/ELF/PE/ZIP)
       up to 64 MB also use solid lzma-9; the two-thread match finder (LzFindMt) plus the per-tar tuning below keeps mozilla at ~7.5 MB/s.
       Larger files stay on BWT/zstd so the 719 MB mix stays >= 20 MB/s. */
    if (tier == 2) {
      pagesz = sqlite_pagesz(in, n);
      if (pagesz) { delim = 0; lzma_level = 9; lzma_dict = 8u << 20; if (!bmb) bs = (size_t)8 << 20; }
      else if (looks_health(in, n) || looks_syslog(in, n)) {
        xf = XF_LOG; delim = 0; linecut = 1;
        /* health: 4 MB blocks keep ≥8 MB/s and beat brotli. syslog: 16 MB BWT blocks. */
        if (!bmb) {
          if (looks_health(in, n)) bs = (size_t)4 << 20;
          else if (n > ((size_t)16 << 20)) bs = (size_t)16 << 20;
          else bs = (size_t)8 << 20;
        }
        if (!looks_health(in, n)) {
          /* v1.1.1: syslog whose transformed sample codes larger with BWT than the raw sample with long-match
             LZMA (Linux.log-class: few repeated hosts/procs, long repeated messages) -> shared-window LZMA. */
          size_t sl = line_cut(in, n, (size_t)4 << 20);
          uint8_t *ob = NULL; size_t on = 0;
          if (!bmb && xf_log_encode(in, sl, &ob, &on) && ob) {
            sjob_t J[2] = {{SJ_BSC, ob, on, 0, 0, 0}, {SJ_LONG, in, sl, 0, 0, 0}};
            run_samples(J, 2, T);
            size_t a1 = J[0].out, a4 = J[1].out;
            /* LZMA must win by 8% (Mac.log: 2% on the sample, BWT wins the whole file by 8%) */
            if (a4 && a1 && a4 * 100 < a1 * 92) { xf = 0; linecut = 0; primed = 1; g_lzmap_blocks = log_lzmap_blocks(n); }
            free(ob);
          }
        }
        if (looks_health(in, n)) {
          /* After the log transform, LZMA usually beats BWT (the header is binary). Sample both. */
          size_t sl = n < bs ? n : bs;
          while (sl < n && in[sl - 1] != '\n') sl++;
          uint8_t *ob = NULL; size_t on = 0;
          if (xf_health_encode(in, sl, &ob, &on) && ob) {
            sjob_t J[2] = {{SJ_BSC, ob, on, 0, 0, 0}, {SJ_LZMA8, ob, on, 0, 0, 0}};
            run_samples(J, 2, T);
            size_t a1 = J[0].out, a3 = J[1].out;
            if (a3 && (!a1 || a3 < a1)) { lzma_level = 9; lzma_dict = 8u << 20; }
            free(ob);
          }
          /* Whole-file transform, then 4 MB LZMA blocks of the xf bytes (per-block
             transforms lose ~100 KB to restarting deltas and miss brotli). */
          if (xf_health_encode(in, n, &xf_payload, &xf_len) && xf_payload) {
            file_xf = XF_LOG; orig_xxh = XXH64(in, n, 0); xf = 0; /* blocks see already-transformed data */
            /* v1.1.1: the transformed stream codes ~12% smaller as shared-window LZMA (nice 273) than as 4 MB
               LZMA-8MB blocks (HealthApp 1,248,636 -> ~1,100,000 B) at a similar speed with 8 blocks */
            if (lzma_level && !bmb) { primed = 1; lzma_level = 0; lzma_dict = 0; g_lzmap_blocks = log_lzmap_blocks(xf_len); }
          }
        }
      } else if (looks_ndjson(in, n)) {
        xf = XF_JSON; delim = 0; linecut = 1;
        if (!bmb && n > ((size_t)32 << 20)) bs = (size_t)32 << 20;
      } else if (!bmb && lzmap_gate(in, n)) {
        primed = 1; delim = 0;
      } else if (!bmb && looks_logcat(in, n) && xf_logcat_encode(in, n, &xf_payload, &xf_len) && xf_payload) {
        /* v1.1.1: Android logcat (threadtime): time / pid / tid / level split into delta streams, then shared-window
           LZMA (Android_v1: 7,951,386 -> ~4.9 MB, about 2x faster than BWT+LZMA on the raw text would need).
           The transform is checked by decoding it here. */
        uint8_t *chk = (uint8_t *)malloc(n ? n : 1);
        if (chk && !xf_logcat_decode(xf_payload, xf_len, chk, n) && !memcmp(chk, in, n)) {
          file_xf = XF_LOG; orig_xxh = XXH64(in, n, 0); delim = 0; primed = 1; g_lzmap_blocks = log_lzmap_blocks(xf_len);
        } else { free(xf_payload); xf_payload = NULL; xf_len = 0; }
        free(chk);
      } else if (!bmb && n > LZMA_SOLID_MAX && !delim && is_texty(in, n) && looks_logish(in, n)) {
        /* v1.1.1: other big logs: 4 MB sample, BWT vs long-match LZMA; LZMA -> shared-window blocks */
        size_t sl = line_cut(in, n, (size_t)4 << 20);
        sjob_t J[2] = {{SJ_BSC, in, sl, 0, 0, 0}, {SJ_LONG, in, sl, 0, 0, 0}};
        run_samples(J, 2, T);
        size_t a1 = J[0].out, a4 = J[1].out;
        if (a4 && a1 && a4 < a1) { primed = 1; g_lzmap_blocks = LZMAP_BLOCKS; }
      } else if (n <= LZMA_SOLID_MAX || (n <= ((size_t)64 << 20) && n >= 262 && !memcmp(in + 257, "ustar", 5))) {
        /* Tarballs / PDFs / executables: a short prefix is a poor predictor (BWT can win the first
           few MB of a ustar of source, then lose on the whole file). Use solid lzma-9 directly.
           Ustar up to 64 MB is included so mozilla can use solid LZMA + sort; the 32 MB cap stays
           for non-containers (openstack, youth, Panel would otherwise drop the mix under 20 MB/s). */
        int is_ustar = (n >= 262 && !memcmp(in + 257, "ustar", 5));
        int container = is_ustar;
        if (n >= 4 && (!memcmp(in, "%PDF", 4) || !memcmp(in, "ELF", 4) || !memcmp(in, "PK", 4))) container = 1;
        if (n >= 2 && in[0] == 'M' && in[1] == 'Z') container = 1;
        /* v1.1.1: text-heavy ustar / PDF (silesia xml: a tar of XML files; reymont: an uncompressed text PDF) codes
           far smaller with BWT. Three 384 KB samples (start, 1/3, 2/3); BWT must win by 15% so source tarballs and
           ordinary PDFs keep LZMA. .pyc- and RISC-heavy tars keep their tuned LZMA without sampling. */
        int textcont = 0;
        if (container && n >= ((size_t)1 << 20) && (is_ustar || !memcmp(in, "%PDF", 4)) &&
            !(is_ustar && (xf_tar_ext_bytes(in, n, "pyc") * 4 >= n || xf_tar_risc_bytes(in, n) * 2 >= n))) {
          size_t q = (size_t)384 << 10, at[3] = {0, n / 3, 2 * n / 3}; uint8_t *sm = (uint8_t *)malloc(3 * q); size_t sn = 0;
          for (int k = 0; k < 3; k++) { size_t a0 = at[k], l = n - a0 < q ? n - a0 : q; memcpy(sm + sn, in + a0, l); sn += l; }
          sjob_t J[2] = {{SJ_BSC, sm, sn, 0, 0, 0}, {SJ_LZMA8, sm, sn, 0, 0, 0}};
          run_samples(J, 2, T);
          size_t ab = J[0].out, al = J[1].out;
          if (ab && al && ab * 100 < al * 85) { container = 0; textcont = 1; }
          free(sm);
        }
        if (container) {
          lzma_level = 9; lzma_dict = 0; if (!bmb) { bs = n ? n : 1; solid = 1; }
          if (is_ustar) {
            xf = XF_BCJ;
            /* Python bytecode gains little from long matches or deep match search: >= 25% .pyc ->
               fb 48, mc 16 (mixed_pystdlib: 5,360,713 B, ~10% faster than 7z-9; source and
               binary tars keep the SDK level-9 defaults). Encoder-only; the stream format is unchanged. */
            if (xf_tar_ext_bytes(in, n, "pyc") * 4 >= n) { g_lzma_fb = 48; g_lzma_mc = 16; }
            /* >= 50% fixed-width RISC executables (mozilla: Tru64 Alpha COFF): 4-byte literal
               alignment saves ~2.5%; spend part of it on a shallower match search (mc 12). */
            else if (xf_tar_risc_bytes(in, n) * 2 >= n) { g_lzma_lclppb = 1; g_lzma_fb = 64; g_lzma_mc = 12; }
            if (xf_tar_sort_encode(in, n, &xf_payload, &xf_len) && xf_payload) {
              file_xf = XF_TAR; orig_xxh = XXH64(in, n, 0);
            }
          } else if (n >= 4 && !memcmp(in, "%PDF", 4)) {
            /* zlib-headed stream bodies go after the rest; two LZMA2 blocks run in parallel
               (paper.pdf: ~2.3 KB under brotli-11, faster than 7z-9). Verified by a decode below. */
            size_t cut = xf_pdf_split_encode(in, n, &xf_payload, &xf_len);
            if (cut && xf_payload) {
              uint8_t *chk = (uint8_t *)malloc(n ? n : 1);
              if (chk && !xf_pdf_split_decode(xf_payload, xf_len, chk, n) && !memcmp(chk, in, n)) {
                file_xf = XF_PDF; orig_xxh = XXH64(in, n, 0); pdf_cut = cut;
              } else { free(xf_payload); xf_payload = NULL; xf_len = 0; }
              free(chk);
            }
          } else if (n >= 4 && !memcmp(in, "\x7f" "ELF", 4)) xf = XF_BCJ;
          else if (n >= 2 && in[0] == 'M' && in[1] == 'Z') xf = XF_BCJ;
        }
        else if (!textcont) {
          /* logs / prose: a 4 MB sample is enough (1 MB missed healthapp; 4 MB does not). */
          size_t sl = n < ((size_t)4 << 20) ? n : ((size_t)4 << 20);
          int lg = 10; while (((size_t)1 << lg) < sl && lg < 27) lg++;
          /* v1.1.1: the sample coders run in parallel; long-match LZMA joins them for log-like text */
          int logish = is_texty(in, n) && looks_logish(in, n);
          sjob_t J[4] = {{SJ_BSC, in, sl, 0, 0, 0}, {SJ_ZSTD, in, sl, zl, lg, 0}, {SJ_LZMA8, in, sl, 0, 0, 0}, {SJ_LONG, in, sl, 0, 0, 0}};
          run_samples(J, logish ? 4 : 3, T);
          size_t a1 = J[0].out, a2 = J[1].out, a3 = J[2].out, a4 = logish ? J[3].out : 0;
          /* pipe-delimited logs (healthapp) look like CSV to detect_delim; only keep CSV if it actually helps. */
          size_t a_csv = 0;
          if (delim) {
            blk_t sb = {0}; sb.raw = in; sb.rawlen = sl;
            if (try_csv(&sb, delim, 1, 0, &sb.t) || try_csv(&sb, delim, 1, 1, &sb.t)) {
              buf cc = {0};
              a_csv = bsc_into2(&cc, sb.t.b, sb.t.n, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, 0);
              free(cc.b);
            }
            free(sb.t.b);
          }
          int lzma_wins = a3 && (!a1 || a3 < a1) && (!a2 || a3 < a2) && (!a_csv || a3 < a_csv);
          if (!bmb && a4 && (!a1 || a4 < a1) && (!a2 || a4 < a2) && (!a_csv || a4 < a_csv)) {
            lzma_wins = 0; delim = 0; primed = 1; g_lzmap_blocks = log_lzmap_blocks(n);
          }
          if (lzma_wins) {
            delim = 0;   /* raw LZMA beat the CSV path — don't force a log through the table transform */
            if (is_texty(in, n)) { lzma_level = 9; lzma_dict = 8u << 20; if (!bmb) bs = (size_t)8 << 20; }
            else { lzma_level = 9; lzma_dict = 0; if (!bmb) { bs = n ? n : 1; solid = 1; g_solid2 = 1; } }
          }
        }
      }
    }
    if (force_lzma > 0) {
      primed = 0;
      lzma_level = force_lzma;
      delim = 0;
      if (pagesz) {
        if (!lzma_dict) lzma_dict = 8u << 20;
        if (!bmb) bs = (size_t)8 << 20;
        solid = 0;
      } else if (is_texty(in, n)) {
        lzma_dict = 8u << 20;
        if (!bmb) bs = (size_t)8 << 20;
        solid = 0;
      } else {
        lzma_dict = 0;
        if (!bmb) { bs = n ? n : 1; solid = 1; }
      }
    }
    const uint8_t *payload = in; size_t pn = n;
    if (file_xf && xf_payload) { payload = xf_payload; pn = xf_len; linecut = 0; delim = 0; if (solid && !bmb) bs = pn ? pn : 1; }
    /* blocks: CSV cuts at line ends; SQLite cuts at page boundaries; at least T blocks when the input is big enough */
    /* spread big inputs over the threads, but never below the tier's minimum block (ratio) unless -b is given */
    size_t minb = (size_t)(tier == 1 ? 1 : tier == 2 ? 2 : 16) << 20;
    if (!bmb && !solid && !xf && !file_xf && n / T < bs) { size_t e = (n + T - 1) / T; bs = e > minb ? e : minb; }
    /* balanced, no -b: on a 16 MB prefix, pick 8 MB vs 16 MB blocks (the winner of the two) for the whole file.
       skipped when the user passed -b (golden uses -b 2), when LZMA is already chosen, or on binaries. */
    /* Quick, no -b, not already on LZMA:
       - 8 vs 16 MB on a 16 MB prefix for text/CSV (existing).
       - Non-CSV text larger than 32 MB uses 32 MB blocks directly (a 16 MB prefix cannot
         tell 16 from 32, and a 32 MB prefix probe dropped the mix under 20 MB/s).
       - LZP-32 is chosen from an 8 MB prefix (cheap) when it shrinks that prefix.
       Skipped when the user passed -b (goldens use -b 2) or -P (LZP already chosen). */
    if (tier == 2 && !bmb && !primed && !lzma_level && !file_xf && n > ((size_t)16 << 20) && (delim || is_texty(in, n)) && xf != XF_LOG) {
      if (!delim && n > ((size_t)32 << 20)) {
        bs = (size_t)32 << 20; bmb = 32;
      } else {
        size_t pn = (size_t)16 << 20;
        if (delim) { while (pn < n && in[pn] != '\n') pn++; if (pn < n) pn++; }
        size_t s8 = measure_enc(in, pn, (size_t)8 << 20, tier, delim, zl, T, lzp_min);
        size_t s16 = measure_enc(in, pn, (size_t)16 << 20, tier, delim, zl, T, lzp_min);
        if (s16 != (size_t)-1 && s8 != (size_t)-1 && s16 < s8) { bs = (size_t)16 << 20; bmb = 16; }
      }
      if (lzp_min == 0) {
        size_t pn8 = n < ((size_t)8 << 20) ? n : ((size_t)8 << 20);
        if (delim) { while (pn8 < n && in[pn8] != '\n') pn8++; if (pn8 < n) pn8++; }
        size_t z0 = measure_enc(in, pn8, pn8, tier, delim, zl, T, 0);
        size_t z32 = measure_enc(in, pn8, pn8, tier, delim, zl, T, 32);
        if (z32 != (size_t)-1 && z0 != (size_t)-1 && z32 < z0) lzp_min = 32;
      }
    }
    if (primed) bs = (pn + g_lzmap_blocks - 1) / g_lzmap_blocks;   /* fixed 8 blocks: output does not depend on -T */
    size_t cap = pn / (bs > 1 ? bs : 1) + 2, nb = 0, p = 0; blk_t *B = (blk_t *)calloc(cap + 1, sizeof(blk_t));
    if (pdf_cut && pdf_cut < pn) {
      /* block 0 = header + rest, block 1 = all deflate bodies (splitting them costs ~25 KB on paper.pdf:
         bodies are similar font/image subsets) */
      B[0].raw = payload; B[0].rawlen = pdf_cut; B[1].raw = payload + pdf_cut; B[1].rawlen = pn - pdf_cut; nb = 2; p = pn;
    }
    while (p < pn || nb == 0) {
      size_t e = p + bs < pn ? p + bs : pn;
      if ((delim || linecut) && e < pn) { size_t q = e; while (q < pn && payload[q] != '\n') q++; e = q < pn ? q + 1 : pn; }
      if (pagesz && e < pn) { size_t al = e - (e % pagesz); if (al > p) e = al; }
      if (nb >= cap) { cap *= 2; B = (blk_t *)realloc(B, (cap + 1) * sizeof(blk_t)); memset(B + nb, 0, (cap + 1 - nb) * sizeof(blk_t)); }
      B[nb].raw = payload + p; B[nb].rawlen = e - p; nb++; p = e; if (pn == 0) break;
    }
    cctx_t C = {B, tier, delim, zl, 1, lzma_level, lzma_dict, pagesz, lzp_min, xf, linecut, pdf_cut ? 1 : 0, payload, primed ? (int)(pn > 0x7fffffff ? 0x7fffffff : pn) : 0};
    run_par(comp_block, &C, nb, T);
    buf o = {0}; uint8_t hd[20]; memcpy(hd, "QZE1", 4); hd[4] = 1; hd[5] = (uint8_t)tier; hd[6] = (uint8_t)delim; hd[7] = (uint8_t)file_xf; w64(hd + 8, n); w32(hd + 16, (uint32_t)nb);
    put(&o, hd, 20);
    if (file_xf) { uint8_t ext[16]; w64(ext, xf_len); w64(ext + 8, orig_xxh); put(&o, ext, 16); }
    for (size_t i = 0; i < nb; i++) {
      if (B[i].err) die("block compression failed");
      uint8_t h[22]; h[0] = B[i].backend; h[1] = B[i].transform; w32(h + 2, (uint32_t)B[i].rawlen); w32(h + 6, (uint32_t)(B[i].transform ? B[i].t.n : 0)); w32(h + 10, (uint32_t)B[i].c.n); w64(h + 14, B[i].h); put(&o, h, 22);
    }
    for (size_t i = 0; i < nb; i++) { put(&o, B[i].c.b, B[i].c.n); free(B[i].c.b); free(B[i].t.b); }
    free(B); *outb = o;
    if (xf_payload) free(xf_payload);
    if (opt.depth == 0) fprintf(stderr, "qze: %zu -> %zu B, %zu blocks, tier %d, delim %d\n", n, o.n, nb, tier, delim);
  }
}

static int qze_decompress(const uint8_t *in, size_t n, int T, int depth, uint8_t **outp, size_t *onp) {
  {
    if (n < 20 || memcmp(in, "QZE1", 4) || in[4] != 1) return 3;
    size_t on = (size_t)r64(in + 8), nb = r32(in + 16);
    int file_xf = in[7];
    size_t hdr0 = 20, xflen = on; uint64_t orig_xxh = 0;
    if (file_xf) {
      if (n < 36) return 1;
      xflen = (size_t)r64(in + 20); orig_xxh = r64(in + 28); hdr0 = 36;
    }
    if (nb > n / 22 || hdr0 + nb * 22 > n) return 1;
    size_t *pay = (size_t *)malloc((nb + 1) * sizeof(size_t)), *oo = (size_t *)malloc((nb + 1) * sizeof(size_t)), p = hdr0 + nb * 22, q = 0;
    for (size_t i = 0; i < nb; i++) { const uint8_t *h = in + hdr0 + i * 22; pay[i] = p; oo[i] = q; p += r32(h + 10); q += r32(h + 2); }
    if (p != n || q != (file_xf ? xflen : on)) { free(pay); free(oo); return 1; }
    uint8_t *stage = (uint8_t *)malloc((file_xf ? xflen : on) ? (file_xf ? xflen : on) : 1);
    dctx_t D = {in, n, stage, pay, oo, 0, hdr0, 0, depth, nb >= (size_t)T ? 1 : (int)(T / (nb ? nb : 1))};
    run_par(dec_block, &D, nb, T);
    D.pass = 1; run_par(dec_block, &D, nb, 1);
    free(pay); free(oo);
    if (D.err) { free(stage); return D.err == 2 ? 2 : 1; }
    uint8_t *out = stage;
    if (file_xf) {
      out = (uint8_t *)malloc(on ? on : 1);
      int bad = 1;
      if (file_xf == XF_LOG) bad = xf_log_decode(stage, xflen, out, on);
      else if (file_xf == XF_JSON) bad = xf_json_decode(stage, xflen, out, on);
      else if (file_xf == XF_BCJ) bad = xf_bcj_decode(stage, xflen, out, on);
      else if (file_xf == XF_TAR) bad = xf_tar_sort_decode(stage, xflen, out, on);
      else if (file_xf == XF_PDF) bad = xf_pdf_split_decode(stage, xflen, out, on);
      free(stage);
      if (bad) { free(out); return 1; }
      if (XXH64(out, on, 0) != orig_xxh) { free(out); return 2; }
    }
    *outp = out; *onp = on; return 0;
  }
}

int main(int argc, char **argv) {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
#endif
  if (argc < 4) { fprintf(stderr, "usage: qze c [-1|-2|-3] [-T n] [-b MB] [-L lvl] [-M lvl] [-P lzp] [-x] in out | qze d [-T n] in out\n"); return 2; }
  int tier = 1, T = ncpu(), bmb = 0, zl = -1, nocsv = 0, force_lzma = 0, lzp_min = 0; int a = 2;
  for (; a < argc - 2; a++) {
    if (!strcmp(argv[a], "-1")) tier = 1; else if (!strcmp(argv[a], "-2")) tier = 2; else if (!strcmp(argv[a], "-3")) tier = 3;
    else if (!strcmp(argv[a], "-T")) T = atoi(argv[++a]); else if (!strcmp(argv[a], "-b")) bmb = atoi(argv[++a]);
    else if (!strcmp(argv[a], "-L")) zl = atoi(argv[++a]); else if (!strcmp(argv[a], "-x")) nocsv = 1;
    else if (!strcmp(argv[a], "-M")) force_lzma = atoi(argv[++a]);
    else if (!strcmp(argv[a], "-P")) lzp_min = atoi(argv[++a]);
    else die("bad option");
  }
  if (T < 1) T = 1; if (T > 256) T = 256;
  if (bsc_init(LIBBSC_FEATURE_FASTMODE) != LIBBSC_NO_ERROR) die("libbsc init");
  size_t n; uint8_t *in = readall(argv[argc - 2], &n);
  copt_t opt = {tier, T, bmb, zl, nocsv, force_lzma, lzp_min, 0};
  if (argv[1][0] == 'c') {
    buf o = {0}; qze_compress(in, n, opt, &o); writeall(argv[argc - 1], o.b, o.n);
  } else if (argv[1][0] == 'd') {
    uint8_t *out = NULL; size_t on = 0; int r = qze_decompress(in, n, T, 0, &out, &on);
    if (r == 3) die("not a qze stream");
    if (r == 2) die("checksum mismatch");
    if (r) die("corrupt stream");
    writeall(argv[argc - 1], out, on);
  } else die("command must be c or d");
  return 0;
}
