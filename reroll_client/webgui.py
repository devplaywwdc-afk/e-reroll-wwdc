"""eFootball Reroll — web dashboard shell (pywebview).

A native window (Edge WebView2 on Windows) loads webui/index.html and talks to
the SAME licensed loader as the Tkinter GUI: Discord login -> entitlement ->
brain-in-RAM run_module. No HTTP server, no port: the HTML calls Python through
`window.pywebview.api.*`, and the engine pushes live events back via
`window.evaluate_js(window._ev(...))`. The engine still ships encrypted + runs
in RAM — this file is only the shell.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import webbrowser

import webview

from loader import config
from loader.loader import VALID_MODULES, run_module


def _bundled_build_id() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, "build_id.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return os.environ.get("REROLL_BUILD", "")


def _asset_dir() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(base, "webui"), base):
        if os.path.exists(os.path.join(cand, "index.html")):
            return cand
    return os.path.join(base, "webui")


def _lvl(line: str) -> str:
    low = line.lower()
    if "error" in low or "fail" in low or "] err" in low or "ผิดพลาด" in line:
        return "err"
    if "] ok " in low or " ok " in low or "สำเร็จ" in line:
        return "ok"
    return "info"


class _StdoutPush:
    """Redirect engine print() to the dashboard log (line-buffered)."""
    def __init__(self, api):
        self.api = api
        self.buf = ""

    def write(self, s):
        if not s:
            return
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                # applog (not w0) so the reroll worker grid stays fed only by the
                # engine's per-worker events; daily/gacha/merge show it in their log
                self.api._push({"type": "applog", "lvl": _lvl(line), "t": line})

    def flush(self):
        pass


class Api:
    def __init__(self):
        self._window = None   # underscore: pywebview must NOT expose/serialize it
                              # (it wraps the native WebView2 control -> infinite
                              # property recursion -> the window freezes)
        self.token = None
        self.name = None
        self.build_id = _bundled_build_id()
        self.stop_event = None
        self.worker = None

    # ---- push an event to the page (any thread) ----
    def _push(self, ev):
        w = self._window
        if not w:
            return
        try:
            w.evaluate_js("window._ev(" + json.dumps(ev, ensure_ascii=False) + ")")
        except Exception:
            pass

    # ---- small helpers the page calls ----
    def logo_data(self):
        for p in (os.path.join(_asset_dir(), "logo.png"),
                  os.path.join(getattr(sys, "_MEIPASS",
                               os.path.dirname(os.path.abspath(__file__))), "logo.png")):
            try:
                with open(p, "rb") as f:
                    return {"uri": "data:image/png;base64," + base64.b64encode(f.read()).decode()}
            except OSError:
                continue
        return {}

    def open_shop(self):
        webbrowser.open(config.SHOP_URL)
        return {"ok": True}

    def open_tg(self):
        webbrowser.open("https://t.me/j3xdr")
        return {"ok": True}

    def pick_folder(self):
        try:
            res = self._window.create_file_dialog(webview.FOLDER_DIALOG)
            if res:
                return {"path": res[0] if isinstance(res, (list, tuple)) else res}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        return {}

    def server_status(self):
        return {"status": "unknown"}   # v1: real gate probe is a fast-follow

    def app_version(self):
        return {"version": config.APP_VERSION}

    def check_update(self):
        """Soft update check vs the latest GitHub release tag (notice only)."""
        try:
            from loader import updater
            return updater.check_latest()
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "newer": False, "current": config.APP_VERSION}

    def open_url(self, url):
        try:
            if url:
                webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True}

    # ---- auth ----
    def login(self):
        try:
            from loader import discord_auth
            info = discord_auth.login(
                on_url=lambda u: self._push({"type": "authurl", "t": u}))
            self.token = info.get("prod_access_token")
            self.name = info.get("username")
            return {"ok": True, "username": self.name}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def entitlement(self):
        if not self.token:
            return {"has_access": False, "reason": "ยังไม่ได้เข้าสู่ระบบ"}
        try:
            from loader import session
            return session.check_entitlement(self.token)
        except Exception as e:  # noqa: BLE001
            return {"has_access": False, "reason": str(e)}

    # ---- gacha scan (pack picker) ----
    def scan_packs(self, out_dir=None):
        if not self.token:
            return {"error": "เข้าสู่ระบบก่อน"}
        old = sys.stdout
        sys.stdout = _StdoutPush(self)   # engine print() must NOT hit the frozen
        try:                             # exe's cp874 stdout (crashes on names like Brasileirão)
            res = run_module("gacha", self.build_id, discord_token=self.token,
                             params={"action": "scan", "out_dir": out_dir, "src_dir": out_dir})
            return {"packs": (res or {}).get("packs", [])}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        finally:
            sys.stdout = old

    # ---- run control ----
    def stop(self):
        if self.stop_event:
            self.stop_event.set()
            self._push({"type": "applog", "lvl": "info", "t": "กำลังหยุด…"})
        return {"ok": True}

    def start(self, params_json):
        if self.worker and self.worker.is_alive():
            return {"error": "กำลังทำงานอยู่ รอให้เสร็จก่อน"}
        if not self.token:
            return {"error": "เข้าสู่ระบบก่อน"}
        try:
            p = json.loads(params_json) if isinstance(params_json, str) else dict(params_json or {})
        except Exception:
            p = {}
        module = p.get("module") or "reroll"
        # fresh entitlement re-check at run start (anti-patched-client)
        try:
            from loader import session
            ent = session.check_entitlement(self.token)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        if not ent.get("has_access"):
            return {"error": "ไม่มีสิทธิ์ใช้งาน — ไปเช่าที่เว็บก่อน"}
        mods = ent.get("modules") or list(VALID_MODULES)
        gate = module if module in mods else mods[0]
        if p.get("action") in ("merge", "check") and gate not in VALID_MODULES:
            gate = "reroll"
        self.stop_event = threading.Event()
        p["stop"] = self.stop_event
        p["emit"] = self._push
        p["progress"] = lambda ok, fail, total: self._push(
            {"type": "progress", "ok": ok, "fail": fail, "total": total})

        def work():
            old = sys.stdout
            sys.stdout = _StdoutPush(self)
            try:
                run_module(gate, self.build_id, discord_token=self.token, params=p)
            except Exception as e:  # noqa: BLE001
                self._push({"type": "applog", "lvl": "err", "t": "[ผิดพลาด] " + str(e)})
            finally:
                sys.stdout = old
                self._push({"type": "done", "module": module})
                self._push({"type": "stopped"})

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        return {"ok": True}


def main():
    for s in (sys.stdout, sys.stderr):   # never crash on non-cp874 chars (ã, Thai, ∞)
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    api = Api()
    idx = os.path.join(_asset_dir(), "index.html")
    win = webview.create_window(
        "eFootball Reroll", url=idx, js_api=api,
        width=1180, height=772, min_size=(980, 648), background_color="#050508")
    api._window = win
    webview.start()


if __name__ == "__main__":
    main()
