"""/session handshake + heartbeat (L0/L2/L3/L9 client side).

Implements exactly the server contract:
  POST /session {key,hwid,build_id,module_id,client_eph_pub(hex),m(hex)}
    -> verify Ed25519 grant over the returned grant string
    -> X25519-ECDH -> HKDF(info="brain-wrap"||hwid||M) -> AES-GCM unwrap k_brain
    -> decrypt brain.enc (RAM only)
  POST /heartbeat {sid,hwid} every `heartbeat` secs; lease TTL 300s.
"""
from __future__ import annotations

import base64
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import brain as brain_mod
from . import config, crypto_core, integrity
from .hwid import hwid
from .tls_pin import PinnedClient


class SessionError(Exception):
    pass


class UpdateRequired(SessionError):
    """Server returned 426 — the running build is stale (force auto-update)."""


class NoRental(SessionError):
    """Discord has no active rental — the user must rent on the web (402)."""


class AccessDenied(SessionError):
    """Server returned 403 — bad/expired key, seat full, or not entitled."""


def _client() -> PinnedClient:
    return PinnedClient(config.SERVER_HOST, config.SERVER_PORT, config.TLS_SPKI_PIN)


class SessionCtx:
    def __init__(self, client, resp, grant, k_brain, brain, m):
        self._client = client
        self.sid = resp["sid"]
        self.heartbeat = int(resp["heartbeat"])
        self.grant_exp = grant["exp"]
        self.lease_exp = int(resp["lease_exp"])
        self.k_brain = k_brain
        self.brain = brain
        self.m = m
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    def send_heartbeat(self) -> dict | None:
        status, r = self._client.json("POST", "/heartbeat",
                                      {"sid": self.sid, "hwid": hwid()})
        if status != 200:
            raise SessionError(f"heartbeat {status}: {r}")
        return r

    def start_heartbeat(self, on_revoked=None) -> None:
        """Background keepalive. Calls on_revoked() once if the lease is lost."""
        def _loop():
            while not self._hb_stop.wait(self.heartbeat):
                try:
                    r = self.send_heartbeat()
                except Exception:
                    if on_revoked:
                        on_revoked()
                    return
                if r and r.get("revoked"):
                    if on_revoked:
                        on_revoked()
                    return
        self._hb_thread = threading.Thread(target=_loop, daemon=True)
        self._hb_thread.start()

    def stop_heartbeat(self) -> None:
        self._hb_stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=2)


def check_entitlement(discord_token: str) -> dict:
    """Ask the server whether this Discord has an active rental (before the full
    handshake). Returns {has_access, modules, expires_at, ...}. Raises on auth error."""
    cl = _client()
    status, r = cl.json("POST", "/auth/entitlement",
                        {"prod_access_token": discord_token, "hwid": hwid()})
    if status == 403:
        raise AccessDenied((r or {}).get("detail", "discord required"))
    if status != 200 or not isinstance(r, dict):
        raise SessionError(f"/auth/entitlement {status}: {r}")
    return r


def open_session(build_id: str, module_id: str, brain_enc: bytes,
                 discord_token: str | None = None, key: str | None = None) -> SessionCtx:
    """Discord-first: with no key, the server resolves the account's active key
    from the Discord token. `key` stays as a dev/admin override."""
    cl = _client()
    eph = X25519PrivateKey.generate()
    eph_pub_hex = eph.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    m = integrity.measure()

    body = {
        "hwid": hwid(), "build_id": build_id, "module_id": module_id,
        "client_eph_pub": eph_pub_hex, "m": m.hex(),
        "proto": 2,   # per-session key: k_brain never reaches this process (gap-1)
    }
    if key:
        body["key"] = key
    if discord_token:
        body["prod_access_token"] = discord_token  # phase 2: person anchor
    status, r = cl.json("POST", "/session", body)
    if status == 426:
        raise UpdateRequired("build stale — update required")
    if status == 402:
        raise NoRental((r or {}).get("detail", "no active rental"))
    if status == 403:
        raise AccessDenied((r or {}).get("detail", "access denied"))
    if status != 200 or not r:
        raise SessionError(f"/session {status}: {r}")

    grant = crypto_core.verify_grant(config.ED25519_PUB, r["grant"], r["sig"])
    if grant["hwid"] != hwid():
        raise SessionError("grant hwid mismatch")
    if grant["build_id"] != build_id:
        raise SessionError("grant build mismatch")
    if grant["exp"] <= int(time.time()):
        raise SessionError("grant already expired")

    shared = crypto_core.ecdh_shared(eph, r["server_x25519_pub"])
    k_wrap = crypto_core.derive_kwrap(shared, hwid(), m)
    try:
        # proto 2: this is the per-session sk, not the build k_brain (which the
        # server keeps). proto 1: the build k_brain (legacy). Same unwrap either way.
        sess_key = crypto_core.unwrap_k_brain(k_wrap, r["wrapped"])
    except Exception as e:
        # tamper / wrong-M / wrong build: no branch to patch, this is the gate.
        raise SessionError("brain key unwrap failed (tamper or wrong build)") from e
    try:
        bs = r.get("brain_sess")
        if bs:
            # proto 2: brain delivered re-encrypted under sk; ignore the bundle.
            brain = brain_mod.decrypt_brain(sess_key, base64.b64decode(bs))
        else:
            # proto 1: decrypt the statically-bundled brain.enc with the build k_brain.
            brain = brain_mod.decrypt_brain(sess_key, brain_enc)
    except Exception as e:
        raise SessionError("brain decrypt failed") from e

    return SessionCtx(cl, r, grant, sess_key, brain, m)
