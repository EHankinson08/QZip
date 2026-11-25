# qze: QZip everyday tiers (fast / balanced). Portable: no -march, no intrinsics, no OpenMP needed.
#   make            -> ./qze      (CC/CXX overridable, e.g. CC=aarch64-linux-gnu-gcc CXX=aarch64-linux-gnu-g++)
CC     ?= cc
CXX    ?= c++
OPT    ?= -O3
EXE    ?=
CFLAGS += $(OPT) -DNDEBUG -DZSTD_DISABLE_ASM -DZSTD_LEGACY_SUPPORT=0 -DZ7_ST -DZ7_NO_LARGE_PAGES -Ilzma -w
OBJ    ?= obj
ZSRC   := $(wildcard zstd/common/*.c zstd/compress/*.c zstd/decompress/*.c)
LSRC   := $(wildcard lzma/*.c)
BSRC   := $(shell find ../libbsc -name '*.cpp')
ZOBJ   := $(patsubst %.c,$(OBJ)/%.o,$(ZSRC))
LOBJ   := $(patsubst lzma/%.c,$(OBJ)/lzma/%.o,$(LSRC))
BOBJ   := $(patsubst ../libbsc/%.cpp,$(OBJ)/bsc/%.o,$(BSRC)) $(OBJ)/bsc/libsais.o
LIBS   ?= -lpthread

qze$(EXE): $(OBJ)/qze.o $(OBJ)/xform.o $(ZOBJ) $(LOBJ) $(BOBJ)
	$(CXX) $(OPT) -o $@ $^ $(LIBS)
$(OBJ)/qze.o: qze.c xform.h
	@mkdir -p $(dir $@); $(CC) $(CFLAGS) -std=c11 -c $< -o $@
$(OBJ)/xform.o: xform.c xform.h
	@mkdir -p $(dir $@); $(CC) $(CFLAGS) -std=c11 -c $< -o $@
$(OBJ)/lzma/%.o: lzma/%.c
	@mkdir -p $(dir $@); $(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/zstd/%.o: zstd/%.c
	@mkdir -p $(dir $@); $(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/bsc/libsais.o: ../libbsc/bwt/libsais/libsais.c
	@mkdir -p $(dir $@); $(CC) $(CFLAGS) -c $< -o $@
$(OBJ)/bsc/%.o: ../libbsc/%.cpp
	@mkdir -p $(dir $@); $(CXX) $(CFLAGS) -c $< -o $@
clean:
	rm -rf $(OBJ) qze qze.exe
.PHONY: clean
