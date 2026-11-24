"""Predictive auto router (src/qzrouter.py): analysis features, rules, overrides, and container round trips.

The round-trip tests need the qzcore binaries (src/qzcore/build.sh); they are skipped when the binaries are missing.
"""
import hashlib, io, os, random, struct
import pytest
from src import qzrouter as Q
from qzip.core import compress_bytes, decompress_container, dump_container, load_container

HAVE_BIN = all(os.path.exists(os.path.join(Q.BIN, b)) for b in ("qzc2", "qzb", "qzc"))

def _csv(n=3000, seed=3):
    r = random.Random(seed); o = io.StringIO(); o.write("id,city,year,flag,value\n")
    for i in range(n):
        o.write(f'{i},"{r.choice(["Ames, IA", "Boise, ID", "Provo, UT"])}",{r.randint(1990, 2025)},{r.choice("YN")},{r.random():.4f}\n')
    return o.getvalue().encode()

def _log(n=3000, seed=4):
    r = random.Random(seed)
    return "".join(f"2026-11-{r.randint(1, 28):02d} 10:{r.randint(0, 59):02d}:{r.randint(0, 59):02d} INFO "
                   f"svc{r.randint(1, 9)} request id={r.randint(0, 99999)} ok\n" for _ in range(n)).encode()

def _prose(n=4000, seed=5):
    r = random.Random(seed)
    w = "the of and to in a is that for it as was with be by on not he this are or his from at which".split()
    return " ".join(r.choice(w) for _ in range(n)).encode()

def _nifti(label_map=False, n=8192, seed=6):
    h = bytearray(352); h[0:4] = struct.pack("<i", 348); h[70:72] = struct.pack("<h", 16); h[344:348] = b"n+1\0"
    r = random.Random(seed)
    vox = bytes(n) if label_map else bytes(r.choice((0, 0, 0, r.randrange(256))) for _ in range(n))
    return bytes(h) + vox

def test_kinds():
    assert Q.analyze(_csv())["kind"] == "csv"
    assert Q.analyze(_log())["kind"] == "log"
    assert Q.analyze(_prose())["kind"] == "text"
    assert Q.analyze(b'{\n  "a": 1,\n  "b": [1, 2],\n' * 50)["kind"] == "text"     # JSON is not a table
    assert Q.analyze(_nifti())["kind"] == "medical"
    assert Q.analyze(os.urandom(50000))["kind"] == "incompressible"
    assert Q.analyze(b"\x7fELF" + bytes(range(256)) * 40)["kind"] == "binary"

def test_rules():
    p = lambda d, o="max", **k: Q.predict(Q.analyze(d), o, **k)[0]
    assert p(_csv(40000)) == "qzs" and p(_csv(800)) == "qzc2"           # small CSV -> CM wins (measured)
    assert p(_log()) == "qzc2" and p(_prose()) == "qzc2"                # free-text log -> CM wins (measured)
    assert p(_prose(400000)) == "qzc2_text"                             # >= 1 MB prose -> sorted word dictionary
    assert p(_prose(400000), ram_mb=1) == "qzc2_text_lean"
    pipe = "".join(f"20171223-22:15:{i % 60:02d}:{i % 999:03d}|Step_LSC|30002312|onStandStepChanged {i}\n"
                   for i in range(3000)).encode()
    assert p(pipe) == "qzs"                                             # field-delimited log -> qzs
    assert p(_nifti()) == "qzm" and p(_nifti(label_map=True)) == "qzb_w0"   # label maps -> BWT
    assert p(os.urandom(50000)) == "stored" and p(b"tiny") == "stored"
    assert p(_prose(), "fast") == "qzb_w0" and p(_log(), "fast") == "qzb_w0"
    assert p(_prose(), ram_mb=1) == "qzc2_lean"                         # RAM budget below need -> lean preset
    assert Q.predict(Q.analyze(_prose()), "fast")[0] in Q.FAST

def test_analysis_is_cheap():
    d = _prose(400000)
    Q.analyze(d)
    assert Q.analyze(d)["analysis_ms"] < 200      # typically ~4 ms

@pytest.mark.skipif(not HAVE_BIN, reason="qzcore binaries not built (src/qzcore/build.sh)")
@pytest.mark.parametrize("mode", ["auto", "auto-fast", "codec:qzb", "codec:qzc2_lean", "codec:qzc2_text", "codec:qzc2_text_lean", "codec:zstd"])
def test_roundtrip_router(mode):
    for data in (_csv(800), _log(800), _prose(3000), b"", os.urandom(3000)):
        c, info = compress_bytes(data, mode=mode)
        assert decompress_container(load_container(dump_container(c))) == data
        assert c["orig_sha256"] == hashlib.sha256(data).hexdigest()
        if data and mode.startswith("codec:") and c["method"] != "stored":
            assert info["tried"][0][1].startswith("ok:")

def test_verifier_gate_and_commit():
    from src import qzverify as V
    assert abs(V.confidence([0.5, 0.5])) < 1e-9 and V.confidence([1.0, 0.0]) == 1.0
    big = _csv(40000); f = Q.analyze(big); pick = Q.predict(f, "max")[0]
    assert pick == "qzs"
    # fake encoders: the rival wins the sample by 5% -> switch; by 0.5% -> ambiguous, rule pick stands
    for gain, want in ((0.05, "qzc2"), (0.005, "qzs")):
        enc = lambda c, b, g=gain: bytes(int(len(b) * (0.3 if c == "qzs" else 0.3 * (1 - g))))
        final, info = V.verify(big, f, "max", pick, enc, force=True)
        assert info["gate"] == "verify" and final == want, info
    final, info = V.verify(big, f, "max", pick, None)          # measured-unreliable pair: no trial, rule pick
    assert info["gate"] == "act" and info["skip"] and final == "qzs"
    s = V.sample(big, f, 64 << 10)
    assert s.startswith(b"id,city,year") and s.endswith(b"\n") and len(s) <= (64 << 10) + 8 * 4096 + 100
    final, info = V.verify(b"\x7fELF" + bytes(range(256)) * 400, Q.analyze(b"\x7fELF" + bytes(range(256)) * 400), "max", "qzc2", None)
    assert info["gate"] == "act" and final == "qzc2"
