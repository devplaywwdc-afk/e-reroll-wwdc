"""
Pure-API browser GUI server for the E-WWDC reroll/gacha/daily/genlink/merge/check engine.

Mirrors the main app (E-WWDC v1.0.18) feature-for-feature, but:
  - transport is http + SSE (opens in a browser, no pywebview),
  - no Discord login / license / entitlement / HWID / forced-update (owner's own tool),
  - the engine's constants come from engine_brain.BRAIN (local), not a released brain.

The browser calls POST /api/<fn> with a JSON array of args; it receives server events
on GET /events (SSE), delivered as batched JSON arrays into window._evb().
"""
import os
import sys
import json
import time
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PES = os.path.dirname(HERE)
sys.path.insert(0, PES)          # import engine_core / engine_brain / injectmod

import engine_core as E
import engine_brain
import flows                     # server_status (standalone probe)
try:
    import injectmod
except Exception:                # adb tooling optional
    injectmod = None

BRAIN = engine_brain.BRAIN
APP_VERSION = "1.0.18"

# ---------------------------------------------------------------- SSE event bus
_listeners = []                  # list[queue.Queue]  (one per open /events connection)
_lis_lock = threading.Lock()


def emit(ev):
    """Fan one event out to every open SSE connection."""
    with _lis_lock:
        for q in list(_listeners):
            try:
                q.put_nowait(ev)
            except Exception:
                pass


def _add_listener():
    q = queue.Queue(maxsize=20000)
    with _lis_lock:
        _listeners.append(q)
    return q


def _drop_listener(q):
    with _lis_lock:
        if q in _listeners:
            _listeners.remove(q)


# ---------------------------------------------------------------- run control
_run_lock = threading.Lock()
_stop_event = None
_worker = None
_run_state = {"running": False, "module": "", "workers": 0}   # for grid rebuild on reconnect


class _StdoutPush:
    """Capture engine print() lines → applog events (daily/gacha/merge log boxes)."""
    def __init__(self):
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                emit({"type": "applog", "lvl": "info", "t": line})

    def flush(self):
        if self._buf.strip():
            emit({"type": "applog", "lvl": "info", "t": self._buf})
        self._buf = ""


def _run_engine(params):
    global _stop_event
    stop = threading.Event()
    _stop_event = stop
    params["stop"] = stop
    params["emit"] = emit
    params["progress"] = lambda ok, fail, total: emit(
        {"type": "progress", "ok": ok, "fail": fail, "total": total})
    _run_state.update({"running": True,
                       "module": params.get("module") or "reroll",
                       "workers": int(params.get("workers") or 0)})
    old = sys.stdout
    sys.stdout = _StdoutPush()
    try:
        E.run(BRAIN, None, params)
    except Exception as e:
        emit({"type": "runerr", "t": str(e), "raw": repr(e)})
    finally:
        try:
            sys.stdout.flush()
        except Exception:
            pass
        sys.stdout = old
        _run_state["running"] = False
        emit({"type": "stopped"})


def start_run(params):
    """Launch a reroll/daily/gacha/genlink/check run in the background (one job at a time)."""
    global _worker
    with _run_lock:
        if _worker and _worker.is_alive():
            return {"error": "กำลังทำงานอยู่ รอให้เสร็จก่อน"}
        _worker = threading.Thread(target=_run_engine, args=(params,), daemon=True)
        _worker.start()
    return {"ok": True}


def stop_run():
    if _stop_event:
        _stop_event.set()
        emit({"type": "applog", "lvl": "info", "t": "กำลังหยุด…"})
    return {"ok": True}


# ---------------------------------------------------------------- blocking actions
def do_scan(out_dir=None):
    """Gacha pack scan (needs one saved account to log in with)."""
    old = sys.stdout
    sys.stdout = _StdoutPush()
    try:
        res = E.run(BRAIN, None, {"action": "scan", "out_dir": out_dir, "src_dir": out_dir})
        return {"packs": (res or {}).get("packs", [])}
    except Exception as e:
        return {"error": str(e)}
    finally:
        sys.stdout = old


def do_merge(params):
    old = sys.stdout
    sys.stdout = _StdoutPush()
    try:
        return E.run(BRAIN, None, {**params, "action": "merge", "emit": emit})
    except Exception as e:
        return {"error": str(e)}
    finally:
        sys.stdout = old


# ---------------------------------------------------------------- api dispatch
def api_call(fn, args):
    a = list(args or [])

    if fn == "app_version":
        return {"version": APP_VERSION}
    if fn == "changelog":
        return {"current": APP_VERSION, "releases": []}
    if fn == "server_status":
        try:
            return flows.server_status()
        except Exception as e:
            return {"status": "down", "error": str(e)}

    if fn == "scan_packs":
        return do_scan(a[0] if a else None)

    if fn == "start":
        try:
            p = json.loads(a[0]) if a and isinstance(a[0], str) else (a[0] if a else {})
        except Exception:
            p = {}
        p = dict(p or {})
        if p.get("action") == "merge":
            return do_merge(p)
        return start_run(p)        # reroll / daily / gacha / genlink / check (all emit live)
    if fn == "stop":
        return stop_run()

    if fn == "inject_list":
        if not injectmod:
            return {"error": "injectmod ไม่พร้อม (adb tooling)"}
        try:
            return {"devices": injectmod.list_devices()}
        except Exception as e:
            return {"error": str(e)}
    if fn == "inject_push":
        if not injectmod:
            return {"error": "injectmod ไม่พร้อม (adb tooling)"}
        device = a[0] if len(a) > 0 else None
        if not device:
            return {"error": "เลือกอุปกรณ์ก่อน"}
        try:
            acct = json.loads(a[1]) if len(a) > 1 and isinstance(a[1], str) else (a[1] if len(a) > 1 else {})
        except Exception as e:
            return {"error": f"บัญชีไม่ถูกต้อง: {e}"}
        if not (acct.get("user_code") and acct.get("auth_code") and acct.get("user_id") is not None):
            return {"error": "บัญชีต้องมี user_code + auth_code + user_id"}
        opts = {}
        if len(a) > 2 and a[2]:
            try:
                opts = json.loads(a[2]) if isinstance(a[2], str) else dict(a[2])
            except Exception:
                opts = {}
        try:
            return injectmod.inject(device, acct,
                                    backup=opts.get("backup", True),
                                    clear_system=opts.get("clear_system", True),
                                    launch=opts.get("launch", True))
        except Exception as e:
            return {"error": str(e)}
    if fn == "read_account_file":
        path = a[0] if a else None
        try:
            with open(path, encoding="utf-8") as f:
                return {"account": json.load(f)}
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"unknown api {fn!r}"}


# ---------------------------------------------------------------- http handler
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, str(e), "text/plain; charset=utf-8")
            return
        if path == "/logo.png":
            p = os.path.join(HERE, "logo.png")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
                self._send(404, b"", "image/png")
            return
        if path == "/events":
            self._sse()
            return
        self._send(404, {"error": "not found"})

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = _add_listener()
        # reconnect/reload: if a reroll run is live, replay 'started' so the grid rebuilds
        if _run_state.get("running") and _run_state.get("module") == "reroll":
            try:
                q.put_nowait({"type": "started", "workers": _run_state.get("workers", 0)})
            except Exception:
                pass
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            while True:
                batch = []
                try:
                    batch.append(q.get(timeout=15))      # block, or heartbeat on timeout
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                for _ in range(500):                     # drain the tick, send one array
                    try:
                        batch.append(q.get_nowait())
                    except queue.Empty:
                        break
                payload = json.dumps(batch, ensure_ascii=False)
                self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
                self.wfile.flush()
                time.sleep(0.08)
        except Exception:
            pass
        finally:
            _drop_listener(q)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send(404, {"error": "not found"})
            return
        fn = path[len("/api/"):]
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"[]"
        try:
            args = json.loads(raw.decode("utf-8") or "[]")
            if not isinstance(args, list):
                args = [args]
        except Exception:
            args = []
        try:
            res = api_call(fn, args)
        except Exception as e:
            res = {"error": str(e)}
        self._send(200, json.dumps(res, ensure_ascii=False).encode("utf-8"))


def main(port=8777):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"E-WWDC pure-API GUI  -  http://127.0.0.1:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8777
    main(p)
