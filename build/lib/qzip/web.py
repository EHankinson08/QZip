"""Run the drag-and-drop web UI locally: `python -m qzip.web` or `qzip web`.

Starts two local processes bound to 127.0.0.1:
  * API server (webapp/api.py, FastAPI) -> http://127.0.0.1:8000
  * Web UI (webapp/ui.py, Flask)        -> http://127.0.0.1:5000
No API key is needed (local mode). For a hosted deployment set QZIP_REQUIRE_API_KEY=1.
"""
import os, signal, subprocess, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _wait(url, secs, proc):
    end = time.time() + secs
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(url, timeout=2).read(); return True
        except Exception:
            time.sleep(1)
    return False

def main(host=None, port=None, api_port=None):
    host = host or os.getenv("QZIP_WEB_HOST", "127.0.0.1")
    port = int(port or os.getenv("QZIP_WEB_PORT", "5000"))
    api_port = int(api_port or os.getenv("QZIP_API_PORT", "8000"))
    env = dict(os.environ)
    env.setdefault("QZIP_NO_RAY", "1"); env.setdefault("QZIP_LOG_FILE", "")
    env.setdefault("QZIP_REQUIRE_API_KEY", "0")
    env.update(QZIP_API_HOST="127.0.0.1", QZIP_API_PORT=str(api_port),
               QZIP_WEB_HOST=host, QZIP_WEB_PORT=str(port),
               QUANTUM_API_URL=f"http://127.0.0.1:{api_port}")
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    procs = []
    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)   # so `kill <launcher>` also stops both servers
    try:
        api = subprocess.Popen([sys.executable, "-m", "webapp.api"], cwd=ROOT, env=env)
        procs.append(api)
        print(f"[qzip] starting API on 127.0.0.1:{api_port} (a few seconds)...", flush=True)
        if not _wait(f"http://127.0.0.1:{api_port}/health", 300, api):
            print("[qzip] API server failed to start", file=sys.stderr); return 1
        ui = subprocess.Popen([sys.executable, "-m", "webapp.ui"], cwd=ROOT, env=env)
        procs.append(ui)
        if not _wait(f"http://127.0.0.1:{port}/api/config", 120, ui):
            print("[qzip] web UI failed to start", file=sys.stderr); return 1
        print(f"[qzip] ready: open http://{host}:{port}  (drag a file in to compress; drag a .qz in to decompress)", flush=True)
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        for p in procs:
            try: p.wait(10)
            except Exception: p.kill()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="python -m qzip.web")
    ap.add_argument("--host"); ap.add_argument("--port", type=int); ap.add_argument("--api-port", type=int)
    a = ap.parse_args()
    raise SystemExit(main(a.host, a.port, a.api_port))
