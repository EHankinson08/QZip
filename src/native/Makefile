# QZip native text codecs (GPL-3.0-or-later build).
#   make     libqznative.so (libbsc+libsais, Apache-2.0) + qzmf (QZip mcm fork, the default engine)
#            + qzcm (stock mcm 0.84) + qzlpaq (lpaq1) + qtyp
#   make clean
# ARCH=native (default) tunes for this CPU; use ARCH=x86-64-v2 (or similar) for portable/Docker builds.
ARCH    ?= native
CXX     ?= g++
CC      ?= gcc
OPT      = -O3 -march=$(ARCH) -fPIC -fopenmp -DNDEBUG -w
BSC_DEF  = -DLIBBSC_OPENMP_SUPPORT -DLIBSAIS_OPENMP
OBJ      = obj
BSC_SRC := $(shell find libbsc -name '*.cpp')
BSC_OBJ := $(patsubst %.cpp,$(OBJ)/%.o,$(BSC_SRC))
LIB_OBJ := $(BSC_OBJ) $(OBJ)/libsais.o $(OBJ)/qz_wrt.o $(OBJ)/qz_bsc.o
MCM_SRC  = Archive.cpp Huffman.cpp MCM.cpp Memory.cpp Util.cpp Compressor.cpp File.cpp LZ.cpp Tests.cpp

all: libqznative.so qtyp qzcm qzlpaq qzmf
	@rm -f EDITION
	@echo "built libqznative.so + qtyp + qzcm + qzlpaq + qzmf"

libqznative.so: $(LIB_OBJ)
	$(CXX) -shared -fopenmp -o $@ $^

$(OBJ)/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(OPT) $(BSC_DEF) -c $< -o $@

$(OBJ)/libsais.o: libbsc/bwt/libsais/libsais.c
	@mkdir -p $(OBJ)
	$(CC) $(OPT) $(BSC_DEF) -c $< -o $@

$(OBJ)/qz_wrt.o: qz_wrt.c
	@mkdir -p $(OBJ)
	$(CC) $(OPT) -c $< -o $@

$(OBJ)/qz_bsc.o: qz_bsc.c
	@mkdir -p $(OBJ)
	$(CC) $(OPT) $(BSC_DEF) -I. -c $< -o $@

# mcm 0.84 (GPL-3.0-or-later, Mathieu Chartier) with QZip build fixes (mcm/QZIP_PATCHES.diff).
qzcm: $(addprefix mcm/,$(MCM_SRC))
	cd mcm && $(CXX) -DNDEBUG -O3 -march=$(ARCH) -std=c++14 -fpermissive -w -D_FILE_OFFSET_BITS=64 -o ../qzcm $(MCM_SRC) -lpthread

# qzmf: QZip-mcm fork, the default engine (src/engine_mcmfork, GPL-3.0-or-later; mcm by Mathieu Chartier + QZip
# CSV/table and text contexts, see src/engine_mcmfork/QZIP_FORK.md). Built WITHOUT -march so every machine runs the
# exact flags that were benchmarked.
MF_SRC   = $(addprefix ../engine_mcmfork/,$(MCM_SRC))
MF_HDR   = $(wildcard ../engine_mcmfork/*.hpp)
qzmf: $(MF_SRC) $(MF_HDR)
	cd ../engine_mcmfork && $(CXX) -DNDEBUG -O3 -fomit-frame-pointer -msse2 -std=c++11 -fpermissive -Wno-changes-meaning -w -D_FILE_OFFSET_BITS=64 -o ../native/qzmf $(MCM_SRC) -lpthread

# qtyp: QZip type-aware column transform (QZip-authored; GPL-3.0-or-later).
qtyp: fast/qtyp.c
	$(CC) -O2 -o $@ $<

# lpaq1 (C) 2007 Matt Mahoney, GPL-2.0-or-later, used under GPL-3.0 (separate program used by src/qzfast.py).
qzlpaq: fast/lpaq1.cpp
	$(CXX) -O3 -march=$(ARCH) -DNDEBUG -w -o $@ $<

clean:
	rm -rf $(OBJ) libqznative.so qzcm qtyp qzlpaq qzmf EDITION

.PHONY: all clean
