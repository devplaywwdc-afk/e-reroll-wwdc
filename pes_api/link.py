"""
Get the KONAMI ID account-link URL for a rerolled account (pure API, no game UI).
The link is short-lived; this prints a live countdown and lets you refresh.

  python pes_api/link.py ASCV342175879        # by user_code
  python pes_api/link.py ASCV342175879 --watch # keep a live countdown, auto-refresh on expiry
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(__file__))
import client as cl
import flows
import accounts


def _find(user_code):
    for a in accounts.load_all():
        if a["user_code"] == user_code:
            return a
    raise SystemExit(f"account {user_code} not found in accounts/")


def fetch(a):
    c = cl.GateClient(user_code=a["user_code"])
    sid = flows.login(c, a["user_id"], a["auth_code"], a.get("device_identifier")).get("session_id")
    if not sid:
        raise SystemExit("login failed")
    return flows.get_konami_link(c, a["user_id"], sid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("user_code")
    ap.add_argument("--watch", action="store_true", help="live countdown, auto-refresh on expiry")
    args = ap.parse_args()
    a = _find(args.user_code)

    link = fetch(a)
    if link["result"] != "NOERR":
        raise SystemExit(f"error: {link}")
    print(f"\naccount : {a['user_code']}  (status: {link['link_status']})")
    print(f"LINK    : {link['url']}")

    if not args.watch:
        left = link["expires"] - int(time.time())
        print(f"expires : in {left//60}m {left%60}s")
        return

    while True:
        left = link["expires"] - int(time.time())
        if left <= 0:
            print("\n[expired] refreshing...")
            link = fetch(a)
            print(f"LINK    : {link['url']}")
            continue
        print(f"\r  valid for {left//60:02d}:{left%60:02d}  ", end="", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
