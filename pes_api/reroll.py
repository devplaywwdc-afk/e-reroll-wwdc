"""
Pure-API reroll: create N guest accounts via the gate API (no game UI) and save each
as a .dat. Run:  python pes_api/reroll.py 10
"""
import sys
import os
import time
import random

sys.path.insert(0, os.path.dirname(__file__))
import client as cl
import flows
import accounts
import gacha


def reroll(count: int, delay: float = 1.0, claim: bool = True,
           do_gacha: bool = False, wanted=None, gacha_agent: int = 0,
           prefix: str = flows.DEFAULT_PREFIX):
    c = cl.GateClient()
    made = []
    for i in range(count):
        try:
            resp = flows.create_user(c, prefix=prefix)
            if "auth_code" not in resp:
                print(f"[{i+1}/{count}] FAIL: {resp.get('result')}")
                time.sleep(delay)
                continue
            uc = resp["user_code"]
            auth = resp["auth_code"]
            uid = resp["pes_user_info"]["user_id"]
            devid = resp.get("_device_identifier")
            # enter MyClub (pick starter team) so the account is lobby-ready (no tutorial)
            setup = "skipped"
            got = ""
            try:
                lr = flows.login(c, uid, auth, devid)
                sid = lr.get("session_id")
                if sid:
                    er = flows.finish_setup(c, uid, sid)
                    setup = er.get("result", "?")
                    # claim the starter box + first daily (durable; waits out the settle delay)
                    if claim:
                        cd = flows.claim_starter(uid, auth, devid, uc)
                        got = f"  got={len(cd['claimed'])} items  GP={cd['gp']} coin={cd['coins']}"
            except Exception as e:
                setup = f"err:{e}"
            accounts.save(uc, auth, uid, devid)
            # optional: open free gacha packs + filter wanted players into folders
            if do_gacha and setup == "NOERR":
                acct = {"user_code": uc, "user_id": uid, "auth_code": auth, "device_identifier": devid}
                gr = gacha.run_account(acct, wanted or [], agent_id=gacha_agent)
                if gr.get("hits"):
                    got += f"  ★HIT {', '.join(gr['hits'])} -> {gr['folder']}"
                else:
                    got += f"  gacha={gr.get('drawn', 0)}"
            made.append(uc)
            print(f"[{i+1}/{count}] OK  {uc}  uid={uid}  myclub={setup}{got}")
        except Exception as e:
            print(f"[{i+1}/{count}] ERROR: {e}")
        time.sleep(delay + random.random())
    print(f"\ncreated {len(made)}/{count} accounts -> {accounts.ACC_DIR}")
    return made


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("count", nargs="?", type=int, default=1)
    ap.add_argument("--no-claim", action="store_true", help="skip starter GP/daily claim")
    ap.add_argument("--gacha", action="store_true", help="open free gacha packs after create")
    ap.add_argument("--want", default="", help="comma-separated wanted players for gacha filter")
    ap.add_argument("--agent", type=int, default=0, help="only draw this agent_id (0=all free)")
    ap.add_argument("--prefix", default=flows.DEFAULT_PREFIX, help="in-game name prefix (default j3xdr)")
    a = ap.parse_args()
    wanted = [w.strip() for w in a.want.split(",") if w.strip()]
    reroll(a.count, claim=not a.no_claim, do_gacha=a.gacha, wanted=wanted,
           gacha_agent=a.agent, prefix=a.prefix)
