# QZip native text codecs (GPL-3.0-or-later build).
#   make     libqznative.so (libbsc+libsais, Apache-2.0) + qzmf (QZip mcm fork, the default engine)
#            + qzcm (stock mcm 0.84) + qzlpaq (lpaq1) + qtyp
#   make clean
# ARCH=native (default) tunes for this CPU; ARCH=x86-64-v2 (or similar) or ARCH=portable (no -march) for
# redistributable builds (CI, wheels, Docker).
# Portable: MACHINE (from the compiler) picks x86 flags only on x86; OpenMP is used when the compiler has it
# (Apple clang does not: libbsc then runs single-threaded per block). EXE=.exe on Windows (MSYS2/MinGW).
ARCH    ?= native
CXX     ?= g++
CC      ?= gcc
MACHINE := $(shell $(CC) -dumpmachine 2>/dev/null)
IS_X86  := $(if $(filter x86_64% i686% amd64%,$(MACHINE)),1,)
IS_WIN  := $(if $(findstring mingw,$(MACHINE))$(findstring windows,$(MACHINE)),1,)
EXE     ?= $(if $(IS_WIN),.exe,)
HAS_OMP := $(shell echo 'int main(){return 0;}' | $(CC) -fopenmp -x c - -o /dev/null 2>/dev/null && echo 1)
OMPF     = $(if $(HAS_OMP),-fopenmp,)
MARCH    = $(if $(filter portable,$(ARCH)),,$(if $(filter native,$(ARCH)),$(if $(IS_X86),-march=native,-mcpu=native),-march=$(ARCH)))
OPT      = -O3 $(MARCH) $(if $(IS_WIN),,-fPIC) $(OMPF) -DNDEBUG -w
BSC_DEF  = $(if $(HAS_OMP),-DLIBBSC_OPENMP_SUPPORT -DLIBSAIS_OPENMP,)
SSE2     = $(if $(IS_X86),-msse2,)
OBJ      = obj
BSC_SRC := $(shell find libbsc -name '*.cpp')
BSC_OBJ := $(patsubst %.cpp,$(OBJ)/%.o,$(BSC_SRC))
LIB_OBJ := $(BSC_OBJ) $(OBJ)/libsais.o $(OBJ)/qz_wrt.o $(OBJ)/qz_bsc.o
MCM_SRC  = Archive.cpp Huffman.cpp MCM.cpp Memory.cpp Util.cpp Compressor.cpp File.cpp LZ.cpp Tests.cpp

all: libqznative.so qtyp$(EXE) qzcm$(EXE) qzlpaq$(EXE) qzmf$(EXE) qze$(EXE)
	@rm -f EDITION
	@echo "built libqznative.so + qtyp + qzcm + qzlpaq + qzmf + qze (everyday tiers)"

libqznative.so: $(LIB_OBJ)
	$(CXX) -shared $(OMPF) -o $@ $^

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
qzcm$(EXE): $(addprefix mcm/,$(MCM_SRC))
	cd mcm && $(CXX) -DNDEBUG -O3 $(MARCH) -std=gnu++14 -fpermissive -w -D_FILE_OFFSET_BITS=64 -o ../qzcm$(EXE) $(MCM_SRC) -lpthread

# qzmf: QZip-mcm fork, the default engine (src/engine_mcmfork, GPL-3.0-or-later; mcm by Mathieu Chartier + QZip
# CSV/table and text contexts, see src/engine_mcmfork/QZIP_FORK.md). Built WITHOUT -march so every machine runs the
# exact flags that were benchmarked.
MF_SRC   = $(addprefix ../engine_mcmfork/,$(MCM_SRC))
MF_HDR   = $(wildcard ../engine_mcmfork/*.hpp)
qzmf$(EXE): $(MF_SRC) $(MF_HDR)
	cd ../engine_mcmfork && $(CXX) -DNDEBUG -O3 -fomit-frame-pointer $(SSE2) -std=gnu++11 -fpermissive -w -D_FILE_OFFSET_BITS=64 -o ../native/qzmf$(EXE) $(MCM_SRC) -lpthread

# qtyp: QZip type-aware column transform (QZip-authored; GPL-3.0-or-later).
qtyp$(EXE): fast/qtyp.c
	$(CC) -O2 -o $@ $<

# lpaq1 (C) 2007 Matt Mahoney, GPL-2.0-or-later, used under GPL-3.0 (separate program used by src/qzfast.py).
qzlpaq$(EXE): fast/lpaq1.cpp
	$(CXX) -O3 $(MARCH) -DNDEBUG -w -o $@ $<

# qze: QZip everyday tiers (fast / balanced), portable C + vendored zstd + libbsc (src/native/everyday)
qze$(EXE): everyday/qze.c everyday/Makefile
	$(MAKE) -C everyday CC=$(CC) CXX=$(CXX) EXE=$(EXE)
	cp everyday/qze$(EXE) qze$(EXE)

clean:
	rm -rf $(OBJ) libqznative.so qzcm qtyp qzlpaq qzmf qze *.exe EDITION
	$(MAKE) -C everyday clean

.PHONY: all clean
