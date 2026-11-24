**What this changes**

**Checks**
- [ ] `make native && python -m pytest -q` passes
- [ ] Format changes keep old files decodable (or bump the container/codec version)
- [ ] Benchmark claims include file, size, time, RAM and machine; every result round-trips byte-exact
