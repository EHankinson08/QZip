"""GPL mcm fork (qzmf, src/engine_mcmfork) as the default engine: balanced routing rules, --max, and round trips.

Round trips need src/native/qzmf (make -C src/native qzmf) and the qzcore binaries; skipped when they are missing.
"""
import hashlib, os, random
import pytest
from src import qzrouter as Q
from qzip.core import compress_bytes, decompress_container, dump_container, load_container
from tests.regression.test_router import _csv, _log, _prose, _nifti

HAVE_QZMF = os.path.exists(Q.QZMF_BIN)
HAVE_CORE = all(os.path.exists(os.path.join(Q.BIN, b)) for b in ("qzc2", "qzb", "qzc"))

def _wide(rows=6, cols=3000, seed=7):
    r = random.Random(seed)
    return "".join(",".join(f"{r.random():.6f}" for _ in range(cols - (i % 3) * 500)) + "\n" for i in range(rows)).encode()

def p(d, o="balanced", **k):
    return Q.predict(Q.analyze(d), o, **k)[0]

@pytest.mark.skipif(not HAVE_QZMF, reason="src/native/qzmf not built")
def test_balanced_rules():
    assert p(_csv(40000)) == "qzmf" and p(_log()) == "qzmf" and p(_prose(400000)) == "qzmf"
    assert p(b"\x7fELF" + bytes(range(256)) * 40) == "qzmf"
    w = Q.analyze(_wide())
    assert w["kind"] == "csv" and w["fields"] > Q.QZMF_FIELDS_MAX
    assert p(_wide()) == "qzc2"                                    # wider than the fork's table model -> x18
    wl = Q.analyze(_wide(rows=1000, cols=400).replace(b"0.", b"x."))   # wide + letters + >= 1 MB: max would pick qzs
    assert wl["fields"] > Q.QZMF_FIELDS_MAX and Q.predict(wl, "max")[0] == "qzs" and Q.predict(wl, "balanced")[0] == "qzc2"
    assert p(_nifti()) == "qzm" and p(_nifti(label_map=True)) == "qzb_w0"
    assert p(os.urandom(50000)) == "stored" and p(b"tiny") == "stored"
    assert p(_csv(40000), "max") == "qzs"                          # auto-max keeps the old behaviour

def test_balanced_without_fork_binary(monkeypatch):
    monkeypatch.setattr(Q, "QZMF_BIN", "/nonexistent/qzmf")
    assert p(_csv(40000)) == p(_csv(40000), "max") == "qzs"

def test_qzmf_level_follows_ram():
    assert Q.qzmf_level(100000) == 11
    assert Q.qzmf_level(4000) == 10 and Q.qzmf_level(2000) == 9

def test_verifier_balanced():
    from src import qzverify as V
    assert V.rival(Q.analyze(_csv(40000)), "balanced", "qzmf")[0] is None
    assert V.rival(Q.analyze(_wide()), "balanced", "qzc2")[0] == "qzmf"

def test_cli_max_flag():
    import argparse
    from qzip import cli
    a = argparse.Namespace(codec=None, max=True, mode="auto", ram_mb=None, no_verify=False)
    assert cli._mode(a) == "auto-max"
    a.max = False
    assert cli._mode(a) == "auto"

@pytest.mark.skipif(not (HAVE_QZMF and HAVE_CORE), reason="qzmf / qzcore binaries not built")
@pytest.mark.parametrize("mode", ["auto", "codec:qzmf", "auto-max"])
def test_roundtrip_qzmf(mode):
    for data in (_csv(800), _log(800), _prose(3000), b"", os.urandom(3000), b"a,b,c\n" * 20):
        c, info = compress_bytes(data, mode=mode)
        assert decompress_container(load_container(dump_container(c))) == data
        assert c["orig_sha256"] == hashlib.sha256(data).hexdigest()
    c, info = compress_bytes(_csv(800), mode=mode)
    assert ("codec=qzmf" in str(info["tried"])) == (mode != "auto-max"), info["tried"]

@pytest.mark.skipif(not HAVE_QZMF, reason="src/native/qzmf not built")
def test_qzmf_blob_has_no_paths():
    b = Q.encode("qzmf", _prose(3000))
    assert b.startswith(b"MCMARCHIVE") and b"/tmp" not in b and b"qzmf_" not in b
    assert Q.decode("qzmf", b) == _prose(3000)

def test_cli_max_with_classic_modes():
    """v1.1: Quick is the default; --max still combines with the old default 'auto' and its alias 'classic'."""
    import argparse
    from qzip import cli
    for m in ("quick", "auto", "classic", "auto-max"):
        assert cli._mode(argparse.Namespace(codec=None, max=True, mode=m, ram_mb=None, no_verify=False)) == "auto-max"
    with pytest.raises(SystemExit):
        cli._mode(argparse.Namespace(codec=None, max=True, mode="deep", ram_mb=None, no_verify=False))
