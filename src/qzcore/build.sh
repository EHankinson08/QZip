#!/bin/bash
# Build qzcore. qzc = max mode (our CM; qzs/qzm use it), qzc2 = x18, faster max mode for src/qzrouter.py, qzb = fast mode
# (our word transform + libbsc 3.4.1 fork, Apache-2.0), qtyp = CSV/log column transform. All integer math: the
# streams are bit-exact across machines, compilers and architectures (x86-64 AVX2, x86-64 scalar and arm64 produce
# identical bytes; checked in CI).
# Portable: AVX2 only on x86-64 (QZ_ARCH=portable drops it), OpenMP only when the compiler has it, .exe on Windows.
set -e; cd "$(dirname "$0")"
CC=${CC:-gcc}; CXX=${CXX:-g++}
M=$($CC -dumpmachine 2>/dev/null || echo unknown)
EXE=""; LDX=""; case "$M" in *mingw*|*windows*|*cygwin*) EXE=".exe"; LDX="-static";; esac
SIMD=""; case "$M" in x86_64*|amd64*) [ "${QZ_ARCH:-}" != portable ] && SIMD="-mavx2";; esac
F="-O3 $SIMD -w"
OMP=""; if echo 'int main(){return 0;}' | $CC -fopenmp -x c - -o /dev/null 2>/dev/null; then OMP="-fopenmp -DLIBSAIS_OPENMP -DLIBBSC_OPENMP_SUPPORT"; fi
$CXX $F -o qzc$EXE qzc.cpp $LDX
$CXX $F -pthread -o qzc2$EXE qzc2.cpp $LDX   # (QZ_THREADS segment mode uses std::thread) max mode v2 used by the auto router (format v4; -l 1 lean preset; reads v3)
$CC -O2 -w -o qtyp$EXE qtyp.c $LDX
mkdir -p .obj; objs=""
$CC $F $OMP -c vendor/libbsc/bwt/libsais/libsais.c -o .obj/libsais.o; objs=".obj/libsais.o"
for s in $(find vendor/libbsc -name '*.cpp'); do o=.obj/$(echo $s | tr / _).o; $CXX $F $OMP -c $s -o $o; objs="$objs $o"; done
$CXX $F $OMP -o qzb$EXE qzb.cpp $objs $LDX
echo "built qzc qzc2 qzb qtyp ($M${SIMD:+, $SIMD}${OMP:+, OpenMP})"
