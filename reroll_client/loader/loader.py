"""Thin loader — 0 business logic.

login (key+HWID) -> /session handshake -> brain in RAM -> fetch encrypted engine
module -> exec in RAM. The real reroll/gacha/daily logic never ships in the exe;
it is pulled per session and lives only in memory.

Engine module artifact contract (PC side defines it, server just serves bytes):
  builds/<build_id>/module_<module_id>.bin
    = nonce(12) || AES-256-GCM(k_brain).encrypt(nonce, engine_source_utf8, None)
The loader decrypts with the session k_brain and exec()s the source in a fresh
namespace, then calls its `run(brain, session)` entrypoint.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import antidebug
from . import config  # noqa: F401  (kept for symmetry / future embedded brain)
from .session import SessionCtx, open_session

VALID_MODULES = ("reroll", "gacha", "daily")


def _bundled_path(name: str) -> str:
    # Next to the exe (onefile: sys._MEIPASS for bundled read-only data).
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def load_brain_enc(path: str | None = None) -> bytes:
    with open(path or _bundled_path("brain.enc"), "rb") as f:
        return f.read()


def fetch_engine(ctx: SessionCtx, module_id: str) -> str:
    status, blob = ctx._client.request(  # noqa: SLF001 (same package)
        "GET", f"/module/{module_id}?sid={ctx.sid}")
    if status != 200 or not blob:
        raise RuntimeError(f"/module/{module_id} -> {status}")
    nonce, ct = blob[:12], blob[12:]
    src = AESGCM(ctx.k_brain).decrypt(nonce, ct, None)
    return src.decode("utf-8")


def run_module(module_id: str, build_id: str, discord_token: str | None = None,
               key: str | None = None, brain_enc_path: str | None = None,
               exec_engine: bool = True, params: dict | None = None,
               local_engine: str | None = None):
    if module_id not in VALID_MODULES:
        raise ValueError(f"bad module_id {module_id!r}")

    antidebug.enforce()  # L6: refuse under debugger/injector (soft gate, not in M)
    brain_enc = load_brain_enc(brain_enc_path)
    # Discord-first: no key -> server resolves it from the Discord token.
    ctx = open_session(build_id, module_id, brain_enc, discord_token=discord_token, key=key)
    ctx.start_heartbeat(on_revoked=lambda: os._exit(2))
    antidebug.watch(ctx._hb_stop)  # L6: re-check for a debugger ATTACHED mid-run

    if not exec_engine:
        return ctx  # dry-run: handshake proven, caller inspects ctx.brain

    # engine source: pulled from server (default, empty-exe) or a local file (dev)
    if local_engine:
        with open(local_engine, "r", encoding="utf-8") as f:
            src = f.read()
    else:
        src = fetch_engine(ctx, module_id)
    ns: dict = {"__name__": f"engine_{module_id}"}
    exec(compile(src, f"<engine:{module_id}>", "exec"), ns)  # noqa: S102
    entry = ns.get("run")
    if not callable(entry):
        raise RuntimeError("engine module has no run(brain, session, params)")
    try:
        return entry(ctx.brain, ctx, {"module": module_id, **(params or {})})
    finally:
        ctx.stop_heartbeat()
