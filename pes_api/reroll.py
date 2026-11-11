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


def reroll(count: int, delay: float = 1.0):
    c = cl.GateClient()
    made = []
    for i in range(count):
        try:
            resp = flows.create_user(c)
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
            try:
                lr = flows.login(c, uid, auth, devid)
                sid = lr.get("session_id")
                if sid:
                    er = flows.finish_setup(c, uid, sid)
                    setup = er.get("result", "?")
            except Exception as e:
                setup = f"err:{e}"
            accounts.save(uc, auth, uid, devid)
            made.append(uc)
            print(f"[{i+1}/{count}] OK  {uc}  uid={uid}  myclub={setup}")
        except Exception as e:
            print(f"[{i+1}/{count}] ERROR: {e}")
        time.sleep(delay + random.random())
    print(f"\ncreated {len(made)}/{count} accounts -> {accounts.ACC_DIR}")
    return made


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    reroll(n)
