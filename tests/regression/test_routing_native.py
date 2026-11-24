"""QZip router + native text mode regression tests (src/qzip_routes.py, src/native, src/safe_compress.py).

Always run: synthetic text / log / CSV / binary / image round trips through the routed modes, type detection, the
and the check-only fallback. With real files: set QZIP_DATA_DIR (Apache.log,
openstack_abnormal.log, HealthApp.log, a CSV) and QZIP_ENWIK8 (path to enwik8; a 5 MB slice is used).
"""
import hashlib, os, random
import pytest
from qzip.core import compress_bytes, decompress_container, dump_container, load_container, engine
from src import qzip_routes as R

def _rt(data, mode="fast"):
    c, info = compress_bytes(data, mode=mode)
    blob = dump_container(c)                      # same .qz bytes as the web download and the CLI
    assert decompress_container(load_container(blob)) == data
    assert decompress_container(load_container(dump_container(c, gz=False))) == data
    assert c["orig_sha256"] == hashlib.sha256(data).hexdigest() and c["verified"]
    return c, info

def _prose(n=300_000, seed=3):
    r = random.Random(seed)
    words = ("the of and to in is was for on that with as by at from his her an were which this be are has "
             "compression quantum entropy archive language model river mountain history science people city").split()
    out = []
    while sum(map(len, out)) < n:
        s = " ".join(r.choice(words) for _ in range(r.randint(6, 18)))
        out.append(s[0].upper() + s[1:] + ". ")
        if r.random() < 0.1: out.append("\n\n")
    return "".join(out).encode()

def _freeform_log(n=4000, seed=5):
    r = random.Random(seed)
    lv = ["notice", "error", "warn"]
    msgs = ["workerEnv.init() ok /etc/httpd/conf/workers2.properties", "mod_jk child workerEnv in error state 6",
            "jk2_init() Found child 1566 in scoreboard slot 10", "Directory index forbidden by rule: /var/www/html/"]
    return "".join(f"[Sun Dec 04 04:{r.randint(10,59)}:{r.randint(10,59)} 2005] [{r.choice(lv)}] {r.choice(msgs)}\n"
                   for _ in range(n)).encode()

def _csv(n=5000, seed=7):
    r = random.Random(seed)
    return ("id,region,year,status,value\n" + "".join(
        f"{i},{r.choice(['north','south','east','west'])},{r.randint(2000,2022)},{r.choice(['ok','warn'])},{r.randint(0,99)}\n"
        for i in range(n))).encode()

def _native_ok():
    try:
        from src.native import qz_native as N
        return N.lib_available()
    except Exception:
        return False

needs_native = pytest.mark.skipif(not _native_ok(), reason="native library not built (make native)")

@pytest.mark.legacy
def test_type_detection():
    e = __import__("src.safe_compress", fromlist=["_legacy_engine"])._legacy_engine()
    assert R.refine_type(e, _prose(), "text")[0] == "text"
    assert R.refine_type(e, _freeform_log(), "text")[0] == "log"
    assert R.refine_type(e, _csv(), "text")[0] == "csv"
    hl = "".join(f"20171223-22:15:{i%60:02d}:606|Step_LSC|30002312|onStandStepChanged {i}\n" for i in range(3000)).encode()
    assert R.refine_type(e, hl, "text")[0] == "log_structured"
    assert R.refine_type(e, b"\xff\xd8\xff\xe0" + os.urandom(5000), "binary")[0] == "image"
    assert R.refine_type(e, b"\x1f\x8b\x08\x00" + os.urandom(5000), "binary")[0] == "compressed"
    assert R.refine_type(e, os.urandom(5000), "binary")[0] == "binary"
    assert R.refine_type(e, b"x", "medical_imaging")[0] == "medical"

@pytest.mark.legacy
@needs_native
def test_text_routes_to_native_fast():
    data = _prose()
    c, info = _rt(data, "fast")
    q = c["quantum_parameters"]["qzip"]
    assert q["detected_type"] == "text" and q["chosen"] == "text_fast" and c["method"] == "qzip_text"
    assert q["fallback"] is None and len(info["tried"]) == 1          # one encode, no try-all
    import lzma
    assert info["payload"] < len(lzma.compress(data, preset=9))

@pytest.mark.legacy
@needs_native
def test_freeform_log_routes_and_roundtrips():
    c, info = _rt(_freeform_log(), "fast")
    assert c["quantum_parameters"]["qzip"]["detected_type"] == "log"
    assert c["quantum_parameters"]["qzip"]["chosen"] in ("lpaq", "text_fast")

@pytest.mark.legacy
def test_csv_keeps_old_route_without_qtyp():
    data = _csv()
    c, info = _rt(data, "fast")
    q = c["quantum_parameters"]["qzip"]
    assert q["detected_type"] == "csv"
    if "qtyp" not in R._REGISTRY:
        assert q["chosen"] == "oct2025_route"

@pytest.mark.legacy
def test_image_and_binary_routes():
    jpg = b"\xff\xd8\xff\xe0" + bytes(200_000)
    c, _ = _rt(jpg, "fast")
    assert c["quantum_parameters"]["qzip"]["detected_type"] == "image" and c["method"] in ("stored_xz", "stored")
    for data in (b"", os.urandom(5000), bytes(range(256)) * 400):
        _rt(data, "fast")

def test_legacy_mode_is_router_off():
    c, _ = _rt(_prose(100_000), "legacy")
    assert c["method"] != "qzip_text" and c["quantum_parameters"]["qzip"]["detected_type"] is None

@needs_native
def test_verification_failure_falls_back_to_xz(monkeypatch):
    real = R._REGISTRY["text_fast"]["compress"]
    monkeypatch.setitem(R._REGISTRY["text_fast"], "compress", lambda b: real(b)[:-7] + b"garbage")
    data = _prose(120_000)
    c, info = _rt(data, "fast")
    assert c["method"] == "stored_xz" and c["quantum_parameters"]["qzip"]["fallback"] == "stored_xz"

@pytest.mark.legacy
def test_plugin_registry_hook():
    import zlib
    R.register_candidate("unit_zlib", lambda b: zlib.compress(b, 9), zlib.decompress)
    old = R.ROUTES["binary"]["fast"]
    try:
        R.ROUTES["binary"]["fast"] = ["unit_zlib"]
        data = bytes(range(256)) * 400 + os.urandom(100)
        c, _ = _rt(data, "fast")
        assert c["method"] == "unit_zlib"
    finally:
        R.ROUTES["binary"]["fast"] = old

DATA = os.environ.get("QZIP_DATA_DIR")
REAL = ["Apache.log", "openstack_abnormal.log", "HealthApp.log", "fndds_ingredient_nutrient_value (3).csv"]

@pytest.mark.skipif(not DATA, reason="set QZIP_DATA_DIR for real-file round trips")
@pytest.mark.parametrize("name", REAL)
def test_real_files_roundtrip(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        pytest.skip(f"{name} missing")
    data = open(p, "rb").read()
    c, info = _rt(data, "fast")
    _, _old = compress_bytes(data, mode="legacy")
    print(name, info["detected_type"], info["route_choice"], info["payload"], "legacy", _old["payload"])

E8 = os.environ.get("QZIP_ENWIK8")

@needs_native
@pytest.mark.skipif(not E8 or not os.path.exists(E8 or ""), reason="set QZIP_ENWIK8 for the enwik8 slice")
def test_enwik8_5mb_slice():
    with open(E8, "rb") as f:
        data = f.read(5_000_000)
    c, info = _rt(data, "fast")
    assert info["route_choice"] == "text_fast" and info["payload"] < 1_300_000, info
