"""Background-job web API: no hard timeout, cancel kills the worker, uploads hit disk."""
import gzip
import io
import json
import os
import time

import pytest
from qzip.container import unpack

# Job-manager unit tests do not need Flask. Web tests import the app after env is set.


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


def test_slow_job_reports_elapsed_and_cancel_kills_worker(job_env, monkeypatch):
    mgr, tmp = job_env
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "8")
    src = tmp / "slow.bin"
    src.write_bytes(b"hello qzip job " * 100)
    job_id = mgr.start(str(src), filename="slow.bin", mode="fast", heartbeat_required=False)
    deadline = time.time() + 3
    saw_elapsed = False
    while time.time() < deadline:
        st = mgr.status(job_id, heartbeat=False)
        if st and st.get("elapsed_time", 0) >= 0.2 and st.get("status") in {"starting", "compressing"}:
            saw_elapsed = True
            break
        time.sleep(0.05)
    assert saw_elapsed, mgr.status(job_id, heartbeat=False)
    assert mgr.process_alive(job_id)
    pid = mgr.pid(job_id)
    assert pid and os.path.exists(f"/proc/{pid}")
    # Upload was on disk while the job ran (the worker has not yet deleted it).
    assert os.path.exists(mgr.input_path(job_id))
    assert os.path.getsize(mgr.input_path(job_id)) == src.stat().st_size

    final = mgr.cancel(job_id)
    assert final["status"] == "cancelled"
    time.sleep(0.2)
    assert not mgr.process_alive(job_id)
    assert not os.path.exists(f"/proc/{pid}")
    assert not os.path.exists(mgr.input_path(job_id))
    # No leftover output from a cancelled job.
    out = mgr.output_path(job_id)
    assert not out or not os.path.exists(out)


def test_timeout_off_by_default(job_env, monkeypatch):
    from qzip.jobs import compress_timeout_sec
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "0")
    assert compress_timeout_sec() == 0
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "")
    assert compress_timeout_sec() == 0
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "12.5")
    assert compress_timeout_sec() == 12.5


def test_wait_timeout_cancels_worker(job_env, monkeypatch):
    mgr, tmp = job_env
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "8")
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "0.4")
    src = tmp / "t.bin"
    src.write_bytes(b"abc" * 50)
    job_id = mgr.start(str(src), "t.bin", heartbeat_required=False)
    pid = mgr.pid(job_id)
    t0 = time.time()
    with pytest.raises(TimeoutError):
        mgr.wait(job_id, timeout=0.4)
    assert time.time() - t0 < 3
    time.sleep(0.2)
    assert not mgr.process_alive(job_id)
    if pid:
        assert not os.path.exists(f"/proc/{pid}")
    st = mgr.status(job_id, heartbeat=False)
    assert st["status"] in {"timeout", "cancelled"}


def test_manager_completes_and_writes_qz(job_env):
    mgr, tmp = job_env
    src = tmp / "ok.bin"
    data = b"payload-bytes-for-job"
    src.write_bytes(data)
    job_id = mgr.start(str(src), "ok.bin", heartbeat_required=False)
    st = mgr.wait(job_id, timeout=10)
    assert st["status"] == "complete"
    assert st["elapsed_time"] >= 0
    result = st["result"]
    out = result["output_path"]
    raw = open(out, "rb").read()
    assert raw[:5] == b"QZIP\x02" and out.endswith(".qz")
    blob = unpack(raw)
    import base64
    assert base64.b64decode(blob["compressed_data"]) == data
    assert result["original_size"] == len(data)
    assert result["download_url"] == f"/api/download/{job_id}"


@pytest.fixture
def client(job_env, monkeypatch):
    mgr, tmp = job_env
    # Import (or re-bind) the Flask app against the temp dirs.
    import importlib
    from webapp import ui as web
    importlib.reload(web)
    web.app.config["TESTING"] = True
    web.app.config["UPLOAD_FOLDER"] = str(tmp / "uploads")
    web.app.config["DOWNLOAD_FOLDER"] = str(tmp / "downloads")
    web.JOBS_STORAGE_DIR = tmp / "jobs"
    from qzip import jobs as jobsmod
    jobsmod._MANAGER = mgr
    with web.app.test_client() as c:
        yield c, web, mgr, tmp


def _upload(client, data=b"hello world\n", name="hello.txt", extra=None, headers=None):
    kw = {"data": {"file": (io.BytesIO(data), name), "method": "fast"}}
    if extra:
        kw["data"].update(extra)
    if headers:
        kw["headers"] = headers
    kw["content_type"] = "multipart/form-data"
    return client.post("/api/compress", **kw)


def test_sync_wait_is_backward_compatible(client):
    c, web, mgr, tmp = client
    data = b"compat-payload-" * 20
    resp = _upload(c, data=data, name="note.txt")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True
    assert body["job_id"]
    assert body["original_filename"] == "note.txt"
    assert body["original_size"] == len(data)
    assert body["download_url"] == f"/api/download/{body['job_id']}"
    assert "compression_ratio" in body and "compression_time" in body
    dl = c.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.data[:5] == b"QZIP\x02"
    blob = unpack(dl.data)
    import base64
    assert base64.b64decode(blob["compressed_data"]) == data


def test_async_job_poll_and_download(client, monkeypatch):
    c, web, mgr, tmp = client
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "0.4")
    data = b"async-file-bytes"
    resp = _upload(c, data=data, name="async.bin", extra={"async": "1"})
    assert resp.status_code == 202, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["accepted"] is True
    job_id = body["job_id"]
    assert body["status_url"] == f"/api/progress/{job_id}"
    assert body["cancel_url"] == f"/api/jobs/{job_id}/cancel"
    # Input was streamed to disk.
    assert mgr.input_path(job_id) and (
        os.path.exists(mgr.input_path(job_id)) or mgr.status(job_id)["status"] == "complete"
    )
    deadline = time.time() + 8
    last = None
    while time.time() < deadline:
        pr = c.get(f"/api/progress/{job_id}")
        assert pr.status_code == 200
        last = pr.get_json()
        assert "elapsed_time" in last
        if last["status"] == "complete":
            break
        time.sleep(0.1)
    assert last and last["status"] == "complete", last
    assert last["progress"] == 100
    assert last["result"]["success"] is True
    dl = c.get(f"/api/download/{job_id}")
    assert dl.status_code == 200
    assert dl.data[:5] == b"QZIP\x02"
    blob = unpack(dl.data)
    import base64
    assert base64.b64decode(blob["compressed_data"]) == data


def test_async_slow_job_can_be_cancelled(client, monkeypatch):
    c, web, mgr, tmp = client
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "10")
    resp = _upload(c, data=b"x" * 100, name="big.bin", extra={"async": "1"},
                   headers={"X-QZip-Async": "1"})
    assert resp.status_code == 202, resp.get_data(as_text=True)
    job_id = resp.get_json()["job_id"]
    # Wait until the worker is actually running so we test a live cancel.
    deadline = time.time() + 3
    while time.time() < deadline and not mgr.process_alive(job_id):
        time.sleep(0.05)
    pid = mgr.pid(job_id)
    assert mgr.process_alive(job_id)
    cancel = c.post(f"/api/jobs/{job_id}/cancel")
    assert cancel.status_code == 200, cancel.get_data(as_text=True)
    body = cancel.get_json()
    assert body["cancelled"] is True
    assert body["status"] == "cancelled"
    time.sleep(0.3)
    assert not mgr.process_alive(job_id)
    if pid:
        assert not os.path.exists(f"/proc/{pid}")
    pr = c.get(f"/api/progress/{job_id}").get_json()
    assert pr["status"] == "cancelled"
    # Alternate cancel URL exists for already-finished jobs (idempotent).
    again = c.post(f"/api/compress/{job_id}/cancel")
    assert again.status_code == 200


def test_missing_file_is_400(client):
    c, web, mgr, tmp = client
    resp = c.post("/api/compress", data={"method": "fast"}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "file" in resp.get_json()["error"].lower()


def test_cancel_unknown_job_is_404(client):
    c, web, mgr, tmp = client
    resp = c.post("/api/jobs/does-not-exist/cancel")
    assert resp.status_code == 404


def test_sync_timeout_returns_504_and_kills_worker(client, monkeypatch):
    c, web, mgr, tmp = client
    monkeypatch.setenv("QZIP_JOB_TEST_SLEEP", "8")
    monkeypatch.setenv("QZIP_COMPRESS_TIMEOUT", "0.4")
    t0 = time.time()
    resp = _upload(c, data=b"slow-sync", name="slow.txt", extra={"wait": "1"})
    assert time.time() - t0 < 4
    assert resp.status_code == 504, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "timed out" in body["error"].lower()
    job_id = body.get("job_id")
    if job_id:
        time.sleep(0.3)
        assert not mgr.process_alive(job_id)
