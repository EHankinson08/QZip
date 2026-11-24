"""qtyp (type-aware CSV / structured-log mode) and lpaq (free-form logs) candidates."""
import hashlib, os, random, tarfile, subprocess
import pytest
from qzip.core import compress_bytes, decompress_container, dump_container, load_container
from src import qzip_routes as R, qzfast as Q

def _rt(data, mode="fast"):
    c, info = compress_bytes(data, mode=mode)
    assert decompress_container(load_container(dump_container(c))) == data
    assert c["orig_sha256"] == hashlib.sha256(data).hexdigest() and c["verified"]
    return c, info

def _csv(n=6000, seed=11):
    r = random.Random(seed)
    return ("id,date,region,price,qty,note\n" + "".join(
        f'{1000+i},2024-{r.randint(1,12):02d}-{r.randint(1,28):02d},{r.choice(["north","south","east"])},'
        f'{r.randint(1,999)}.{r.randint(0,99):02d},{r.randint(-5,50)},"x, {r.choice(["a","bb",""])}"\n'
        for i in range(n))).encode()

def _log(n=5000, seed=5):
    r = random.Random(seed)
    m = ["workerEnv.init() ok /etc/httpd/conf/workers2.properties", "mod_jk child workerEnv in error state 6",
         "jk2_init() Found child 1566 in scoreboard slot 10", "Directory index forbidden by rule: /var/www/html/"]
    return "".join(f"[Sun Dec 04 04:{r.randint(10,59)}:{r.randint(10,59)} 2005] [{r.choice(['notice','error'])}] "
                   f"{r.choice(m)}\n" for _ in range(n)).encode()

needs_qtyp = pytest.mark.skipif(not Q.qtyp_available(), reason="qtyp not built (make native)")
needs_lpaq = pytest.mark.skipif(not Q.lpaq_available(), reason="qzlpaq not built (make native)")

@needs_qtyp
def test_qtyp_direct_roundtrip_edge_cases():
    for d in (_csv(), b"a,b\n", b"a,b\n1,2", b"x\ty\n1\t\n\t-0003\n", b'a,b\n"q""x",1\r\n"m\nl",2\r\n', b""):
        assert Q.qtyp_decompress(Q.qtyp_compress(d)) == d

@pytest.mark.legacy
@needs_qtyp
def test_csv_routes_to_qtyp():
    data = _csv()
    c, info = _rt(data)
    q = c["quantum_parameters"]["qzip"]
    assert q["detected_type"] == "csv" and q["chosen"] == "qtyp" and len(info["tried"]) == 1
    import lzma
    assert info["payload"] < len(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))

@pytest.mark.legacy
@needs_lpaq
def test_freeform_log_routes_to_lpaq_in_gpl():
    c, _ = _rt(_log())
    assert c["quantum_parameters"]["qzip"]["chosen"] == "lpaq"

