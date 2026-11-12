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


def _capture_code(state: str, timeout: float) -> str:
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

    srv = http.server.HTTPServer(("127.0.0.1", FIXED_PORT), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        webbrowser.open(_authorize_url(state))
        print("opened Discord login in your browser…", flush=True)
        deadline = time.time() + timeout
        while "code" not in holder and time.time() < deadline:
            time.sleep(0.25)
    finally:
        srv.shutdown()
        srv.server_close()
    if "code" not in holder:
        raise DiscordAuthError("Discord login timed out")
    return holder["code"]


def login(timeout: float = 180.0) -> dict:
    """Run the full flow. Returns {prod_access_token, discord_id, username}."""
    if not config.DISCORD_CLIENT_ID:
        raise DiscordAuthError("this build has no DISCORD_CLIENT_ID embedded")
    state = secrets.token_urlsafe(16)
    code = _capture_code(state, timeout)
    cl = PinnedClient(config.SERVER_HOST, config.SERVER_PORT, config.TLS_SPKI_PIN)
    status, r = cl.json("POST", "/auth/discord/exchange",
                        {"code": code, "redirect_uri": REDIRECT_URI, "hwid": hwid()})
    if status != 200 or not r:
        raise DiscordAuthError(f"exchange failed {status}: {r}")
    return r
