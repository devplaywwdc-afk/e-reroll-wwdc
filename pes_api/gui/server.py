"""
Local web dashboard for the reroll engine (no external deps — stdlib only).
Run:  python pes_api/gui/server.py        (PORT from env, default 8080)
Then open http://localhost:<PORT>
"""
import os
import sys
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from engine import ENGINE
import flows
import client as cl
import accounts

PORT = int(os.environ.get("PORT") or os.environ.get("HOST_PORT") or 8080)
INDEX = os.path.join(HERE, "index.html")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default logging
        pass

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if body:
            self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            with open(INDEX, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path.split("?")[0].lstrip("/") in ("logo.png", "logo.jpg", "logo.webp"):
            name = self.path.split("?")[0].lstrip("/")
            fp = os.path.join(HERE, name)
            if os.path.exists(fp):
                ctype = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[name.split(".")[-1]]
                with open(fp, "rb") as f:
                    self._send(200, f.read(), ctype)
            else:
                self._send(404, b"")
        elif self.path == "/events":
            self._sse()
        elif self.path == "/api/status":
            self._send(200, json.dumps({"running": ENGINE.running, "cfg": ENGINE.cfg,
                                        "ok": ENGINE.stats["ok"], "fail": ENGINE.stats["fail"]}))
        elif self.path == "/api/accounts":
            accs = [a["user_code"] for a in accounts.load_all()]
            self._send(200, json.dumps({"accounts": accs, "count": len(accs)}))
        elif self.path.startswith("/api/link"):
            from urllib.parse import urlparse, parse_qs
            uc = (parse_qs(urlparse(self.path).query).get("user_code") or [""])[0]
            self._send(200, json.dumps(self._konami_link(uc), ensure_ascii=False))
        else:
            self._send(404, b"{}")

    def _link_for(self, a):
        """Fetch a KONAMI link for an uploaded account dict (may not be in local accounts/)."""
        uc = a.get("user_code", "?")
        try:
            uid = int(a["user_id"])
            auth = a["auth_code"]
        except Exception:
            return {"user_code": uc, "error": "bad account file (need user_id + auth_code)"}
        devid = a.get("device_identifier")  # optional; login works without the original device
        try:
            c = cl.GateClient(user_code=uc)
            sid = flows.login(c, uid, auth, devid).get("session_id")
            if not sid:
                return {"user_code": uc, "error": "login failed (bad auth_code?)"}
            r = flows.get_konami_link(c, uid, sid)
            r["user_code"] = uc
            return r
        except Exception as e:
            return {"user_code": uc, "error": f"{type(e).__name__}: {e}"}

    def _konami_link(self, user_code):
        a = next((x for x in accounts.load_all() if x["user_code"] == user_code), None)
        if not a:
            return {"error": "account not found"}
        try:
            c = cl.GateClient(user_code=user_code)
            sid = flows.login(c, a["user_id"], a["auth_code"], a.get("device_identifier")).get("session_id")
            if not sid:
                return {"error": "login failed"}
            return flows.get_konami_link(c, a["user_id"], sid)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(ln) if ln else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            data = {}
        if self.path == "/api/genlink":
            accs = data.get("accounts") or []
            out = []
            for a in accs:
                out.append(self._link_for(a))
            self._send(200, json.dumps({"results": out}, ensure_ascii=False))
            return
        if self.path == "/api/start":
            proxies = [p.strip() for p in (data.get("proxies") or "").splitlines()
                       if p.strip() and not p.strip().startswith("#")]
            ok = ENGINE.start(
                mode=data.get("mode", "direct"),
                workers=int(data.get("workers", 15)),
                target=int(data.get("target", 0)),
                proxies=proxies,
                do_setup=bool(data.get("setup", False)),
            )
            self._send(200, json.dumps({"ok": ok, "workers": int(data.get("workers", 15))}))
        elif self.path == "/api/stop":
            ENGINE.stop()
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, b"{}")

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = ENGINE.add_listener()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                except Exception:
                    self.wfile.write(b": ping\n\n")  # keepalive
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            ENGINE.remove_listener(q)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"[+] Reroll dashboard: http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
