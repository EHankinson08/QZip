"""Background compression jobs for the local web app.

The Flask UI used to proxy /api/compress to the API server and wait under a
hard, size-based timeout. Large files (enwik9) returned HTTP 504 while the
API process kept working, leaving an orphaned job.

Jobs now run in a child process so they can be cancelled with SIGTERM, then
SIGKILL. Uploads live on disk. There is no hard timeout unless
QZIP_COMPRESS_TIMEOUT is set to a positive number of seconds (0, the default,
means off). Clients may poll /api/progress/<job_id> and POST
/api/jobs/<job_id>/cancel. A missing heartbeat after QZIP_JOB_HEARTBEAT_SEC
(default 90, 0 = off) is treated as a disconnect and the worker is killed.
"""
from __future__ import annotations

import atexit
import base64
import gzip
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

TERMINAL = frozenset({"complete", "error", "cancelled", "timeout"})


def _env_float(name, default):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


def compress_timeout_sec():
    """Seconds to wait in sync (wait=1) mode. 0 or negative = no timeout."""
    return _env_float("QZIP_COMPRESS_TIMEOUT", 0)


def heartbeat_sec():
    """Seconds without a progress poll before an async job is cancelled. 0 = off."""
    return _env_float("QZIP_JOB_HEARTBEAT_SEC", 90)


def _atomic_write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    tmp.replace(path)


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rm(path):
    if not path:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("could not remove %s: %s", path, e)


def format_file_size(size_bytes):
    if size_bytes == 0:
        return "0 B"
    size_names = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(size_bytes)
    while size >= 1024 and i < len(size_names) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {size_names[i]}"


def build_qc_gz(container, dest_path):
    """Write the `.qz` download (same bytes as `qzip compress`; see qzip/container.py)."""
    from qzip.container import pack
    gz = pack(container)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(gz)
    return len(gz)


def _real_compress(input_path, mode):
    from qzip.core import compress_bytes
    data = Path(input_path).read_bytes()
    return compress_bytes(data, mode=mode)


def _test_compress(input_path, mode):
    """Identity container used by the regression suite (QZIP_JOB_MODE=test)."""
    delay = _env_float("QZIP_JOB_TEST_SLEEP", 0)
    deadline = time.time() + delay
    while time.time() < deadline:
        time.sleep(min(0.05, max(0.0, deadline - time.time())))
    data = Path(input_path).read_bytes()
    container = {
        "compressed_data": base64.b64encode(data).decode("ascii"),
        "reconstruction_metadata": None,
        "method": "test",
        "quantum_parameters": {"qzip": {"mode": mode, "chosen": "test", "detected_type": "test", "tried": ["test"]}},
        "verified": True,
    }
    info = {"payload": max(1, len(data)), "ratio": 1.0, "chosen": "test", "tried": ["test"]}
    return container, info


def _compress_fn():
    if os.environ.get("QZIP_JOB_MODE", "").strip().lower() == "test":
        return _test_compress
    return _real_compress


class JobCancelled(Exception):
    pass


def _install_worker_signals():
    def _die(signum, _frame):
        raise JobCancelled(f"signal {signum}")
    signal.signal(signal.SIGTERM, _die)
    signal.signal(signal.SIGINT, _die)


def worker_main(argv=None):
    """Entry point for `python -m qzip.jobs --worker ...`."""
    import argparse
    p = argparse.ArgumentParser(prog="qzip.jobs")
    p.add_argument("--worker", action="store_true", required=True)
    p.add_argument("--job-id", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--result", required=True)
    p.add_argument("--mode", default="auto")
    p.add_argument("--filename", default="upload")
    args = p.parse_args(argv)

    _install_worker_signals()
    started = time.time()
    original_size = 0
    try:
        original_size = os.path.getsize(args.input) if os.path.exists(args.input) else 0
        _atomic_write(args.status, {
            "job_id": args.job_id,
            "status": "compressing",
            "progress": 10,
            "filename": args.filename,
            "original_size": original_size,
            "start_time": started,
            "elapsed_time": 0,
            "message": f"Compressing {args.filename}...",
        })
        container, info = _compress_fn()(args.input, args.mode)
        payload = max(1, int(info.get("payload") or 1))
        ratio = (original_size / payload) if original_size else 1.0
        elapsed = time.time() - started
        _atomic_write(args.status, {
            "job_id": args.job_id,
            "status": "finalizing",
            "progress": 90,
            "filename": args.filename,
            "original_size": original_size,
            "start_time": started,
            "elapsed_time": round(elapsed, 3),
            "message": "Finalizing...",
        })
        gz_size = build_qc_gz(container, args.output)
        elapsed = time.time() - started
        qp = (container.get("quantum_parameters") or {}).get("qzip") or {}
        result = {
            "success": True,
            "job_id": args.job_id,
            "original_filename": args.filename,
            "original_size": original_size,
            "original_size_formatted": format_file_size(original_size),
            "compressed_size": payload,
            "compressed_size_formatted": format_file_size(payload),
            "qc_gz_size": gz_size,
            "compression_ratio": ratio,
            "compression_time": round(elapsed, 3),
            "compression_speed_mbps": round((original_size / (1024 * 1024)) / elapsed, 2) if elapsed > 0 else 0,
            "method": container.get("method") or "hybrid_quantum",
            "space_saved_percent": round((1 - 1 / ratio) * 100, 2) if ratio else 0,
            "download_url": f"/api/download/{args.job_id}",
            "perfect_reconstruction": True,
            "detected_type": qp.get("detected_type"),
            "route_method": qp.get("chosen") or info.get("chosen"),
            "mode": qp.get("mode") or args.mode,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "output_path": args.output,
        }
        _atomic_write(args.result, result)
        _atomic_write(args.status, {
            "job_id": args.job_id,
            "status": "complete",
            "progress": 100,
            "filename": args.filename,
            "original_size": original_size,
            "start_time": started,
            "elapsed_time": round(elapsed, 3),
            "speed_mbps": result["compression_speed_mbps"],
            "message": "Compression complete",
            "result": result,
        })
        _rm(args.input)
        return 0
    except JobCancelled:
        _rm(args.input)
        _rm(args.output)
        _atomic_write(args.status, {
            "job_id": args.job_id,
            "status": "cancelled",
            "progress": 0,
            "filename": args.filename,
            "original_size": original_size,
            "start_time": started,
            "elapsed_time": round(time.time() - started, 3),
            "message": "Cancelled",
            "error": "Cancelled",
        })
        return 143
    except Exception as e:
        logger.exception("worker failed")
        _rm(args.input)
        _rm(args.output)
        _atomic_write(args.status, {
            "job_id": args.job_id,
            "status": "error",
            "progress": 0,
            "filename": args.filename,
            "original_size": original_size,
            "start_time": started,
            "elapsed_time": round(time.time() - started, 3),
            "message": str(e),
            "error": f"Compression failed: {e}",
        })
        return 1


def _kill_process(proc, grace=5):
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        logger.error("worker pid %s did not exit after SIGKILL", getattr(proc, "pid", None))


class JobManager:
    def __init__(self, upload_dir=None, download_dir=None, jobs_dir=None):
        self.upload_dir = Path(upload_dir or os.getenv("QZIP_UPLOAD_DIR", "uploads"))
        self.download_dir = Path(download_dir or os.getenv("QZIP_DOWNLOAD_DIR", "downloads"))
        self.jobs_dir = Path(jobs_dir or os.getenv("QZIP_JOBS_DIR", "storage/jobs"))
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._jobs = {}
        self._lock = threading.RLock()
        self._reaper_stop = threading.Event()
        self._reaper = threading.Thread(target=self._reaper_loop, name="qzip-job-reaper", daemon=True)
        self._reaper.start()
        atexit.register(self.shutdown)

    def _record(self, job_id):
        return self._jobs.get(job_id)

    def start(self, input_path, filename, mode="auto", heartbeat_required=True, job_id=None):
        job_id = job_id or uuid.uuid4().hex
        status_path = self.jobs_dir / f"{job_id}.status.json"
        result_path = self.jobs_dir / f"{job_id}.result.json"
        output_path = self.download_dir / f"{job_id}.qz"
        original_size = os.path.getsize(input_path) if os.path.exists(input_path) else 0
        estimated = (original_size / (1024 * 1024)) / 0.85 if original_size else 0
        now = time.time()
        initial = {
            "job_id": job_id,
            "status": "starting",
            "progress": 0,
            "filename": filename,
            "original_size": original_size,
            "estimated_time": estimated,
            "elapsed_time": 0,
            "speed_mbps": 0,
            "start_time": now,
            "message": f"Starting compression of {filename}...",
        }
        _atomic_write(status_path, initial)
        env = os.environ.copy()
        env.setdefault("QZIP_NO_RAY", "1")
        env.setdefault("QZIP_LOG_FILE", "")
        cmd = [
            sys.executable, "-m", "qzip.jobs",
            "--worker",
            "--job-id", job_id,
            "--input", str(input_path),
            "--output", str(output_path),
            "--status", str(status_path),
            "--result", str(result_path),
            "--mode", mode or "auto",
            "--filename", filename,
        ]
        log_path = self.jobs_dir / f"{job_id}.worker.log"
        rec_log = str(log_path)
        log_f = open(log_path, "wb")
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
        )
        log_f.close()

        rec = {
            "job_id": job_id,
            "filename": filename,
            "mode": mode or "auto",
            "input_path": str(input_path),
            "output_path": str(output_path),
            "status_path": str(status_path),
            "result_path": str(result_path),
            "original_size": original_size,
            "estimated_time": estimated,
            "start_time": now,
            "proc": proc,
            "pid": proc.pid,
            "worker_log": str(self.jobs_dir / f"{job_id}.worker.log"),
            "heartbeat_required": bool(heartbeat_required),
            "last_poll": now,
            "terminal": False,
        }
        with self._lock:
            self._jobs[job_id] = rec
        return job_id

    def _refresh(self, rec):
        status = _read_json(rec["status_path"]) or {}
        proc = rec["proc"]
        alive = proc is not None and proc.poll() is None
        if not alive and not rec["terminal"]:
            code = None if proc is None else proc.poll()
            if status.get("status") not in TERMINAL:
                err = "Compression worker exited unexpectedly"
                logp = rec.get("worker_log")
                if logp and Path(logp).exists():
                    try:
                        tail = Path(logp).read_bytes()[-500:]
                        if tail:
                            err = err + ": " + tail.decode("utf-8", "replace")
                    except Exception:
                        pass
                status = {
                    "job_id": rec["job_id"],
                    "status": "cancelled" if code in (143, -signal.SIGTERM, -signal.SIGKILL) else "error",
                    "progress": 0,
                    "filename": rec["filename"],
                    "original_size": rec["original_size"],
                    "start_time": rec["start_time"],
                    "elapsed_time": round(time.time() - rec["start_time"], 3),
                    "error": "Cancelled" if code in (143, -signal.SIGTERM, -signal.SIGKILL) else err,
                    "message": "Cancelled" if code in (143, -signal.SIGTERM, -signal.SIGKILL) else err,
                }
                _atomic_write(rec["status_path"], status)
            rec["terminal"] = True
            _rm(rec.get("input_path"))
        if status.get("status") in TERMINAL:
            rec["terminal"] = True
        # Always stamp live elapsed time for in-flight jobs.
        if status.get("status") not in TERMINAL:
            status["elapsed_time"] = round(time.time() - rec["start_time"], 3)
            estimated = rec.get("estimated_time") or status.get("estimated_time") or 0
            status["estimated_time"] = estimated
            if estimated and status["elapsed_time"] > 0:
                remaining = max(0.0, estimated - status["elapsed_time"])
                status["time_remaining"] = round(remaining, 1)
                time_progress = min(85, int((status["elapsed_time"] / estimated) * 85))
                status["progress"] = max(int(status.get("progress") or 0), time_progress)
            if status["elapsed_time"] > 0 and rec["original_size"]:
                status["speed_mbps"] = round((rec["original_size"] / (1024 * 1024)) / status["elapsed_time"], 2)
        elif not status.get("result"):
            result = _read_json(rec["result_path"])
            if result:
                status["result"] = result
        status.setdefault("job_id", rec["job_id"])
        status.setdefault("pid", rec.get("pid"))
        status.setdefault("download_url", f"/api/download/{rec['job_id']}")
        return status

    def status(self, job_id, heartbeat=True):
        with self._lock:
            rec = self._record(job_id)
            if rec is None:
                return None
            if heartbeat:
                rec["last_poll"] = time.time()
            return self._refresh(rec)

    def result(self, job_id):
        st = self.status(job_id, heartbeat=False)
        if not st:
            return None
        return st.get("result") or _read_json(self._jobs[job_id]["result_path"])

    def wait(self, job_id, timeout=None):
        """Block until the job finishes. timeout is seconds; 0/None = no limit.

        Returns the final status dict. Raises TimeoutError if the wait expires
        (the job is then cancelled).
        """
        if timeout is None:
            timeout = compress_timeout_sec()
        timeout = float(timeout or 0)
        deadline = (time.time() + timeout) if timeout > 0 else None
        while True:
            st = self.status(job_id, heartbeat=False)
            if st is None:
                raise KeyError(job_id)
            if st.get("status") in TERMINAL:
                return st
            if deadline is not None and time.time() >= deadline:
                self.cancel(job_id, reason="timeout")
                st = self.status(job_id, heartbeat=False) or {}
                st["status"] = "timeout"
                st["error"] = f"Compression timed out after {timeout:.1f}s"
                rec = self._record(job_id)
                if rec:
                    _atomic_write(rec["status_path"], st)
                raise TimeoutError(st["error"])
            time.sleep(0.05)

    def cancel(self, job_id, reason="cancelled"):
        with self._lock:
            rec = self._record(job_id)
            if rec is None:
                return None
            st = self._refresh(rec)
            if rec["terminal"] or st.get("status") in TERMINAL:
                return st
            _kill_process(rec["proc"])
            rec["terminal"] = True
            _rm(rec.get("input_path"))
            _rm(rec.get("output_path"))
            final = {
                "job_id": job_id,
                "status": "timeout" if reason == "timeout" else "cancelled",
                "progress": 0,
                "filename": rec["filename"],
                "original_size": rec["original_size"],
                "start_time": rec["start_time"],
                "elapsed_time": round(time.time() - rec["start_time"], 3),
                "message": "Timed out" if reason == "timeout" else "Cancelled",
                "error": "Compression timed out" if reason == "timeout" else "Cancelled",
            }
            _atomic_write(rec["status_path"], final)
            return final

    def pid(self, job_id):
        rec = self._record(job_id)
        if not rec:
            return None
        return rec.get("pid")

    def process_alive(self, job_id):
        rec = self._record(job_id)
        if not rec or rec["proc"] is None:
            return False
        return rec["proc"].poll() is None

    def input_path(self, job_id):
        rec = self._record(job_id)
        return rec.get("input_path") if rec else None

    def output_path(self, job_id):
        rec = self._record(job_id)
        return rec.get("output_path") if rec else None

    def reap(self):
        """Cancel async jobs whose clients have gone silent, and close finished processes."""
        now = time.time()
        hb = heartbeat_sec()
        doomed = []
        with self._lock:
            for job_id, rec in list(self._jobs.items()):
                self._refresh(rec)
                if rec["terminal"]:
                    continue
                if rec["heartbeat_required"] and hb > 0 and (now - rec["last_poll"]) > hb:
                    doomed.append(job_id)
        for job_id in doomed:
            logger.info("cancelling job %s after missed heartbeat", job_id)
            self.cancel(job_id, reason="disconnect")

    def _reaper_loop(self):
        while not self._reaper_stop.wait(1.0):
            try:
                self.reap()
            except Exception:
                logger.exception("job reaper failed")

    def shutdown(self):
        if getattr(self, "_closed", False):
            return
        self._closed = True
        self._reaper_stop.set()
        with self._lock:
            ids = [job_id for job_id, rec in self._jobs.items() if not rec["terminal"]]
        for job_id in ids:
            try:
                self.cancel(job_id, reason="shutdown")
            except Exception:
                logger.exception("shutdown cancel failed for %s", job_id)


_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def get_manager(**kwargs):
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = JobManager(**kwargs)
        return _MANAGER


def reset_manager(**kwargs):
    """Replace the process-wide manager (used by tests)."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is not None:
            try:
                _MANAGER.shutdown()
            except Exception:
                pass
        _MANAGER = JobManager(**kwargs)
        return _MANAGER


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--worker":
        return worker_main(argv)
    print("usage: python -m qzip.jobs --worker ...", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
