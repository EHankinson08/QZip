#include "xform.h"
#include "Bra.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct { uint8_t *b; size_t n, cap; } xbuf;
static void xres(xbuf *x, size_t k) {
  if (x->n + k > x->cap) { x->cap = (x->n + k) * 2 + 4096; x->b = (uint8_t *)realloc(x->b, x->cap); }
}
static void xput(xbuf *x, const void *s, size_t k) { xres(x, k); memcpy(x->b + x->n, s, k); x->n += k; }
static void xpc(xbuf *x, int c) { xres(x, 1); x->b[x->n++] = (uint8_t)c; }
static void xpv(xbuf *x, uint64_t v) {
  xres(x, 10);
  while (v >= 128) { x->b[x->n++] = (uint8_t)((v & 127) | 128); v >>= 7; }
  x->b[x->n++] = (uint8_t)v;
}
static int xgv(const uint8_t *b, size_t n, size_t *p, uint64_t *v) {
  uint64_t r = 0; int s = 0;
  for (;;) {
    if (*p >= n || s > 63) return -1;
    uint8_t c = b[(*p)++];
    r |= (uint64_t)(c & 127) << s;
    if (c < 128) break;
    s += 7;
  }
  *v = r; return 0;
}
static uint64_t zig64(int64_t v) { return ((uint64_t)v << 1) ^ (uint64_t)(v >> 63); }
static int64_t unzig64(uint64_t v) { return (int64_t)((v >> 1) ^ -(int64_t)(v & 1)); }
static void xpvz(xbuf *x, int64_t d) { xpv(x, zig64(d)); }

static int is_digit(int c) { return c >= '0' && c <= '9'; }
static int parse_u(const uint8_t *s, int n, int *ok) {
  int v = 0; *ok = 0; if (n <= 0) return 0;
  for (int i = 0; i < n; i++) { if (!is_digit(s[i])) return 0; v = v * 10 + (s[i] - '0'); }
  *ok = 1; return v;
}

/* -------- line iterator: yields (ptr,len) without the newline; reports crlf if the line ended with \r\n */
typedef struct { const uint8_t *s; size_t n, p; int crlf, saw_crlf, saw_lf; } lines_t;
static void lines_init(lines_t *L, const uint8_t *s, size_t n) {
  L->s = s; L->n = n; L->p = 0; L->crlf = 0; L->saw_crlf = 0; L->saw_lf = 0;
}
static int lines_next(lines_t *L, const uint8_t **p, size_t *ln) {
  if (L->p >= L->n) return 0;
  size_t a = L->p;
  while (L->p < L->n && L->s[L->p] != '\n') L->p++;
  size_t e = L->p;
  if (L->p < L->n && L->s[L->p] == '\n') {
    L->saw_lf = 1;
    if (e > a && L->s[e - 1] == '\r') { e--; L->saw_crlf = 1; }
    L->p++;
  }
  *p = L->s + a; *ln = e - a; return 1;
}
static int lines_crlf(const lines_t *L) { return L->saw_crlf && !0; }

/* -------- detectors */
int looks_health(const uint8_t *s, size_t n) {
  lines_t L; lines_init(&L, s, n < 65536 ? n : 65536);
  int tot = 0, ok = 0; const uint8_t *p; size_t ln;
  while (tot < 200 && lines_next(&L, &p, &ln)) {
    tot++;
    if (ln >= 22 && p[8] == '-' && p[11] == ':' && p[14] == ':' && p[17] == ':' &&
        p[21] == '|') {
      int i, good = 1;
      for (i = 0; i < 8 && good; i++) if (!is_digit(p[i])) good = 0;
      if (good) ok++;
    }
  }
  return tot >= 8 && ok * 5 >= tot * 4; /* >=80% */
}
static const char *MOMS[13] = {0,"Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"};
static int month_of(const uint8_t *p) {
  for (int i = 1; i <= 12; i++) if (p[0]==MOMS[i][0] && p[1]==MOMS[i][1] && p[2]==MOMS[i][2]) return i;
  return 0;
}
int looks_syslog(const uint8_t *s, size_t n) {
  lines_t L; lines_init(&L, s, n < 65536 ? n : 65536);
  int tot = 0, ok = 0; const uint8_t *p; size_t ln;
  while (tot < 200 && lines_next(&L, &p, &ln)) {
    tot++;
    /* Mon[ ]D[D] HH:MM:SS host proc[pid]: */
    if (ln < 20 || !month_of(p) || p[3] != ' ') continue;
    size_t i = 4; if (i < ln && p[i] == ' ') i++;
    if (i >= ln || !is_digit(p[i])) continue;
    ok++;
  }
  return tot >= 8 && ok * 5 >= tot * 4;
}
int looks_ndjson(const uint8_t *s, size_t n) {
  size_t k = n < 65536 ? n : 65536;
  size_t i = 0; while (i < k && (s[i]==' '||s[i]=='\t'||s[i]=='\n'||s[i]=='\r')) i++;
  if (i >= k || s[i] != '{') return 0;
  int lines = 0, brace = 0, keys = 0;
  for (size_t p = i; p + 6 < k; p++) {
    if (s[p] == '\n') {
      lines++;
      size_t q = p + 1; while (q < k && (s[q]==' '||s[q]=='\t')) q++;
      if (q < k && s[q] == '{') brace++;
    }
    if (s[p]=='"' && p+5 < k && (!memcmp(s+p, "\"id\":", 5) || !memcmp(s+p, "\"type\":", 7) || !memcmp(s+p, "\"url\":", 6)))
      keys++;
  }
  return lines >= 8 && brace * 2 >= lines && keys >= 8;
}

/* -------- health: QZLOG1H u8 flags(bit0=crlf) nlines nuniq [len name]*  date* todms* comp* id*  msgs\n */
static int health_parse(const uint8_t *p, size_t ln,
                        int *date, int *tod, const uint8_t **comp, size_t *cl, int *id,
                        const uint8_t **msg, size_t *ml) {
  if (ln < 22 || p[8] != '-' || p[11] != ':' || p[14] != ':' || p[17] != ':' || p[21] != '|') return 0;
  int ok, ymd, hh, mm, ss, ms, ident;
  ymd = parse_u(p, 8, &ok); if (!ok) return 0;
  hh = parse_u(p+9, 2, &ok); if (!ok) return 0;
  mm = parse_u(p+12, 2, &ok); if (!ok) return 0;
  ss = parse_u(p+15, 2, &ok); if (!ok) return 0;
  ms = parse_u(p+18, 3, &ok); if (!ok) return 0;
  /* |comp|id|msg */
  size_t a = 22, b = a;
  while (b < ln && p[b] != '|') b++;
  if (b >= ln) return 0;
  size_t c = b + 1, d = c;
  while (d < ln && p[d] != '|') d++;
  if (d >= ln) return 0;
  ident = parse_u(p + c, (int)(d - c), &ok); if (!ok) return 0;
  *date = ymd; *tod = ((hh * 60 + mm) * 60 + ss) * 1000 + ms;
  *comp = p + a; *cl = b - a; *id = ident;
  *msg = p + d + 1; *ml = ln - (d + 1);
  return 1;
}
int xf_health_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on) {
  /* first pass: count lines */
  lines_t L; lines_init(&L, s, n);
  const uint8_t *p; size_t ln; size_t nlines = 0;
  while (lines_next(&L, &p, &ln)) nlines++;
  if (!nlines) return 0;
  int crlf = L.saw_crlf;
  typedef struct { const uint8_t *s; uint16_t n; } name_t;
  name_t *uniq = (name_t *)malloc((nlines < 4096 ? nlines : 4096) * sizeof(name_t));
  uint32_t nucap = nlines < 4096 ? (uint32_t)nlines : 4096, nuniq = 0;
  int64_t *date = (int64_t *)malloc(nlines * sizeof(int64_t));
  int64_t *tod  = (int64_t *)malloc(nlines * sizeof(int64_t));
  int64_t *cid  = (int64_t *)malloc(nlines * sizeof(int64_t));
  int64_t *ids  = (int64_t *)malloc(nlines * sizeof(int64_t));
  const uint8_t **msg = (const uint8_t **)malloc(nlines * sizeof(void *));
  uint32_t *msgl = (uint32_t *)malloc(nlines * sizeof(uint32_t));
  if (!date || !tod || !cid || !ids || !msg || !msgl || !uniq) { free(date);free(tod);free(cid);free(ids);free(msg);free(msgl);free(uniq); return 0; }
  lines_init(&L, s, n);
  size_t i = 0;
  while (lines_next(&L, &p, &ln)) {
    int da, tm, ident; const uint8_t *cp, *mp; size_t cl, ml;
    if (health_parse(p, ln, &da, &tm, &cp, &cl, &ident, &mp, &ml)) {
      uint32_t u;
      for (u = 0; u < nuniq; u++) if (uniq[u].n == cl && !memcmp(uniq[u].s, cp, cl)) break;
      if (u == nuniq) {
        if (nuniq == nucap) { nucap *= 2; uniq = (name_t *)realloc(uniq, nucap * sizeof(name_t)); }
        uniq[nuniq].s = cp; uniq[nuniq].n = (uint16_t)cl; nuniq++;
      }
      date[i]=da; tod[i]=tm; cid[i]=u; ids[i]=ident; msg[i]=mp; msgl[i]=(uint32_t)ml;
    } else {
      date[i]=0; tod[i]=-1; cid[i]=0; ids[i]=0; msg[i]=p; msgl[i]=(uint32_t)ln;
    }
    i++;
  }
  xbuf o = {0};
  xput(&o, "QZLOG1H", 7); xpc(&o, crlf ? 1 : 0);
  xpv(&o, nlines); xpv(&o, nuniq);
  for (uint32_t u = 0; u < nuniq; u++) { xpv(&o, uniq[u].n); xput(&o, uniq[u].s, uniq[u].n); }
  { int64_t pd=0,pt=0,pc=0,pi=0;
    for (size_t k=0;k<nlines;k++) { xpvz(&o, date[k]-pd); pd=date[k]; }
    for (size_t k=0;k<nlines;k++) { xpvz(&o, tod[k]-pt); pt=tod[k]; }
    for (size_t k=0;k<nlines;k++) { xpvz(&o, cid[k]-pc); pc=cid[k]; }
    for (size_t k=0;k<nlines;k++) { xpvz(&o, ids[k]-pi); pi=ids[k]; }
  }
  for (size_t k=0;k<nlines;k++) { xput(&o, msg[k], msgl[k]); if (k+1<nlines) xpc(&o, '\n'); }
  free(date);free(tod);free(cid);free(ids);free(msg);free(msgl);free(uniq);
  *out = o.b; *on = o.n; return 1;
}
static int get_deltas(const uint8_t *t, size_t tn, size_t *p, size_t n, int64_t *out) {
  int64_t prev = 0;
  for (size_t i = 0; i < n; i++) {
    uint64_t v; if (xgv(t, tn, p, &v)) return 0;
    prev += unzig64(v); out[i] = prev;
  }
  return 1;
}
static void emit_u(xbuf *o, int v, int w) {
  char tmp[16]; int i = w;
  tmp[--i] = (char)('0' + (v % 10)); v /= 10;
  while (i > 0) { tmp[--i] = (char)('0' + (v % 10)); v /= 10; }
  xput(o, tmp, (size_t)w);
}
int xf_health_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want) {
  if (tn < 8 || memcmp(t, "QZLOG1H", 7)) return 1;
  size_t p = 7; int crlf = t[p++];
  uint64_t nlines, nuniq;
  if (xgv(t, tn, &p, &nlines) || xgv(t, tn, &p, &nuniq) || nlines > 50u << 20 || nuniq > nlines + 1) return 1;
  uint8_t **names = (uint8_t **)calloc((size_t)nuniq + 1, sizeof(void *));
  uint32_t *nlens = (uint32_t *)calloc((size_t)nuniq + 1, sizeof(uint32_t));
  int64_t *date = (int64_t *)malloc((size_t)nlines * sizeof(int64_t));
  int64_t *tod  = (int64_t *)malloc((size_t)nlines * sizeof(int64_t));
  int64_t *cid  = (int64_t *)malloc((size_t)nlines * sizeof(int64_t));
  int64_t *ids  = (int64_t *)malloc((size_t)nlines * sizeof(int64_t));
  if (!date||!tod||!cid||!ids||!names||!nlens) { free(date);free(tod);free(cid);free(ids);free(names);free(nlens); return 1; }
  int bad = 0;
  for (uint64_t u = 0; u < nuniq && !bad; u++) {
    uint64_t L; if (xgv(t, tn, &p, &L) || p + L > tn) { bad = 1; break; }
    names[u] = (uint8_t *)(t + p); nlens[u] = (uint32_t)L; p += (size_t)L;
  }
  if (!bad) bad = !get_deltas(t, tn, &p, (size_t)nlines, date);
  if (!bad) bad = !get_deltas(t, tn, &p, (size_t)nlines, tod);
  if (!bad) bad = !get_deltas(t, tn, &p, (size_t)nlines, cid);
  if (!bad) bad = !get_deltas(t, tn, &p, (size_t)nlines, ids);
  xbuf o = {0};
  /* remaining: messages joined by \n */
  const uint8_t *ms = t + p; size_t mn = tn - p;
  size_t mp = 0;
  for (uint64_t i = 0; i < nlines && !bad; i++) {
    const uint8_t *m; size_t ml;
    if (mp < mn) {
      size_t a = mp; while (mp < mn && ms[mp] != '\n') mp++;
      m = ms + a; ml = mp - a; if (mp < mn && ms[mp] == '\n') mp++;
    } else { m = (const uint8_t *)""; ml = 0; }
    if (tod[i] < 0) {
      xput(&o, m, ml);
    } else {
      int ymd = (int)date[i], todms = (int)tod[i];
      if (todms < 0) { bad = 1; break; }
      int ms_ = todms % 1000; todms /= 1000;
      int ss = todms % 60; todms /= 60;
      int mm = todms % 60; int hh = todms / 60;
      emit_u(&o, ymd, 8); xpc(&o, '-');
      emit_u(&o, hh, 2); xpc(&o, ':'); emit_u(&o, mm, 2); xpc(&o, ':');
      emit_u(&o, ss, 2); xpc(&o, ':'); emit_u(&o, ms_, 3);
      xpc(&o, '|');
      if (cid[i] < 0 || (uint64_t)cid[i] >= nuniq) { bad = 1; break; }
      xput(&o, names[cid[i]], nlens[cid[i]]);
      xpc(&o, '|');
      /* id as decimal, no padding */
      { char tmp[32]; int v = (int)ids[i], k = 0, neg = 0;
        if (v < 0) { neg = 1; v = -v; }
        if (v == 0) tmp[k++] = '0';
        char r[32]; int rk=0; while (v) { r[rk++] = (char)('0'+v%10); v/=10; }
        if (neg) tmp[k++]='-';
        while (rk) tmp[k++]=r[--rk];
        xput(&o, tmp, (size_t)k);
      }
      xpc(&o, '|'); xput(&o, m, ml);
    }
    if (crlf) xput(&o, "\r\n", 2); else xpc(&o, '\n');
  }
  /* If the original did not end with a trailing newline, we may have added one.
     Health/syslog corpora always end with a newline; if want doesn't match, try dropping a final newline. */
  if (!bad && o.n != want && o.n == want + (crlf ? 2u : 1u)) o.n = want;
  if (!bad && o.n == want) memcpy(dst, o.b, want); else bad = 1;
  free(date);free(tod);free(cid);free(ids);free(names);free(nlens);free(o.b);
  return bad;
}

/* -------- syslog: QZLOG1S u8 flags nlines nhost [len host]* nproc [len proc]* months days times hosts procs pids  msgs\n */
static int syslog_parse(const uint8_t *p, size_t ln,
                        int *mo, int *day, int *tod, const uint8_t **host, size_t *hl,
                        const uint8_t **proc, size_t *pl, int *pid, const uint8_t **rest, size_t *rl) {
  if (ln < 16) return 0;
  int m = month_of(p); if (!m || p[3] != ' ') return 0;
  size_t i = 4; if (i < ln && p[i] == ' ') i++;
  if (i >= ln || !is_digit(p[i])) return 0;
  int d = 0; while (i < ln && is_digit(p[i])) { d = d * 10 + (p[i] - '0'); i++; }
  if (i >= ln || p[i] != ' ') return 0; i++;
  if (i + 8 > ln || p[i+2] != ':' || p[i+5] != ':') return 0;
  int ok, hh, mm, ss;
  hh = parse_u(p+i, 2, &ok); if (!ok) return 0;
  mm = parse_u(p+i+3, 2, &ok); if (!ok) return 0;
  ss = parse_u(p+i+6, 2, &ok); if (!ok) return 0;
  i += 8; if (i >= ln || p[i] != ' ') return 0; i++;
  size_t hs = i; while (i < ln && p[i] != ' ') i++;
  if (i >= ln || hs == i) return 0;
  *host = p + hs; *hl = i - hs; i++;
  size_t ps = i; while (i < ln && p[i] != '[' && p[i] != ':' && p[i] != ' ') i++;
  if (ps == i) return 0;
  *proc = p + ps; *pl = i - ps;
  int id = 0;
  if (i < ln && p[i] == '[') {
    i++; int v = 0, any = 0;
    while (i < ln && is_digit(p[i])) { v = v * 10 + (p[i] - '0'); i++; any = 1; }
    if (!any || i >= ln || p[i] != ']') return 0;
    id = v; i++;
  }
  if (i >= ln || p[i] != ':') return 0; i++;
  if (i < ln && p[i] == ' ') i++;
  *mo = m; *day = d; *tod = hh * 3600 + mm * 60 + ss; *pid = id;
  *rest = p + i; *rl = ln - i;
  return 1;
}
int xf_syslog_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on) {
  lines_t L; lines_init(&L, s, n);
  const uint8_t *p; size_t ln; size_t nlines = 0;
  while (lines_next(&L, &p, &ln)) nlines++;
  if (!nlines) return 0;
  int crlf = L.saw_crlf;
  typedef struct { const uint8_t *s; uint16_t n; } name_t;
  name_t *hosts = (name_t *)malloc(256 * sizeof(name_t)); uint32_t nh = 0, hcap = 256;
  name_t *procs = (name_t *)malloc(256 * sizeof(name_t)); uint32_t np = 0, pcap = 256;
  int64_t *mo=(int64_t*)malloc(nlines*sizeof(int64_t));
  int64_t *day=(int64_t*)malloc(nlines*sizeof(int64_t));
  int64_t *tod=(int64_t*)malloc(nlines*sizeof(int64_t));
  int64_t *hid=(int64_t*)malloc(nlines*sizeof(int64_t));
  int64_t *prd=(int64_t*)malloc(nlines*sizeof(int64_t));
  int64_t *pid=(int64_t*)malloc(nlines*sizeof(int64_t));
  const uint8_t **msg=(const uint8_t**)malloc(nlines*sizeof(void*));
  uint32_t *msgl=(uint32_t*)malloc(nlines*sizeof(uint32_t));
  if (!hosts||!procs||!mo||!day||!tod||!hid||!prd||!pid||!msg||!msgl) return 0;
  lines_init(&L, s, n);
  size_t i = 0;
  while (lines_next(&L, &p, &ln)) {
    int m,d,tm,id; const uint8_t *h,*pr,*r; size_t hl,pl,rl;
    if (syslog_parse(p, ln, &m, &d, &tm, &h, &hl, &pr, &pl, &id, &r, &rl)) {
      uint32_t u;
      for (u=0;u<nh;u++) if (hosts[u].n==hl && !memcmp(hosts[u].s,h,hl)) break;
      if (u==nh) { if (nh==hcap){hcap*=2;hosts=(name_t*)realloc(hosts,hcap*sizeof(name_t));} hosts[nh].s=h;hosts[nh].n=(uint16_t)hl; nh++; }
      hid[i]=u;
      for (u=0;u<np;u++) if (procs[u].n==pl && !memcmp(procs[u].s,pr,pl)) break;
      if (u==np) { if (np==pcap){pcap*=2;procs=(name_t*)realloc(procs,pcap*sizeof(name_t));} procs[np].s=pr;procs[np].n=(uint16_t)pl; np++; }
      prd[i]=u;
      mo[i]=m; day[i]=d; tod[i]=tm; pid[i]=id; msg[i]=r; msgl[i]=(uint32_t)rl;
    } else {
      mo[i]=0; day[i]=0; tod[i]=-1; hid[i]=0; prd[i]=0; pid[i]=0; msg[i]=p; msgl[i]=(uint32_t)ln;
    }
    i++;
  }
  xbuf o={0};
  xput(&o,"QZLOG1S",7); xpc(&o, crlf?1:0);
  xpv(&o,nlines); xpv(&o,nh);
  for (uint32_t u=0;u<nh;u++){ xpv(&o,hosts[u].n); xput(&o,hosts[u].s,hosts[u].n); }
  xpv(&o,np);
  for (uint32_t u=0;u<np;u++){ xpv(&o,procs[u].n); xput(&o,procs[u].s,procs[u].n); }
  { int64_t a=0,b=0,c=0,d=0,e=0,f=0;
    for(size_t k=0;k<nlines;k++){xpvz(&o,mo[k]-a);a=mo[k];}
    for(size_t k=0;k<nlines;k++){xpvz(&o,day[k]-b);b=day[k];}
    for(size_t k=0;k<nlines;k++){xpvz(&o,tod[k]-c);c=tod[k];}
    for(size_t k=0;k<nlines;k++){xpvz(&o,hid[k]-d);d=hid[k];}
    for(size_t k=0;k<nlines;k++){xpvz(&o,prd[k]-e);e=prd[k];}
    for(size_t k=0;k<nlines;k++){xpvz(&o,pid[k]-f);f=pid[k];}
  }
  for (size_t k=0;k<nlines;k++){ xput(&o,msg[k],msgl[k]); if(k+1<nlines) xpc(&o,'\n'); }
  free(hosts);free(procs);free(mo);free(day);free(tod);free(hid);free(prd);free(pid);free(msg);free(msgl);
  *out=o.b; *on=o.n; return 1;
}
int xf_syslog_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want) {
  if (tn < 8 || memcmp(t, "QZLOG1S", 7)) return 1;
  size_t p = 7; int crlf = t[p++];
  uint64_t nlines, nh, np;
  if (xgv(t,tn,&p,&nlines) || xgv(t,tn,&p,&nh) || nlines>50u<<20 || nh>nlines+1) return 1;
  const uint8_t **hname=(const uint8_t**)calloc((size_t)nh+1,sizeof(void*));
  uint32_t *hlen=(uint32_t*)calloc((size_t)nh+1,sizeof(uint32_t));
  for (uint64_t u=0;u<nh;u++){ uint64_t L; if (xgv(t,tn,&p,&L)||p+L>tn) return 1; hname[u]=t+p; hlen[u]=(uint32_t)L; p+=(size_t)L; }
  if (xgv(t,tn,&p,&np) || np>nlines+1) return 1;
  const uint8_t **pname=(const uint8_t**)calloc((size_t)np+1,sizeof(void*));
  uint32_t *plen=(uint32_t*)calloc((size_t)np+1,sizeof(uint32_t));
  for (uint64_t u=0;u<np;u++){ uint64_t L; if (xgv(t,tn,&p,&L)||p+L>tn) return 1; pname[u]=t+p; plen[u]=(uint32_t)L; p+=(size_t)L; }
  int64_t *mo=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int64_t *day=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int64_t *tod=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int64_t *hid=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int64_t *prd=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int64_t *pid=(int64_t*)malloc((size_t)nlines*sizeof(int64_t));
  int bad = 0;
  if (!get_deltas(t,tn,&p,(size_t)nlines,mo)) bad=1;
  if (!bad && !get_deltas(t,tn,&p,(size_t)nlines,day)) bad=1;
  if (!bad && !get_deltas(t,tn,&p,(size_t)nlines,tod)) bad=1;
  if (!bad && !get_deltas(t,tn,&p,(size_t)nlines,hid)) bad=1;
  if (!bad && !get_deltas(t,tn,&p,(size_t)nlines,prd)) bad=1;
  if (!bad && !get_deltas(t,tn,&p,(size_t)nlines,pid)) bad=1;
  xbuf o={0};
  const uint8_t *ms=t+p; size_t mn=tn-p, mp=0;
  for (uint64_t i=0;i<nlines && !bad;i++) {
    const uint8_t *m; size_t ml;
    if (mp<mn){ size_t a=mp; while(mp<mn && ms[mp]!='\n') mp++; m=ms+a; ml=mp-a; if(mp<mn && ms[mp]=='\n') mp++; }
    else { m=(const uint8_t*)""; ml=0; }
    if (tod[i] < 0) {
      xput(&o,m,ml);
    } else {
      int mon=(int)mo[i]; if (mon<1||mon>12){bad=1;break;}
      xput(&o, MOMS[mon], 3); xpc(&o,' ');
      /* day right-aligned width 2 (space padded) */
      int d=(int)day[i];
      if (d<10) xpc(&o,' ');
      else xpc(&o, '0'+ (d/10)%10);
      xpc(&o, '0'+ (d%10)); xpc(&o,' ');
      int t=(int)tod[i]; if (t<0){bad=1;break;}
      int hh=t/3600, mm=(t%3600)/60, ss=t%60;
      emit_u(&o,hh,2); xpc(&o,':'); emit_u(&o,mm,2); xpc(&o,':'); emit_u(&o,ss,2);
      if (hid[i]<0||(uint64_t)hid[i]>=nh || prd[i]<0||(uint64_t)prd[i]>=np){bad=1;break;}
      xpc(&o,' '); xput(&o,hname[hid[i]],hlen[hid[i]]);
      xpc(&o,' '); xput(&o,pname[prd[i]],plen[prd[i]]);
      xpc(&o,'[');
      { char tmp[16]; int v=(int)pid[i], k=0; if(v==0) tmp[k++]='0'; else { char r[16]; int rk=0; if(v<0){xpc(&o,'-');v=-v;} while(v){r[rk++]=(char)('0'+v%10);v/=10;} while(rk) tmp[k++]=r[--rk]; } xput(&o,tmp,(size_t)k); }
      xput(&o, "]: ", 3); xput(&o,m,ml);
    }
    if (crlf) xput(&o,"\r\n",2); else xpc(&o,'\n');
  }
  if (!bad && o.n != want && o.n == want + (crlf ? 2u : 1u)) o.n = want;
  if (!bad && o.n == want) memcpy(dst, o.b, want); else bad = 1;
  free(hname);free(hlen);free(pname);free(plen);free(mo);free(day);free(tod);free(hid);free(prd);free(pid);free(o.b);
  return bad;
}

int xf_log_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on) {
  if (looks_health(s, n)) return xf_health_encode(s, n, out, on);
  if (looks_syslog(s, n)) return xf_syslog_encode(s, n, out, on);
  return 0;
}
int xf_log_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want) {
  if (tn >= 7 && !memcmp(t, "QZLOG1H", 7)) return xf_health_decode(t, tn, dst, want);
  if (tn >= 7 && !memcmp(t, "QZLOG1S", 7)) return xf_syslog_decode(t, tn, dst, want);
  return 1;
}

/* -------- NDJSON key dictionary: QZJSN1 nkeys [len key]*  body
   Body: '~' escaped as '~~'; each selected key's `"key":` becomes ~ + 2 chars from [A-Z0-9]. */
static const uint8_t TOKABC[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
#define TOKRADIX 36
static void tok_of(int i, uint8_t t[3]) {
  t[0] = '~'; t[1] = TOKABC[i / TOKRADIX]; t[2] = TOKABC[i % TOKRADIX];
}

typedef struct { uint8_t *k; uint16_t n; uint32_t c; } jk_t;

int xf_json_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on) {
  /* collect "key": counts with a simple scanner */
  jk_t *ks = (jk_t *)malloc(4096 * sizeof(jk_t)); uint32_t nk = 0, kcap = 4096;
  if (!ks) return 0;
  for (size_t i = 0; i + 3 < n; i++) {
    if (s[i] != '"') continue;
    size_t a = i + 1, b = a;
    while (b < n && s[b] != '"' && s[b] != '\\' && (b - a) < 64) b++;
    if (b >= n || s[b] != '"' || b == a) continue;
    size_t q = b + 1; while (q < n && (s[q]==' '||s[q]=='\t')) q++;
    if (q >= n || s[q] != ':') continue;
    size_t kn = b - a; const uint8_t *kp = s + a;
    uint32_t u;
    for (u = 0; u < nk; u++) if (ks[u].n == kn && !memcmp(ks[u].k, kp, kn)) { ks[u].c++; break; }
    if (u == nk) {
      if (nk == kcap) { kcap *= 2; ks = (jk_t *)realloc(ks, kcap * sizeof(jk_t)); }
      ks[nk].k = (uint8_t *)malloc(kn); memcpy(ks[nk].k, kp, kn); ks[nk].n = (uint16_t)kn; ks[nk].c = 1; nk++;
    }
    i = q; /* continue after colon */
  }
  /* pick keys: count>=3 (or >=2 if few lines) and length>=3, max 180, longer first then lex */
  uint32_t minc = n > (1u << 20) ? 8 : 3;
  int idx[256]; int nuse = 0;
  for (uint32_t u = 0; u < nk && nuse < 180; u++) {
    if (ks[u].c >= minc && ks[u].n >= 3) idx[nuse++] = (int)u;
  }
  /* if nothing useful, fail so caller leaves the block untransformed */
  if (nuse < 4) {
    for (uint32_t u=0;u<nk;u++) free(ks[u].k); free(ks); return 0;
  }
  /* sort by length desc, then memcmp */
  for (int a=0;a<nuse;a++) for (int b=a+1;b<nuse;b++) {
    jk_t *A=&ks[idx[a]], *B=&ks[idx[b]];
    int sw = 0;
    if (A->n < B->n) sw = 1;
    else if (A->n == B->n && memcmp(A->k, B->k, A->n) > 0) sw = 1;
    if (sw) { int t=idx[a]; idx[a]=idx[b]; idx[b]=t; }
  }
  /* escape then replace */
  xbuf body = {0};
  for (size_t i = 0; i < n; i++) {
    if (s[i] == '~') { xpc(&body, '~'); xpc(&body, '~'); continue; }
    /* try `"key":` at i */
    int hit = -1;
    if (s[i] == '"') {
      for (int u = 0; u < nuse; u++) {
        jk_t *K = &ks[idx[u]];
        size_t need = 1 + K->n + 2; /* " key ": */
        if (i + need <= n && s[i+1+K->n]=='"' && s[i+2+K->n]==':' && !memcmp(s+i+1, K->k, K->n)) {
          /* no optional space in our scanner (we required immediate ":") */
          hit = u; break;
        }
      }
    }
    if (hit >= 0) {
      uint8_t tk[3]; tok_of(hit, tk); xput(&body, tk, 3);
      i += 1 + ks[idx[hit]].n + 2 - 1; /* advance to last byte of `:`; for-loop ++ */
      continue;
    }
    xpc(&body, s[i]);
  }
  xbuf o = {0};
  xput(&o, "QZJSN1", 6); xpv(&o, (uint64_t)nuse);
  for (int u = 0; u < nuse; u++) { xpv(&o, ks[idx[u]].n); xput(&o, ks[idx[u]].k, ks[idx[u]].n); }
  xput(&o, body.b, body.n);
  free(body.b);
  for (uint32_t u=0;u<nk;u++) free(ks[u].k); free(ks);
  *out = o.b; *on = o.n; return 1;
}
int xf_json_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want) {
  if (tn < 6 || memcmp(t, "QZJSN1", 6)) return 1;
  size_t p = 6; uint64_t nuse;
  if (xgv(t, tn, &p, &nuse) || nuse > 180) return 1;
  const uint8_t *keys[180]; uint16_t kn[180];
  for (uint64_t u = 0; u < nuse; u++) {
    uint64_t L; if (xgv(t, tn, &p, &L) || p + L > tn || L > 64) return 1;
    keys[u] = t + p; kn[u] = (uint16_t)L; p += (size_t)L;
  }
  xbuf o = {0};
  const uint8_t *b = t + p; size_t bn = tn - p;
  for (size_t i = 0; i < bn; i++) {
    if (b[i] == '~') {
      if (i + 1 < bn && b[i+1] == '~') { xpc(&o, '~'); i++; continue; }
      if (i + 2 < bn) {
        int hit = -1;
        for (uint64_t u = 0; u < nuse; u++) {
          uint8_t tk[3]; tok_of((int)u, tk);
          if (b[i]==tk[0] && b[i+1]==tk[1] && b[i+2]==tk[2]) { hit = (int)u; break; }
        }
        if (hit >= 0) {
          xpc(&o, '"'); xput(&o, keys[hit], kn[hit]); xput(&o, "\":", 2);
          i += 2; continue;
        }
      }
    }
    xpc(&o, b[i]);
  }
  int bad = (o.n != want);
  if (!bad) memcpy(dst, o.b, want);
  free(o.b);
  return bad;
}

/* -------- BCJ / branch filter on ELF, PE, Mach-O, i386 COFF, including ustar members.
   QZFLT1 u16 nrange  [u32 off u32 len u8 kind]*  payload (filters already applied). kind 1=x86 2=arm64 3=arm */
static void w32le(uint8_t *p, uint32_t v) { p[0]=(uint8_t)v; p[1]=(uint8_t)(v>>8); p[2]=(uint8_t)(v>>16); p[3]=(uint8_t)(v>>24); }
static uint32_t r32le(const uint8_t *p) { return p[0]|p[1]<<8|(uint32_t)p[2]<<16|(uint32_t)p[3]<<24; }
static unsigned long long tar_octal(const uint8_t *p, int n) {
  if (p[0] & 0x80) { unsigned long long v=0; for (int i=1;i<n;i++) v=(v<<8)|p[i]; return v; }
  unsigned long long v=0; int i=0;
  while (i<n && (p[i]==' '||p[i]=='0')) i++;
  for (; i<n && p[i] && p[i]!=' '; i++) { if (p[i]<'0'||p[i]>'7') break; v=(v<<3)|(unsigned)(p[i]-'0'); }
  return v;
}
static int kind_of(const uint8_t *p, size_t n) {
  if (n >= 20 && !memcmp(p, "\x7f" "ELF", 4)) {
    int le = p[5] != 2; unsigned mach = le ? (p[18] | p[19]<<8) : (p[18]<<8 | p[19]);
    if (mach==3||mach==6||mach==62) return 1;
    if (mach==183) return 2; if (mach==40) return 3; return 0;
  }
  if (n>=64 && p[0]=='M' && p[1]=='Z') {
    uint32_t off = r32le(p+0x3c);
    if (off+6<=n && !memcmp(p+off, "PE\0\0", 4)) {
      unsigned mach = p[off+4]|p[off+5]<<8;
      if (mach==0x14c||mach==0x8664) return 1;
      if (mach==0xaa64) return 2; if (mach==0x1c0||mach==0x1c4) return 3;
    }
  }
  if (n>=20) {
    unsigned m = p[0]|p[1]<<8;
    if (m==0x14c||m==0x183||m==0x152||m==0x15c||m==0x17c||m==0x1c0) {
      unsigned nscns = p[2]|p[3]<<8;
      if (nscns>0 && nscns<100) return 1;
    }
    if (m==0x8664) return 1;
    if (m==0xaa64) return 2;
  }
  return 0;
}
static void apply_enc(uint8_t *p, size_t n, int kind) {
  if (!n||!kind) return;
  if (kind==1) { UInt32 st = Z7_BRANCH_CONV_ST_X86_STATE_INIT_VAL; z7_BranchConvSt_X86_Enc(p, n, 0, &st); }
  else if (kind==2) z7_BranchConv_ARM64_Enc(p, n, 0);
  else if (kind==3) z7_BranchConv_ARM_Enc(p, n, 0);
}
static void apply_dec(uint8_t *p, size_t n, int kind) {
  if (!n||!kind) return;
  if (kind==1) { UInt32 st = Z7_BRANCH_CONV_ST_X86_STATE_INIT_VAL; z7_BranchConvSt_X86_Dec(p, n, 0, &st); }
  else if (kind==2) z7_BranchConv_ARM64_Dec(p, n, 0);
  else if (kind==3) z7_BranchConv_ARM_Dec(p, n, 0);
}
typedef struct { uint32_t off, len; uint8_t kind; } fr_t;
static void collect_ranges(const uint8_t *s, size_t n, fr_t **R, int *nr) {
  int cap = 16, nrr = 0; fr_t *r = (fr_t *)malloc((size_t)cap * sizeof(fr_t));
  if (n >= 262 && !memcmp(s+257, "ustar", 5)) {
    size_t p = 0;
    while (p + 512 <= n) {
      int z=1; for (int i=0;i<512;i++) if (s[p+i]) { z=0; break; }
      if (z) { p+=512; continue; }
      unsigned long long sz = tar_octal(s+p+124, 12);
      unsigned type = s[p+156];
      size_t data = p+512;
      size_t rec = 512 + (size_t)((sz+511)/512)*512;
      if (data>n || data+(size_t)sz>n) break;
      if ((type==0||type=='0'||type=='7') && sz>32) {
        int k = kind_of(s+data, (size_t)sz);
        if (k) {
          if (nrr==cap) { cap*=2; r=(fr_t*)realloc(r,(size_t)cap*sizeof(fr_t)); }
          r[nrr].off=(uint32_t)data; r[nrr].len=(uint32_t)sz; r[nrr].kind=(uint8_t)k; nrr++;
        }
      }
      p += rec;
    }
  } else {
    int k = kind_of(s, n);
    if (k) { r[0].off=0; r[0].len=(uint32_t)n; r[0].kind=(uint8_t)k; nrr=1; }
  }
  *R = r; *nr = nrr;
}
int xf_bcj_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on) {
  fr_t *R; int nr;
  collect_ranges(s, n, &R, &nr);
  if (nr <= 0) { free(R); return 0; }
  xbuf o = {0};
  xput(&o, "QZFLT1", 6);
  uint8_t nb[2] = { (uint8_t)(nr), (uint8_t)(nr>>8) }; xput(&o, nb, 2);
  for (int i=0;i<nr;i++) {
    uint8_t rec[9]; w32le(rec,R[i].off); w32le(rec+4,R[i].len); rec[8]=R[i].kind; xput(&o, rec, 9);
  }
  size_t payload = o.n;
  xput(&o, s, n);
  for (int i=0;i<nr;i++) apply_enc(o.b + payload + R[i].off, R[i].len, R[i].kind);
  free(R);
  *out = o.b; *on = o.n; return 1;
}
int xf_bcj_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want) {
  if (tn < 8 || memcmp(t, "QZFLT1", 6)) return 1;
  int nr = t[6] | t[7]<<8;
  if (nr < 0 || nr > 100000) return 1;
  size_t hdr = 8 + (size_t)nr * 9;
  if (tn < hdr + want) return 1;
  if (tn - hdr != want) return 1;
  memcpy(dst, t + hdr, want);
  for (int i=0;i<nr;i++) {
    const uint8_t *rec = t + 8 + (size_t)i*9;
    uint32_t off=r32le(rec), len=r32le(rec+4); uint8_t kind=rec[8];
    if ((size_t)off + len > want) return 1;
    apply_dec(dst + off, len, kind);
  }
  return 0;
}
