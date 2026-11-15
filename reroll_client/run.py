"""Entry point for the 3 exes (one loader, module_id selects behavior).

module_id resolution order:
  1. REROLL_MODULE env   2. exe/script stem (eFootballReroll -> reroll, ...)
  3. --module <id>       4. default "reroll"

build_id + key come from --build / --key or env (REROLL_BUILD / REROLL_KEY) or,
for a shipped build, a bundled `build_id.txt` next to the exe and an interactive
key prompt. Phase-1 = key + HWID (no Discord).
"""
from __future__ import annotations

import argparse
import os
import sys

from loader.loader import VALID_MODULES, run_module


def _module_from_name() -> str | None:
    stem = os.path.splitext(os.path.basename(sys.executable
                            if getattr(sys, "frozen", False) else sys.argv[0]))[0]
    low = stem.lower()
    for m in VALID_MODULES:
        if m in low:
            return m
    return None


def _bundled_build_id() -> str | None:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "build_id.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="eFootball reroll client loader")
    ap.add_argument("--module", choices=VALID_MODULES)
    ap.add_argument("--key")
    ap.add_argument("--build")
    ap.add_argument("--brain", help="path to brain.enc (default: bundled)")
    ap.add_argument("--count", type=int, default=1, help="reroll: how many accounts")
    ap.add_argument("--prefix", help="reroll: in-game name prefix")
    ap.add_argument("--agent", type=int, default=0, help="gacha: only this agent_id")
    ap.add_argument("--local-engine", help="dev: exec this engine source instead of /module")
    ap.add_argument("--no-discord", action="store_true",
                    help="dev only: skip the (mandatory) Discord login")
    ap.add_argument("--dry-run", action="store_true",
                    help="handshake + brain decrypt only, no engine exec")
    args = ap.parse_args()

    module_id = (os.environ.get("REROLL_MODULE") or _module_from_name()
                 or args.module or "reroll")
    build_id = args.build or os.environ.get("REROLL_BUILD") or _bundled_build_id()
    key = args.key or os.environ.get("REROLL_KEY")

    if not build_id:
        print("error: no build_id (pass --build or bundle build_id.txt)", file=sys.stderr)
        return 2

    # Discord-first: Login with Discord ONLY. The server resolves the rental key
    # from the Discord identity; no manual key. A patched client still gets 403/402
    # from the server. `--key` stays as a dev/admin override.
    discord_token = None
    if not args.no_discord:
        from loader import discord_auth
        try:
            info = discord_auth.login()
            discord_token = info.get("prod_access_token")
            print(f"Discord: {info.get('username')} ({info.get('discord_id')})")
        except Exception as e:  # noqa: BLE001
            print(f"Discord login failed: {e}", file=sys.stderr)
            return 1

    # Pre-check entitlement -> bounce to the web shop if no active rental.
    if discord_token and not key:
        from loader import session, config
        try:
            ent = session.check_entitlement(discord_token)
        except Exception as e:  # noqa: BLE001
            print(f"entitlement check failed: {e}", file=sys.stderr)
            return 1
        if not ent.get("has_access"):
            reason = ent.get("reason", "no active rental")
            print(f"ยังไม่มีสิทธิ์ใช้งาน ({reason}) — เปิดหน้าเว็บให้เช่า: {config.SHOP_URL}")
            try:
                import webbrowser
                webbrowser.open(config.SHOP_URL)
            except Exception:  # noqa: BLE001
                pass
            return 3
        exp = ent.get("expires_at")
        print(f"สิทธิ์: {','.join(ent.get('modules', []))}"
              + (f" · หมดอายุ {exp}" if exp else " · ถาวร"))

    params = {"count": args.count, "agent": args.agent}
    if args.prefix:
        params["prefix"] = args.prefix
    try:
        result = run_module(module_id, build_id, discord_token=discord_token, key=key,
                            brain_enc_path=args.brain, exec_engine=not args.dry_run,
                            params=params, local_engine=args.local_engine)
    except Exception as e:  # noqa: BLE001 — top-level: show one clean line
        from loader.session import NoRental
        if isinstance(e, NoRental):
            from loader import config
            print(f"ยังไม่มีสิทธิ์ใช้งาน — เปิดหน้าเว็บให้เช่า: {config.SHOP_URL}")
            try:
                import webbrowser
                webbrowser.open(config.SHOP_URL)
            except Exception:  # noqa: BLE001
                pass
            return 3
        print(f"[{module_id}] FAILED: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        ctx = result
        print(f"[{module_id}] handshake OK — sid={ctx.sid} "
              f"brain_keys={sorted(ctx.brain)} heartbeat={ctx.heartbeat}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
