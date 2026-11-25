/* File-format transforms for qze (log, NDJSON keys, executable branch filters). */
#ifndef QZE_XFORM_H
#define QZE_XFORM_H
#include <stddef.h>
#include <stdint.h>

enum { XF_LOG = 3, XF_JSON = 4, XF_BCJ = 5, XF_TAR = 6, XF_PDF = 7 };

int looks_health(const uint8_t *s, size_t n);
int looks_syslog(const uint8_t *s, size_t n);
int looks_ndjson(const uint8_t *s, size_t n);

/* Encode writes a self-contained blob (magic + payload). 1 = ok, 0 = fail (leave *out untouched). */
int xf_health_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
int xf_health_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_syslog_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
int xf_syslog_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_json_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
int xf_json_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_bcj_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
int xf_bcj_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_log_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on); /* health or syslog */
int xf_log_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_tar_sort_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
size_t xf_tar_ext_bytes(const uint8_t *s, size_t n, const char *ext);
size_t xf_tar_risc_bytes(const uint8_t *s, size_t n);
size_t xf_pdf_split_encode(const uint8_t *s, size_t n, uint8_t **out, size_t *on);
int xf_pdf_split_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);
int xf_tar_sort_decode(const uint8_t *t, size_t tn, uint8_t *dst, size_t want);

#endif
