"""Discord OAuth2 login (L0 person anchor, phase 2) — desktop loopback flow.

The exe opens the browser to Discord's authorize page with a FIXED loopback
redirect (http://127.0.0.1:53123/cb). Discord redirects back with a code; we
hand that code to OUR server (/auth/discord/exchange), which holds the client
secret, exchanges it, reads the Discord identity, and returns a short-lived
Ed25519 prod_access_token. The client never sees the client secret.

The exe embeds only the PUBLIC client_id (config.DISCORD_CLIENT_ID). The Discord
app must register the exact redirect URI below.
"""
from __future__ import annotations

import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser

from . import config
from .hwid import hwid
from .tls_pin import PinnedClient

FIXED_PORT = 53123
REDIRECT_URI = f"http://127.0.0.1:{FIXED_PORT}/cb"


class DiscordAuthError(Exception):
    pass


def _authorize_url(state: str) -> str:
    q = urllib.parse.urlencode({
        "client_id": config.DISCORD_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "consent",
    })
    return "https://discord.com/oauth2/authorize?" + q


class _ReuseServer(http.server.HTTPServer):
    # let a re-login grab the fixed port again right after the previous one closed
    allow_reuse_address = True


# the single in-flight loopback server, so a fresh login attempt (user switched
# browser / the first click hung) can cancel the previous one and re-bind.
_active: dict = {"srv": None}


def cancel() -> None:
    """Shut the currently-waiting loopback server, if any (lets the user click
    Login again to retry without the port staying stuck)."""
    srv = _active.get("srv")
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
        _active["srv"] = None


def _capture_code(state: str, timeout: float, on_url=None) -> str:
    holder: dict[str, str] = {}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            p = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(p.query)
            if p.path == "/cb" and q.get("state", [""])[0] == state and "code" in q:
                holder["code"] = q["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h3>Login complete. You can close this tab.</h3>")
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *a):  # silence
            pass

    cancel()  # drop any previous in-flight attempt before binding the port
    srv = _ReuseServer(("127.0.0.1", FIXED_PORT), _H)
    _active["srv"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = _authorize_url(state)
    try:
        if on_url:
            try:
                on_url(url)
            except Exception:
                pass
        try:
            webbrowser.open(url)
        except Exception:
            pass
        print("opened Discord login in your browser…", flush=True)
        deadline = time.time() + timeout
        # stop early if cancel() swapped the active server (user clicked again)
        while ("code" not in holder and time.time() < deadline
               and _active.get("srv") is srv):
            time.sleep(0.25)
    finally:
        if _active.get("srv") is srv:
            srv.shutdown()
            srv.server_close()
            _active["srv"] = None
    if "code" not in holder:
        raise DiscordAuthError("Discord login timed out")
    return holder["code"]


def login(timeout: float = 180.0, on_url=None) -> dict:
    """Run the full flow. Returns {prod_access_token, discord_id, username}.
    `on_url(url)` (optional) receives the authorize URL so the GUI can show it —
    the user can paste it into the right browser if the auto-open hit the wrong one."""
    if not config.DISCORD_CLIENT_ID:
        raise DiscordAuthError("this build has no DISCORD_CLIENT_ID embedded")
    state = secrets.token_urlsafe(16)
    code = _capture_code(state, timeout, on_url=on_url)
    cl = PinnedClient(config.SERVER_HOST, config.SERVER_PORT, config.TLS_SPKI_PIN)
    status, r = cl.json("POST", "/auth/discord/exchange",
                        {"code": code, "redirect_uri": REDIRECT_URI, "hwid": hwid()})
    if status != 200 or not r:
        raise DiscordAuthError(f"exchange failed {status}: {r}")
    return r
