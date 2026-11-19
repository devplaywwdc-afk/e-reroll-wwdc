"""
Daily-login engine for the GUI: login every account + claim the daily reward, show what
each got + live currencies (GP / eFootball Coins / eFootball Points).

- Source: accounts in the saved folder, or an uploaded list.
- Reward resets at 09:00 Asia/Bangkok daily (not a 24h timer). If running, it fires a pass at
  every 09:00. On open it also does a catch-up pass (claims whatever the server already put in
  the mailbox since the last reset).
- Failed accounts re-queue to the BACK of the queue; retried up to `max_retries` (default 3).
  After that they are parked as failed (the GUI offers to delete them from the folder).
"""
import os
import sys
import time
import queue
import threading
import datetime
import collections

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))
import client as cl
import flows
import accounts

TZ = datetime.timezone(datetime.timedelta(hours=7))  # Asia/Bangkok


class DailyEngine:
    def __init__(self):
        self.listeners = []
        self.lock = threading.Lock()
        self.running = False
        self.scheduler_on = False
        self.results = {}          # user_code -> result dict
        self.failed = []           # user_codes that exhausted retries
        self.cfg = {}
        self._stop = False

    # ---- SSE fan-out ----
    def emit(self, ev):
        with self.lock:
            for q in self.listeners:
                try: q.put_nowait(ev)
                except Exception: pass

    def add_listener(self):
        q = queue.Queue(maxsize=5000)
        with self.lock: self.listeners.append(q)
        return q

    def remove_listener(self, q):
        with self.lock:
            if q in self.listeners: self.listeners.remove(q)

    def _result(self, r):
        self.results[r["user_code"]] = r
        self.emit({"type": "daily", **r})

    def push_summary(self):
        done = sum(1 for r in self.results.values() if r["state"] == "ok")
        fail = len(self.failed)
        self.emit({"type": "dsum", "total": self.cfg.get("total", 0), "done": done,
                   "failed": fail, "running": self.running,
                   "scheduler": self.scheduler_on, "next": self.cfg.get("next_run", "")})

    # ---- one account ----
    def _process(self, acct):
        uc = acct["user_code"]
        c = cl.GateClient(user_code=uc)
        lr = flows.login(c, int(acct["user_id"]), acct["auth_code"], acct.get("device_identifier"))
        sid = lr.get("session_id")
        if not sid:
            raise RuntimeError(f"login: {lr.get('result','?')}")
        # play the Daily Game (sugoroku) first so its reward is in the mailbox before we claim
        sug = None
        if self.cfg.get("do_sugoroku"):
            try:
                sug = flows.play_sugoroku(c, int(acct["user_id"]), sid)
            except Exception as e:
                sug = {"played": False, "reason": str(e)}
        cd = flows.claim_daily(c, int(acct["user_id"]), sid)
        if cd.get("result") != "NOERR":
            raise RuntimeError(f"claim: {cd.get('result')}")
        st = flows.account_status(c, int(acct["user_id"]), sid)
        return {
            "user_code": uc, "state": "ok", "attempts": acct.get("_try", 1),
            "had_bonus": cd["had_login_bonus"],
            "rewards": cd["claimed"],
            "sugoroku": sug,
            "gp": st["gp"], "coins": st["coins"], "efp": st["efp"],
            "ts": datetime.datetime.now(TZ).strftime("%H:%M:%S"),
        }

    # ---- a full pass over the queue (with retry re-queue) ----
    def run_pass(self, source_accounts, workers, max_retries):
        q = collections.deque()
        for a in source_accounts:
            a = dict(a); a["_try"] = 0
            q.append(a)
        self.cfg["total"] = len(q)
        self.failed = []
        qlock = threading.Lock()

        def worker():
            while not self._stop:
                with qlock:
                    if not q: return
                    a = q.popleft()
                a["_try"] += 1
                uc = a["user_code"]
                self._result({"user_code": uc, "state": "run", "attempts": a["_try"]})
                try:
                    self._result(self._process(a))
                except Exception as e:
                    if a["_try"] < max_retries:
                        self._result({"user_code": uc, "state": "retry", "attempts": a["_try"],
                                      "error": str(e)})
                        with qlock: q.append(a)        # re-queue to the BACK
                    else:
                        self.failed.append(uc)
                        self._result({"user_code": uc, "state": "failed", "attempts": a["_try"],
                                      "error": str(e)})
                self.push_summary()
                time.sleep(0.15)

        ths = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
        [t.start() for t in ths]; [t.join() for t in ths]
        self.push_summary()

    # ---- control ----
    def start(self, source="folder", uploaded=None, workers=10, max_retries=3, schedule=False,
              do_sugoroku=False):
        if self.running: return False
        self.results = {}; self.failed = []; self._stop = False
        self.running = True; self.scheduler_on = schedule
        self.cfg = {"source": source, "workers": workers, "max_retries": max_retries,
                    "do_sugoroku": do_sugoroku}
        accs = uploaded if (source == "upload" and uploaded) else accounts.load_all()
        threading.Thread(target=self._runner, args=(accs, workers, max_retries, schedule), daemon=True).start()
        self.emit({"type": "dstarted", "total": len(accs), "schedule": schedule})
        return True

    def _runner(self, accs, workers, max_retries, schedule):
        # immediate catch-up pass
        self.run_pass(accs, workers, max_retries)
        if not schedule:
            self.running = False
            self.emit({"type": "dstopped"})
            return
        # then wait for each 09:00 Bangkok
        while not self._stop:
            secs = _secs_until_0900()
            self.cfg["next_run"] = (datetime.datetime.now(TZ) + datetime.timedelta(seconds=secs)).strftime("%d/%m %H:%M")
            self.push_summary()
            end = time.time() + secs
            while time.time() < end and not self._stop:
                time.sleep(2)
            if self._stop: break
            fresh = accounts.load_all() if self.cfg["source"] == "folder" else accs
            self.run_pass(fresh, workers, max_retries)
        self.running = False
        self.emit({"type": "dstopped"})

    def stop(self):
        self._stop = True
        self.running = False
        self.emit({"type": "dstopped"})

    def delete_failed(self):
        """Delete the parked-failed accounts from the folder; returns deleted user_codes."""
        deleted = []
        for uc in list(self.failed):
            p = os.path.join(accounts.ACC_DIR, f"{uc}.txt")
            try:
                if os.path.exists(p): os.remove(p); deleted.append(uc)
            except Exception: pass
        self.failed = [u for u in self.failed if u not in deleted]
        self.emit({"type": "deleted", "codes": deleted})
        return deleted


def _secs_until_0900():
    now = datetime.datetime.now(TZ)
    t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if t <= now: t += datetime.timedelta(days=1)
    return (t - now).total_seconds()


DAILY = DailyEngine()
