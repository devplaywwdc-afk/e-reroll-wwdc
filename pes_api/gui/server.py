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
import queue
import threading
from engine import ENGINE
from daily_engine import DAILY
import flows
import client as cl
import accounts
import efootbase
import gacha

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
        elif self.path.startswith("/api/roster"):
            from urllib.parse import urlparse, parse_qs
            uc = (parse_qs(urlparse(self.path).query).get("user_code") or [""])[0]
            a = next((x for x in accounts.load_all() if x["user_code"] == uc), None)
            self._send(200, json.dumps(self._account_report(a), ensure_ascii=False))
        else:
            self._send(404, b"{}")

    def _account_report(self, a):
        """Full report for an account: currencies + enriched player roster."""
        if not a:
            return {"error": "account not found"}
        uc = a.get("user_code", "?")
        try:
            c = cl.GateClient(user_code=uc)
            lr = flows.login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
            sid = lr.get("session_id")
            if not sid:
                return {"user_code": uc, "error": "login failed"}
            st = flows.account_status(c, int(a["user_id"]), sid)
            roster = flows.get_roster(lr)
            info = efootbase.lookup_many([p["id"] for p in roster])
            players = []
            for p in roster:
                e = info.get(str(p["id"]), {})
                players.append({**p, "name": e.get("name"), "overall": e.get("overall"),
                                "position": e.get("position"), "card": e.get("card"), "art": e.get("art")})
            players.sort(key=lambda x: (x.get("overall") or 0), reverse=True)
            return {"user_code": uc, "gp": st["gp"], "coins": st["coins"], "efp": st["efp"],
                    "player_count": len(players), "players": players}
        except Exception as e:
            return {"user_code": uc, "error": f"{type(e).__name__}: {e}"}

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
        if self.path == "/api/check":
            accs = data.get("accounts") or accounts.load_all()
            report = self._account_report

            def run_check(accs):
                import concurrent.futures as cf
                total = len(accs)
                ENGINE.emit({"type": "cstart", "total": total})
                done = 0
                with cf.ThreadPoolExecutor(max_workers=5) as ex:
                    futs = {ex.submit(report, a): a for a in accs}
                    for fu in cf.as_completed(futs):
                        try:
                            rep = fu.result()
                        except Exception as e:
                            rep = {"user_code": futs[fu].get("user_code", "?"), "error": str(e)}
                        done += 1
                        ENGINE.emit({"type": "crow", "done": done, "total": total, "rep": rep})
                ENGINE.emit({"type": "cdone", "total": total})

            threading.Thread(target=run_check, args=(accs,), daemon=True).start()
            self._send(200, json.dumps({"ok": True, "total": len(accs)})); return
        if self.path == "/api/gacha/scan":
            accs = data.get("accounts") or accounts.load_all()
            if not accs:
                self._send(200, json.dumps({"error": "no accounts"})); return
            a = accs[0]
            try:
                c = cl.GateClient(user_code=a.get("user_code"))
                lr = flows.login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
                sid = lr.get("session_id")
                if not sid:
                    self._send(200, json.dumps({"error": "login failed"})); return
                sc = gacha.scan_agents(c, int(a["user_id"]), sid)
                sc["sample"] = a.get("user_code")
                self._send(200, json.dumps(sc, ensure_ascii=False))
            except Exception as e:
                self._send(200, json.dumps({"error": f"{type(e).__name__}: {e}"}))
            return
        if self.path == "/api/gacha/run":
            accs = data.get("accounts") or accounts.load_all()
            wanted = [w.strip() for w in (data.get("wanted") or []) if w.strip()]
            agent_id = int(data.get("gacha_agent", 0) or 0)

            def run_gacha(accs, wanted):
                total = len(accs)
                ENGINE.emit({"type": "gstart", "total": total, "wanted": wanted})
                done = 0
                for a in accs:
                    rep = gacha.run_account(a, wanted, agent_id=agent_id)
                    done += 1
                    ENGINE.emit({"type": "grow", "done": done, "total": total, "rep": rep})
                ENGINE.emit({"type": "gdone", "total": total})

            threading.Thread(target=run_gacha, args=(accs, wanted), daemon=True).start()
            self._send(200, json.dumps({"ok": True, "total": len(accs)})); return
        if self.path == "/api/daily/start":
            up = data.get("accounts")  # optional uploaded accounts
            ok = DAILY.start(source=data.get("source", "folder"), uploaded=up,
                             workers=int(data.get("workers", 10)),
                             max_retries=int(data.get("max_retries", 3)),
                             schedule=bool(data.get("schedule", False)))
            self._send(200, json.dumps({"ok": ok})); return
        if self.path == "/api/daily/stop":
            DAILY.stop(); self._send(200, json.dumps({"ok": True})); return
        if self.path == "/api/daily/delete-failed":
            self._send(200, json.dumps({"deleted": DAILY.delete_failed()})); return
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
                do_claim=bool(data.get("claim", True)),
                do_gacha=bool(data.get("gacha", False)),
                gacha_agent=int(data.get("gacha_agent", 0) or 0),
                wanted=[w.strip() for w in (data.get("wanted") or []) if w.strip()],
                name_prefix=(data.get("prefix") or "").strip() or None,
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
        q = queue.Queue(maxsize=8000)
        with ENGINE.listeners_lock:
            ENGINE.listeners.append(q)
        with DAILY.lock:
            DAILY.listeners.append(q)
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
            DAILY.remove_listener(q)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"[+] Reroll dashboard: http://localhost:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
