/* qze: QZip "everyday" tiers (fast / balanced). Copyright 2026 Elliott Hankinson. GPL-3.0-or-later.
 *
 *   qze c [-1|-2] [-T threads] [-b blockMB] [-L zstd_level] [-x] <in|-> <out|->   compress (-1 fast, default; -2 balanced)
 *   qze d [-T threads] <in|-> <out|->                                           decompress
 *
 * The input is cut into independent blocks (CSV/TSV: at line ends), each block is optionally run through QZip's CSV
 * column transform (transpose to column-major, numeric columns -> zigzag-delta varints, fixed decimals kept exact)
 * and then coded by one backend: zstd (vendored 1.5.7, BSD) or libbsc (vendored 3.4.1, Apache-2.0; BWT + QLFC).
 * Blocks are compressed AND decompressed in parallel. Every block carries the XXH64 of its raw bytes; the decoder
 * checks it. Portable C11 + libbsc (C++); threads via pthreads or Win32. No x86 intrinsics.
 *
 * Format "QZE1": magic[4] u8 ver=1 u8 tier u8 delim(0=none) u8 flags | u64 n | u32 nblocks
 *   per block: u8 backend(0 stored,1 zstd,2 bsc) u8 transform(0 none,1 csv) u32 rawlen u32 tlen u32 clen u64 xxh64
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
enum { B_STORED = 0, B_ZSTD = 1, B_BSC = 2 };
typedef struct { const uint8_t *raw; size_t rawlen; uint8_t backend, transform; buf t; buf c; uint64_t h; int err; } blk_t;
typedef struct { blk_t *b; int tier, delim, zlevel, first_csv; } cctx_t;

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
  int r = bsc_compress(s, o->b, (int)n, lzpmin ? 16 : 0, lzpmin ? lzpmin : 0, sorter, coder, LIBBSC_FEATURE_FASTMODE);
  if (r < 0) return 0; o->n = (size_t)r; return (size_t)r;
}
static size_t bsc_into(buf *o, const uint8_t *s, size_t n, int coder) {
  o->n = 0; bres(o, n + LIBBSC_HEADER_SIZE + 64);
  int r = bsc_compress(s, o->b, (int)n, 16, 128, LIBBSC_BLOCKSORTER_BWT, coder, LIBBSC_FEATURE_FASTMODE);
  if (r < 0) return 0; o->n = (size_t)r; return (size_t)r;
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
        if (bsc_into2(&c, src, sn, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, 0) && c.n < bestn) { bestn = c.n; cand = k; }
      }
      free(c.b); free(sb.t.b);
    }
    const uint8_t *src = b->raw; size_t sn = b->rawlen;
    if (cand > 0 && try_csv(b, C->delim, i == 0, cand == 2, &b->t)) { src = b->t.b; sn = b->t.n; b->transform = 1; }
    int usebsc = b->transform || is_texty(src, sn);
    if (!usebsc) {   /* binary: BWT often beats LZ on tables / databases / executables; decide on a head sample */
      size_t sl = sn < SAMPLE ? sn : SAMPLE; buf c1 = {0}, c2 = {0}; int lg = 10; while (((size_t)1 << lg) < sl && lg < 27) lg++;
      size_t a1 = bsc_into2(&c1, src, sl, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, 0), a2 = zstd_into(&c2, src, sl, C->zlevel, lg);
      usebsc = a1 && (!a2 || a1 * 100 < a2 * B2BIN); free(c1.b); free(c2.b);
    }
    if (usebsc) { b->backend = B_BSC; if (!bsc_into2(&b->c, src, sn, LIBBSC_BLOCKSORTER_BWT, LIBBSC_CODER_QLFC_STATIC, 0)) b->err = 1; }
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
typedef struct { const uint8_t *in; size_t inn; uint8_t *out; size_t *pay, *ooff; int err; } dctx_t;
static void dec_block(job_t *j, size_t i) {
  dctx_t *D = (dctx_t *)j->ctx; const uint8_t *h = D->in + 4 + 4 + 8 + 4 + i * 22;
  int be = h[0], tr = h[1]; size_t raw = r32(h + 2), tl = r32(h + 6), cl = r32(h + 10); uint64_t xh = r64(h + 14);
  const uint8_t *c = D->in + D->pay[i]; uint8_t *dst = D->out + D->ooff[i];
  uint8_t *tb = tr ? (uint8_t *)malloc(tl + 1) : dst; size_t want = tr ? tl : raw; int bad = 0;
  if (be == B_STORED) { if (cl != want) bad = 1; else memcpy(tb, c, cl); }
  else if (be == B_ZSTD) { size_t r = ZSTD_decompress(tb, want, c, cl); if (ZSTD_isError(r) || r != want) bad = 1; }
  else if (be == B_BSC) { if (bsc_decompress(c, (int)cl, tb, (int)want, LIBBSC_FEATURE_FASTMODE) != LIBBSC_NO_ERROR) bad = 1; }
  else bad = 1;
  if (!bad && tr) { if (csv_decode(tb, tl, dst, raw)) bad = 1; }
  if (tr) free(tb);
  if (!bad && XXH64(dst, raw, 0) != xh) bad = 2;
  if (bad) D->err = bad;
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

int main(int argc, char **argv) {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY); _setmode(_fileno(stdout), _O_BINARY);
#endif
  if (argc < 4) { fprintf(stderr, "usage: qze c [-1|-2|-3] [-T n] [-b MB] [-L lvl] [-x] in out | qze d [-T n] in out\n"); return 2; }
  int tier = 1, T = ncpu(), bmb = 0, zl = -1, nocsv = 0; int a = 2;
  for (; a < argc - 2; a++) {
    if (!strcmp(argv[a], "-1")) tier = 1; else if (!strcmp(argv[a], "-2")) tier = 2; else if (!strcmp(argv[a], "-3")) tier = 3;
    else if (!strcmp(argv[a], "-T")) T = atoi(argv[++a]); else if (!strcmp(argv[a], "-b")) bmb = atoi(argv[++a]);
    else if (!strcmp(argv[a], "-L")) zl = atoi(argv[++a]); else if (!strcmp(argv[a], "-x")) nocsv = 1;
    else die("bad option");
  }
  if (T < 1) T = 1; if (T > 256) T = 256;
  if (bsc_init(LIBBSC_FEATURE_FASTMODE) != LIBBSC_NO_ERROR) die("libbsc init");
  size_t n; uint8_t *in = readall(argv[argc - 2], &n);
  if (argv[1][0] == 'c') {
    if (zl < 0) zl = tier == 1 ? 0 : tier == 2 ? B2LV : 19;   /* tier 1: 0 = per block (3 CSV-transformed, 5 other) */
    size_t bs = (size_t)(bmb > 0 ? bmb : (tier == 1 ? 4 : tier == 2 ? B2MB : 32)) << 20;
    int delim = nocsv ? 0 : detect_delim(in, n);
    /* blocks: CSV cuts at line ends; at least T blocks when the input is big enough */
    /* spread big inputs over the threads, but never below the tier's minimum block (ratio) unless -b is given */
    size_t minb = (size_t)(tier == 1 ? 1 : tier == 2 ? 2 : 16) << 20;
    if (!bmb && n / T < bs) { size_t e = (n + T - 1) / T; bs = e > minb ? e : minb; }
    size_t cap = n / (bs > 1 ? bs : 1) + 2, nb = 0, p = 0; blk_t *B = (blk_t *)calloc(cap + 1, sizeof(blk_t));
    while (p < n || nb == 0) {
      size_t e = p + bs < n ? p + bs : n;
      if (delim && e < n) { size_t q = e; while (q < n && in[q] != '\n') q++; e = q < n ? q + 1 : n; }
      if (nb >= cap) { cap *= 2; B = (blk_t *)realloc(B, (cap + 1) * sizeof(blk_t)); memset(B + nb, 0, (cap + 1 - nb) * sizeof(blk_t)); }
      B[nb].raw = in + p; B[nb].rawlen = e - p; nb++; p = e; if (n == 0) break;
    }
    cctx_t C = {B, tier, delim, zl, 1};
    run_par(comp_block, &C, nb, T);
    buf o = {0}; uint8_t hd[20]; memcpy(hd, "QZE1", 4); hd[4] = 1; hd[5] = (uint8_t)tier; hd[6] = (uint8_t)delim; hd[7] = 0; w64(hd + 8, n); w32(hd + 16, (uint32_t)nb);
    put(&o, hd, 20);
    for (size_t i = 0; i < nb; i++) {
      if (B[i].err) die("block compression failed");
      uint8_t h[22]; h[0] = B[i].backend; h[1] = B[i].transform; w32(h + 2, (uint32_t)B[i].rawlen); w32(h + 6, (uint32_t)(B[i].transform ? B[i].t.n : 0)); w32(h + 10, (uint32_t)B[i].c.n); w64(h + 14, B[i].h); put(&o, h, 22);
    }
    for (size_t i = 0; i < nb; i++) { put(&o, B[i].c.b, B[i].c.n); free(B[i].c.b); free(B[i].t.b); }
    writeall(argv[argc - 1], o.b, o.n);
    fprintf(stderr, "qze: %zu -> %zu B, %zu blocks, tier %d, delim %d\n", n, o.n, nb, tier, delim);
  } else if (argv[1][0] == 'd') {
    if (n < 20 || memcmp(in, "QZE1", 4) || in[4] != 1) die("not a qze stream");
    size_t on = (size_t)r64(in + 8), nb = r32(in + 16);
    if (20 + nb * 22 > n) die("truncated header");
    size_t *pay = (size_t *)malloc((nb + 1) * sizeof(size_t)), *oo = (size_t *)malloc((nb + 1) * sizeof(size_t)), p = 20 + nb * 22, q = 0;
    for (size_t i = 0; i < nb; i++) { const uint8_t *h = in + 20 + i * 22; pay[i] = p; oo[i] = q; p += r32(h + 10); q += r32(h + 2); }
    if (p != n || q != on) die("corrupt header");
    uint8_t *out = (uint8_t *)malloc(on ? on : 1); dctx_t D = {in, n, out, pay, oo, 0};
    run_par(dec_block, &D, nb, T);
    if (D.err) die(D.err == 2 ? "checksum mismatch" : "corrupt block");
    writeall(argv[argc - 1], out, on);
  } else die("command must be c or d");
  return 0;
}
