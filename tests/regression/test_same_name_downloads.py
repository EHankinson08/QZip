"""Same-name inputs get distinct downloads and carry a relative source_path in the .qz header."""
import hashlib
import io
import os
import subprocess
import sys
import time

import pytest

from qzip.container import pack, unpack
from qzip.core import decompress_container, load_container
from qzip.identity import (
    attach_identity,
    ascii_fallback_filename,
    content_disposition,
    download_name,
    identity_from_container,
    relative_source_path,
    sanitize_source_path,
    short_sha,
)


SALES = b"date,amount\n2024-01-01,10\n2024-01-02,20\n"
INVENTORY = b"sku,qty\nA,1\nB,2\nC,3\n"
SALES_SHA = hashlib.sha256(SALES).hexdigest()
INVENTORY_SHA = hashlib.sha256(INVENTORY).hexdigest()


def _old_v100_container(data, method="stored"):
    """A v1.0.0-style container: orig_sha256/orig_size, no original_filename or source_path."""
    osha = hashlib.sha256(data).hexdigest()
    return {
        "compressed_data": data,
        "reconstruction_metadata": None,
        "method": method,
        "quantum_parameters": {
            "qzip": {
                "orig_sha256": osha,
                "orig_size": len(data),
                "verified": True,
                "format": "qzip-1",
                "decoder": "current",
                "mode": "codec:stored",
                "chosen": "stored",
            }
        },
        "orig_sha256": osha,
        "orig_size": len(data),
        "verified": True,
        "format": "qzip-1",
    }


def test_sanitize_rejects_absolute_and_dotdot():
    assert sanitize_source_path("sales/report.csv") == "sales/report.csv"
    assert sanitize_source_path(r"sales\\report.csv") == "sales/report.csv"
    # A leading slash is stripped, so this stays a relative path (never stored as absolute).
    assert sanitize_source_path("/etc/passwd", basename="passwd") == "etc/passwd"
    # leading slash is stripped, so /sales/report.csv becomes sales/report.csv (relative)
    assert sanitize_source_path("/sales/report.csv") == "sales/report.csv"
    assert sanitize_source_path("C:\\\\sales\\\\report.csv") == "sales/report.csv"
    assert sanitize_source_path("C:/sales/report.csv") == "sales/report.csv"
    assert sanitize_source_path("file:///sales/report.csv") == "sales/report.csv"
    assert sanitize_source_path("../../../etc/passwd", basename="passwd") == "passwd"
    assert sanitize_source_path("sales/../report.csv", basename="report.csv") == "report.csv"
    assert sanitize_source_path("//server/share/sales/report.csv") == "sales/report.csv"
    assert ".." not in (sanitize_source_path("../sales/report.csv", basename="report.csv") or "")
    assert not (sanitize_source_path("/abs/sales/report.csv") or "").startswith("/")


def test_relative_source_path_never_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sales").mkdir()
    src = tmp_path / "sales" / "report.csv"
    src.write_bytes(SALES)
    rel = relative_source_path(str(src))
    assert rel == "sales/report.csv"
    assert not os.path.isabs(rel)
    outside = tmp_path.parent / "elsewhere_qzip_ident" / "report.csv"
    outside.parent.mkdir(exist_ok=True)
    outside.write_bytes(SALES)
    rel2 = relative_source_path(str(outside), cwd=str(tmp_path))
    assert not os.path.isabs(rel2)
    assert ".." not in rel2.split("/")
    # may be elsewhere_qzip_ident/report.csv or report.csv — never the absolute path
    assert str(outside) not in rel2
    assert rel2.endswith("report.csv")


def test_download_name_disambiguates_collisions():
    used = set()
    a = download_name("report.csv", source_path="sales/report.csv", orig_sha256=SALES_SHA, used_names=used)
    assert a == "report.csv.qz"
    used.add(a)
    b = download_name("report.csv", source_path="inventory/report.csv", orig_sha256=INVENTORY_SHA, used_names=used)
    assert b == "report.csv (inventory).qz"
    used.add(b)
    c = download_name("report.csv", source_path="inventory/report.csv", orig_sha256=INVENTORY_SHA, used_names=used)
    assert c == f"report.csv (inventory).{short_sha(INVENTORY_SHA)}.qz"
    used.add(c)
    d = download_name("report.csv", source_path="report.csv", orig_sha256="abcd1234ffff", used_names=used)
    assert d == "report.csv.abcd12.qz"
    assert len({a, b, c, d}) == 4


def test_content_disposition_rfc5987():
    header = content_disposition("report.csv (sales).qz")
    assert 'filename="report.csv (sales).qz"' in header
    assert "filename*=UTF-8''" in header
    assert "report.csv%20%28sales%29.qz" in header
    nonascii = content_disposition("rapport.csv (ventes été).qz")
    assert "filename*=" in nonascii
    assert "UTF-8''" in nonascii
    # ASCII fallback has no raw non-ascii or quotes
    fallback = ascii_fallback_filename('a"b.csv')
    assert '"' not in fallback


def test_attach_identity_ignored_by_old_style_unpack():
    c = _old_v100_container(SALES)
    raw = pack(c)
    assert raw[:5] == b"QZIP\x02"
    out = unpack(raw)
    assert "source_path" not in out and "original_filename" not in out
    ident = identity_from_container(out)
    assert ident["source_path"] is None
    assert ident["original_filename"] is None
    assert ident["orig_sha256"] == SALES_SHA
    attach_identity(c, source_path="sales/report.csv", original_filename="report.csv")
    raw2 = pack(c)
    out2 = unpack(raw2)
    # new keys present; everything an old reader uses is still there
    assert out2["orig_sha256"] == SALES_SHA
    assert out2["orig_size"] == len(SALES)
    assert out2["method"] == "stored"
    assert out2["source_path"] == "sales/report.csv"
    assert out2["original_filename"] == "report.csv"


def test_old_v100_qz_still_decompresses():
    raw = pack(_old_v100_container(SALES))
    out = decompress_container(load_container(raw))
    assert out == SALES
    assert hashlib.sha256(out).hexdigest() == SALES_SHA


def test_cli_records_relative_path_and_info_prints_it(tmp_path):
    sales_dir = tmp_path / "sales"
    inv_dir = tmp_path / "inventory"
    sales_dir.mkdir()
    inv_dir.mkdir()
    (sales_dir / "report.csv").write_bytes(SALES)
    (inv_dir / "report.csv").write_bytes(INVENTORY)
    env = os.environ.copy()
    env.setdefault("QZIP_NO_RAY", "1")
    env.setdefault("QZIP_LOG_FILE", "")
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    py = sys.executable
    def run(*args):
        return subprocess.run(
            [py, "-m", "qzip", *args], cwd=str(tmp_path), env=env,
            capture_output=True, text=True, check=False,
        )
    r1 = run("compress", "sales/report.csv", "--codec", "stored", "-o", "sales.qz")
    r2 = run("compress", "inventory/report.csv", "--codec", "stored", "-o", "inventory.qz")
    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert r2.returncode == 0, r2.stdout + r2.stderr
    c1 = unpack((tmp_path / "sales.qz").read_bytes())
    c2 = unpack((tmp_path / "inventory.qz").read_bytes())
    assert c1["original_filename"] == "report.csv"
    assert c2["original_filename"] == "report.csv"
    assert c1["source_path"] == "sales/report.csv"
    assert c2["source_path"] == "inventory/report.csv"
    assert not os.path.isabs(c1["source_path"]) and not os.path.isabs(c2["source_path"])
    assert c1["orig_sha256"] == SALES_SHA
    assert c2["orig_sha256"] == INVENTORY_SHA
    info = run("info", "sales.qz")
    assert info.returncode == 0, info.stdout + info.stderr
    assert "sales/report.csv" in info.stdout
    assert "report.csv" in info.stdout
    assert SALES_SHA in info.stdout
    ver = run("verify", "sales.qz", "sales/report.csv")
    assert ver.returncode == 0, ver.stdout + ver.stderr
    assert "sales/report.csv" in ver.stdout
    dec = run("decompress", "inventory.qz", "-o", "out.csv")
    assert dec.returncode == 0, dec.stdout + dec.stderr
    assert (tmp_path / "out.csv").read_bytes() == INVENTORY
    assert "inventory/report.csv" in dec.stdout
    assert INVENTORY_SHA in dec.stdout

    # old archive still works through the CLI
    (tmp_path / "old.qz").write_bytes(pack(_old_v100_container(SALES)))
    old_info = run("info", "old.qz")
    assert old_info.returncode == 0, old_info.stdout + old_info.stderr
    assert "not recorded" in old_info.stdout
    old_dec = run("decompress", "old.qz", "-o", "old.csv")
    assert old_dec.returncode == 0, old_dec.stdout + old_dec.stderr
    assert (tmp_path / "old.csv").read_bytes() == SALES


# --- web app ---

@pytest.fixture
def job_env(tmp_path, monkeypatch):
    monkeypatch.setenv("QZIP_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("QZIP_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("QZIP_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("QZIP_JOB_MODE", "test")
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "0")
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "0")
    monkeypatch.setenv("QZIP_JOB_HEARTBEAT_SEC", "0")
    monkeypatch.setenv("QZIP_REQUIRE_API_KEY", "0")
    monkeypatch.setenv("QZIP_NO_RAY", "1")
    monkeypatch.setenv("QZIP_LOG_FILE", "")
    from qzip.jobs import reset_manager
    mgr = reset_manager(
        upload_dir=str(tmp_path / "uploads"),
        download_dir=str(tmp_path / "downloads"),
        jobs_dir=str(tmp_path / "jobs"),
    )
    yield mgr, tmp_path
    mgr.shutdown()


@pytest.fixture
def client(job_env, monkeypatch):
    mgr, tmp = job_env
    import importlib
    from webapp import ui as web
    importlib.reload(web)
    web.app.config["TESTING"] = True
    web.app.config["UPLOAD_FOLDER"] = str(tmp / "uploads")
    web.app.config["DOWNLOAD_FOLDER"] = str(tmp / "downloads")
    web.JOBS_STORAGE_DIR = tmp / "jobs"
    web.COMPRESSION_JOBS.clear()
    web._RESERVED_DOWNLOAD_NAMES.clear()
    from qzip import jobs as jobsmod
    jobsmod._MANAGER = mgr
    with web.app.test_client() as c:
        yield c, web, mgr, tmp


def _upload(client, data, name="report.csv", source_path=None, extra=None):
    form = {"file": (io.BytesIO(data), name), "method": "fast"}
    if source_path:
        form["source_path"] = source_path
    if extra:
        form.update(extra)
    return client.post("/api/compress", data=form, content_type="multipart/form-data")


def _disposition_name(resp):
    from qzip.identity import content_disposition as _cd  # noqa: F401
    header = resp.headers.get("Content-Disposition") or ""
    # prefer filename*
    if "filename*=" in header:
        part = header.split("filename*=", 1)[1]
        part = part.split(";", 1)[0].strip()
        if part.lower().startswith("utf-8''"):
            from urllib.parse import unquote
            return unquote(part[7:])
    if 'filename="' in header:
        return header.split('filename="', 1)[1].split('"', 1)[0]
    return header


def test_web_two_same_name_csvs_distinct_downloads_and_headers(client):
    c, web, mgr, tmp = client
    r1 = _upload(c, SALES, name="report.csv", source_path="sales/report.csv")
    assert r1.status_code == 200, r1.get_data(as_text=True)
    b1 = r1.get_json()
    r2 = _upload(c, INVENTORY, name="report.csv", source_path="inventory/report.csv")
    assert r2.status_code == 200, r2.get_data(as_text=True)
    b2 = r2.get_json()

    assert b1["original_filename"] == "report.csv"
    assert b2["original_filename"] == "report.csv"
    assert b1["source_path"] == "sales/report.csv"
    assert b2["source_path"] == "inventory/report.csv"
    assert b1["orig_sha256"] == SALES_SHA
    assert b2["orig_sha256"] == INVENTORY_SHA
    assert b1["download_name"] == "report.csv.qz"
    assert b2["download_name"] == "report.csv (inventory).qz"
    assert b1["download_name"] != b2["download_name"]

    d1 = c.get(b1["download_url"])
    d2 = c.get(b2["download_url"])
    assert d1.status_code == 200 and d2.status_code == 200
    assert d1.data[:5] == b"QZIP\x02" and d2.data[:5] == b"QZIP\x02"
    assert _disposition_name(d1) == "report.csv.qz"
    assert _disposition_name(d2) == "report.csv (inventory).qz"
    assert "filename*=" in (d1.headers.get("Content-Disposition") or "")

    h1 = unpack(d1.data)
    h2 = unpack(d2.data)
    assert h1["original_filename"] == "report.csv"
    assert h2["original_filename"] == "report.csv"
    assert h1["source_path"] == "sales/report.csv"
    assert h2["source_path"] == "inventory/report.csv"
    assert h1["orig_sha256"] == SALES_SHA
    assert h2["orig_sha256"] == INVENTORY_SHA
    # test-mode payload is the original bytes (identity codec)
    import base64
    assert base64.b64decode(h1["compressed_data"]) == SALES
    assert base64.b64decode(h2["compressed_data"]) == INVENTORY
    assert hashlib.sha256(base64.b64decode(h1["compressed_data"])).hexdigest() == SALES_SHA
    assert hashlib.sha256(base64.b64decode(h2["compressed_data"])).hexdigest() == INVENTORY_SHA


def test_web_old_v100_header_roundtrip_still_valid():
    raw = pack(_old_v100_container(SALES))
    out = decompress_container(load_container(raw))
    assert out == SALES
    ident = identity_from_container(load_container(raw))
    assert ident["source_path"] is None
    assert ident["original_filename"] is None
