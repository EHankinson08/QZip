#!/bin/bash
# Build qzcore. qzc = max mode (our CM; qzs/qzm use it), qzc2 = faster max mode for src/qzrouter.py, qzb = fast mode (our word transform + libbsc 3.4.1 fork, Apache-2.0),
# qtyp = CSV/log column transform. All integer math -> bit-exact across x86-64 machines and compilers.
set -e; cd "$(dirname "$0")"
F="-O3 -mavx2 -w"; OMP="-fopenmp -DLIBSAIS_OPENMP -DLIBBSC_OPENMP_SUPPORT"
g++ $F -o qzc qzc.cpp
g++ $F -pthread -o qzc2 qzc2.cpp   # (QZ_THREADS segment mode uses std::thread) max mode v2 used by the auto router (format v4; -l 1 lean preset; reads v3)
gcc -O2 -w -o qtyp qtyp.c
mkdir -p .obj; objs=""
gcc $F $OMP -c vendor/libbsc/bwt/libsais/libsais.c -o .obj/libsais.o; objs=".obj/libsais.o"
for s in $(find vendor/libbsc -name '*.cpp'); do o=.obj/$(echo $s | tr / _).o; g++ $F $OMP -c $s -o $o; objs="$objs $o"; done
g++ $F $OMP -o qzb qzb.cpp $objs
echo built qzc qzc2 qzb qtyp
