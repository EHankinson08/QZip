"""QZip regression tests.

Fast tests always run. Showcase tests need the real files: set QZIP_DATA_DIR to a folder containing
Data8277.csv, products-2000000.csv, fndds_ingredient_nutrient_value.csv, youth-dataset-long-form.csv
(and optionally the original web-app artifacts *.qc.gz next to them). Ratios are the measured Nov-2026 values.
"""
import csv, gzip, hashlib, io, json, os, random
import pytest
from qzip.core import compress_bytes, decompress_container, dump_container, load_container, engine

def _sample_csv(n=4000, seed=7):
    r = random.Random(seed)
    out = io.StringIO()
    out.write("id,region,year,status,value\n")
    for i in range(n):
        out.write(f"{i},{r.choice(['north','south','east','west'])},{r.randint(2000,2022)},{r.choice(['ok','warn'])},{r.randint(0,99)}\n")
    return out.getvalue().encode()

@pytest.mark.parametrize("mode", ["fast", "hybrid", "auto"])
def test_roundtrip_modes(mode):
    data = _sample_csv()
    c, info = compress_bytes(data, mode=mode)
    blob = dump_container(c)
    assert decompress_container(load_container(blob)) == data
    assert c["verified"] and c["orig_sha256"] == hashlib.sha256(data).hexdigest()

def test_binary_and_empty():
    for data in (b"", os.urandom(5000), bytes(range(256)) * 50):
        c, _ = compress_bytes(data, mode="auto")
        assert decompress_container(load_container(dump_container(c))) == data

def test_web_qc_padding_and_uncompressed():
    data = _sample_csv(500)
    c, _ = compress_bytes(data, mode="fast")
    raw = json.dumps(c).encode() + b" " * 700        # web .qc: JSON padded with spaces
    assert decompress_container(load_container(raw)) == data
    assert decompress_container(load_container(gzip.compress(raw))) == data

def test_tampered_container_rejected():
    data = _sample_csv(500)
    c, _ = compress_bytes(data, mode="auto")
    c["orig_sha256"] = "0" * 64
    c["quantum_parameters"]["qzip"]["orig_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        decompress_container(c)

class _FakeGPU:
    def __init__(self, lossy): self.lossy = lossy
    def compress(self, b): return b"QGPU" + b
    def decompress(self, z): return z[4:-1] if self.lossy else z[4:]

@pytest.mark.legacy
def test_gpu_csv_guard_blocks_lossy_output():
    e = engine()
    data = _sample_csv(300)
    rows = list(csv.reader(io.StringIO(data.decode())))
    saved = e.gpu_csv_compressor
    try:
        e.gpu_csv_compressor = _FakeGPU(lossy=True)
        out = e._compress_csv_with_neural(rows, data)
        assert not (isinstance(out[0], (bytes, bytearray)) and bytes(out[0])[:4] == b"QGPU")
        e.gpu_csv_compressor = _FakeGPU(lossy=False)
        out = e._compress_csv_with_neural(rows, data)
        assert bytes(out[0])[:4] == b"QGPU"
    finally:
        e.gpu_csv_compressor = saved

DATA = os.environ.get("QZIP_DATA_DIR")
SHOWCASE = [  # file, minimum ratio (payload incl. metadata, as measured), mode
    ("Data8277.csv", 116.0, "fast"),
    ("products-2000000.csv", 5.16, "fast"),
    ("fndds_ingredient_nutrient_value.csv", 56.0, "auto"),   # the 17,017,713 B showcase copy
    # routed mode = one encode with the Oct-2025 CSV route (26.96:1). The pre-router try-all 'auto' reached 27.0 by
    # also running the Nov-2025 engine; with the qtyp CSV codec installed the route gives 38.7:1.
    ("youth-dataset-long-form.csv", 26.9, "auto"),
]

@pytest.mark.skipif(not DATA, reason="set QZIP_DATA_DIR to run showcase regression")
@pytest.mark.parametrize("name,min_ratio,mode", SHOWCASE)
def test_showcase_ratio(name, min_ratio, mode):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        pytest.skip(f"{name} not in QZIP_DATA_DIR")
    data = open(p, "rb").read()
    c, info = compress_bytes(data, mode=mode)
    assert decompress_container(load_container(dump_container(c))) == data
    assert info["ratio"] >= min_ratio, info

@pytest.mark.skipif(not DATA, reason="set QZIP_DATA_DIR to decode original web-app artifacts")
def test_old_web_artifacts_still_decode():
    found = 0
    for name, _, _ in SHOWCASE:
        a, o = os.path.join(DATA, name + ".qc.gz"), os.path.join(DATA, name)
        if os.path.exists(a) and os.path.exists(o):
            found += 1
            assert decompress_container(load_container(open(a, "rb").read())) == open(o, "rb").read()
    if not found:
        pytest.skip("no artifact/original pairs present")


def test_qz_container_is_raw_payload_plus_small_header():
    """The .qz file is the codec output plus a small header (no base64/gzip), and older JSON containers still load."""
    import base64
    from qzip.container import pack, unpack, strip_ext
    data = _sample_csv(3000)
    c, _ = compress_bytes(data, mode="auto")
    payload = base64.b64decode(c["compressed_data"])
    qz = dump_container(c)
    assert qz[:5] == b"QZIP\x02" and qz.endswith(payload) and len(qz) - len(payload) < 2048
    assert decompress_container(load_container(qz)) == data
    old = gzip.compress(json.dumps(c).encode() + b" " * 100)          # 2025/2026 .qc.gz
    assert decompress_container(load_container(old)) == data
    with pytest.raises(ValueError):
        unpack(b"QZIP\x09" + bytes(8))
    assert strip_ext("a.csv.qz") == strip_ext("a.csv.qc.gz") == strip_ext("a.csv.qc") == "a.csv"


def test_cli_writes_qz_and_reads_old_qc_gz(tmp_path):
    import subprocess, sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = tmp_path / "t.csv"; src.write_bytes(_sample_csv(2000))
    run = lambda *a: subprocess.run([sys.executable, "-m", "qzip", *a], cwd=root, capture_output=True, text=True, check=True)
    run("compress", str(src))
    assert (tmp_path / "t.csv.qz").read_bytes()[:5] == b"QZIP\x02"
    src.rename(tmp_path / "orig.csv")
    run("decompress", str(tmp_path / "t.csv.qz"))
    assert (tmp_path / "t.csv").read_bytes() == (tmp_path / "orig.csv").read_bytes()
    c = load_container((tmp_path / "t.csv.qz").read_bytes())
    (tmp_path / "old.csv.qc.gz").write_bytes(gzip.compress(json.dumps(c).encode()))
    run("decompress", str(tmp_path / "old.csv.qc.gz"))
    assert (tmp_path / "old.csv").read_bytes() == (tmp_path / "orig.csv").read_bytes()
