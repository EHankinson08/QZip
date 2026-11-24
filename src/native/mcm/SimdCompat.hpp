// QZip portability shim (GPL-3.0-or-later, QZip). x86/x86-64: the real SSE2 header. ARM64 (Apple Silicon, Linux
// aarch64, Windows on ARM): sse2neon (MIT, DLTcollab/sse2neon) maps the integer SSE2 intrinsics the mixer uses to NEON
// with identical integer results, so streams stay byte-identical across architectures.
#ifndef QZIP_SIMD_COMPAT_HPP_
#define QZIP_SIMD_COMPAT_HPP_
#if defined(__aarch64__) || defined(_M_ARM64) || defined(__arm__) || defined(_M_ARM)
#include "sse2neon.h"
#else
#include <emmintrin.h>
#endif
#endif
