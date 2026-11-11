"""
Daily-login bot: log every saved account in once a day (claims the daily login bonus
server-side) and report. Designed to run forever on a VPS.

  python pes_api/daily.py run            # run one pass now
  python pes_api/daily.py schedule       # loop forever, fire at 09:00 Asia/Bangkok daily
"""
import sys
import os
import time
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))
import client as cl
import flows
import accounts

LOG = os.path.join(os.path.dirname(__file__), "daily.log")
TZ = datetime.timezone(datetime.timedelta(hours=7))  # Asia/Bangkok


def log(msg):
    line = f"{datetime.datetime.now(TZ):%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _mailbox_summary(mb):
    pres = mb.get("present_list", []) if isinstance(mb, dict) else []
    kinds = {}
    for p in pres:
        kinds[p.get("present_type", "?")] = kinds.get(p.get("present_type", "?"), 0) + 1
    return len(pres), kinds


def run_once():
    accs = accounts.load_all()
    log(f"=== daily pass: {len(accs)} accounts ===")
    ok = 0
    for a in accs:
        uc, uid, auth = a["user_code"], a["user_id"], a["auth_code"]
        devid = a.get("device_identifier")
        c = cl.GateClient(user_code=uc)
        try:
            lr = flows.login(c, uid, auth, devid)
            if "session_id" not in lr:
                log(f"  {uc}: LOGIN fail {lr.get('result')}")
                continue
            sid = lr["session_id"]
            # the daily login is registered by the login itself; now claim the mailbox
            mb = flows.get_mailbox(c, uid, sid)
            n, kinds = _mailbox_summary(mb)
            ids = [p["present_id"] for p in mb.get("present_list", [])] if isinstance(mb, dict) else []
            claimed = 0
            if ids:
                rr = flows.recv_present(c, uid, sid, ids)
                if rr.get("result") == "NOERR":
                    claimed = len(rr.get("receive_result_list", []))
                else:
                    log(f"  {uc}: claim failed {rr.get('result')}")
            log(f"  {uc}: login OK  mailbox={n} {kinds}  claimed={claimed}")
            ok += 1
        except Exception as e:
            log(f"  {uc}: ERROR {e}")
        time.sleep(1.0)
    log(f"=== done: {ok}/{len(accs)} logged in ===")
    return ok


def _seconds_until_0900():
    now = datetime.datetime.now(TZ)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def schedule():
    log("scheduler started (fires 09:00 Asia/Bangkok daily)")
    while True:
        secs = _seconds_until_0900()
        log(f"sleeping {secs/3600:.2f}h until next 09:00")
        time.sleep(secs)
        try:
            run_once()
        except Exception as e:
            log(f"pass crashed: {e}")
        time.sleep(60)  # avoid double-fire


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "schedule":
        schedule()
    else:
        run_once()
