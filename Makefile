# QZip build helpers.
#   make native                 native codecs: default engine qzmf (QZip mcm fork) + x18/qzs/qzb + libbsc helpers
#   make native ARCH=x86-64-v2  portable build (default ARCH=native)
#   make test                   regression tests (pytest)
ARCH    ?= native
PY      ?= python3

native:
	$(MAKE) -C src/native -j$$(nproc 2>/dev/null || echo 4) ARCH=$(ARCH)
	src/qzcore/build.sh    # QZip's own codecs (x18 qzc2, qzs, qzb, qzm helpers)

clean-native:
	$(MAKE) -C src/native clean

test: native
	$(PY) -m pytest -q

.PHONY: native clean-native test
