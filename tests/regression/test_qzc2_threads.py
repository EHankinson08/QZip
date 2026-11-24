"""qzc2 segment-parallel mode (QZ_THREADS): byte-exact round trips, and QZ_THREADS=1 is the plain single-model stream."""
import os, random, subprocess
import pytest
from src import qzrouter as Q

BIN = os.path.join(Q.BIN, "qzc2")
pytestmark = pytest.mark.skipif(not os.path.exists(BIN), reason="qzc2 not built (src/qzcore/build.sh)")

def _text(n, seed=7):
    r = random.Random(seed); words = ["".join(r.choice("etaoinshrdlu") for _ in range(r.randint(2, 9))) for _ in range(3000)]
    out = []; size = 0
    while size < n:
        line = " ".join(r.choice(words) for _ in range(r.randint(4, 14))) + (".\n" if r.random() < 0.3 else ", ")
        out.append(line); size += len(line)
    return "".join(out).encode()[:n]

def _rt(tmp_path, data, threads, args):
    src, q, d = tmp_path / "in", tmp_path / "in.q", tmp_path / "out"
    src.write_bytes(data); env = dict(os.environ, QZ_THREADS=str(threads))
    subprocess.run([BIN, "c", str(src), str(q), "20"] + args + ["-m", "1024"], check=True, env=env, capture_output=True, timeout=600)
    subprocess.run([BIN, "d", str(q), str(d)], check=True, capture_output=True, timeout=600)
    assert d.read_bytes() == data
    return q.read_bytes()

@pytest.mark.parametrize("args", [[], ["2"]], ids=["raw", "prose"])
def test_threads_roundtrip(tmp_path, args):
    data = _text(5 << 20)
    one = _rt(tmp_path, data, 1, args)
    two = _rt(tmp_path, data, 2, args)
    assert one != two  # segmented stream really used
    assert len(two) < len(data) // 2
