"""Router env for qzc2 segment threads: QZIP_QZC2_THREADS only. QZIP_THREADS belongs to libbsc (src/native/qz_native.py)
and must never change the qzc2 thread count (a shared name used to split qzc2 into 8 segments, about +10% size)."""
import pytest
from src import qzrouter as Q

BIG = 64 << 20  # large enough that the 2 MB-per-segment cap never limits the count

@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("QZIP_THREADS", raising=False)
    monkeypatch.delenv("QZIP_QZC2_THREADS", raising=False)

def test_default_is_one():
    assert Q.qzc2_threads(BIG) == 1

@pytest.mark.parametrize("v", ["8", "16", "auto"])
def test_qzip_threads_does_not_change_qzc2(monkeypatch, v):
    monkeypatch.setenv("QZIP_THREADS", v)
    assert Q.qzc2_threads(BIG) == 1

def test_qzc2_threads_var(monkeypatch):
    monkeypatch.setenv("QZIP_THREADS", "8")
    monkeypatch.setenv("QZIP_QZC2_THREADS", "4")
    assert Q.qzc2_threads(BIG) == 4
    assert Q.qzc2_threads(5 << 20) == 2  # capped at one segment per 2 MB

def test_qzc2_threads_auto(monkeypatch):
    monkeypatch.setenv("QZIP_QZC2_THREADS", "auto")
    monkeypatch.setattr(Q, "free_cores", lambda: 32)
    assert Q.qzc2_threads(BIG) == 16

@pytest.mark.parametrize("qzc2_var,expect", [(None, "1"), ("4", "4")])
def test_encode_env_ignores_qzip_threads(monkeypatch, qzc2_var, expect):
    """The env handed to the qzc2 subprocess gets QZ_THREADS from QZIP_QZC2_THREADS only, even with QZIP_THREADS=8."""
    monkeypatch.setenv("QZIP_THREADS", "8")
    if qzc2_var:
        monkeypatch.setenv("QZIP_QZC2_THREADS", qzc2_var)
    seen = {}
    monkeypatch.setattr(Q, "_bin", lambda name: name)
    monkeypatch.setattr(Q, "_with_files", lambda data, fn: fn("in", "out") or b"")
    monkeypatch.setattr(Q, "_run", lambda args, env=None: seen.update(env=env))
    Q.encode("qzc2_text", b"x" * BIG)
    assert seen["env"]["QZ_THREADS"] == expect
