"""
Reroll engine for the GUI: N worker threads, each streaming its own log.
Two modes:
  - direct : spray from this machine's IP, keep successes, discard failures (no backoff)
  - proxy  : each worker uses a proxy from the pool (IP rotation); rotates on heavy throttle
"""
import os
import sys
import time
import queue
import threading
import collections

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.dirname(HERE))  # pes_api/
import client as cl
import flows
import accounts


class Engine:
    def __init__(self):
        self.workers = []
        self.running = False
        self.listeners = []           # SSE queues
        self.listeners_lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.stats = collections.Counter()
        self.per_worker = collections.defaultdict(lambda: collections.Counter())
        self.start_ts = 0
        self.cfg = {}

    # ---- log fan-out (to all connected SSE clients) ----
    def emit(self, event: dict):
        with self.listeners_lock:
            for q in self.listeners:
                try:
                    q.put_nowait(event)
                except Exception:
                    pass

    def add_listener(self):
        q = queue.Queue(maxsize=5000)
        with self.listeners_lock:
            self.listeners.append(q)
        return q

    def remove_listener(self, q):
        with self.listeners_lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def log(self, wid, level, text):
        self.emit({"type": "log", "w": wid, "lvl": level, "t": text})

    def push_stats(self):
        dt = max(0.001, time.time() - self.start_ts)
        self.emit({
            "type": "stats",
            "ok": self.stats["ok"], "fail": self.stats["fail"],
            "rate": round(self.stats["ok"] / dt, 2),
            "perday": int(self.stats["ok"] / dt * 86400),
            "elapsed": int(dt),
            "running": self.running,
            "target": self.cfg.get("target", 0),
            "per_worker": {str(w): self.per_worker[w]["ok"] for w in self.per_worker},
        })

    # ---- control ----
    def start(self, mode="direct", workers=15, target=0, proxies=None, do_setup=False, do_claim=True):
        if self.running:
            return False
        proxies = proxies or []
        self.cfg = dict(mode=mode, workers=workers, target=target, do_setup=do_setup,
                        do_claim=do_claim, nproxies=len(proxies))
        self.stats.clear()
        self.per_worker.clear()
        self.running = True
        self.start_ts = time.time()
        self.workers = []
        for i in range(workers):
            t = threading.Thread(target=self._worker, args=(i, mode, proxies, do_setup, do_claim), daemon=True)
            t.start()
            self.workers.append(t)
        threading.Thread(target=self._stats_loop, daemon=True).start()
        threading.Thread(target=self._joiner, daemon=True).start()
        self.emit({"type": "started", "workers": workers, "mode": mode})
        return True

    def _joiner(self):
        for t in list(self.workers):
            t.join()
        if self.running:                    # finished by target, not by STOP
            self.running = False
            self.emit({"type": "stopped"})
            self.push_stats()

    def stop(self):
        self.running = False
        self.emit({"type": "stopped"})

    def _stats_loop(self):
        while self.running:
            self.push_stats()
            time.sleep(1.0)
        self.push_stats()

    def _target_reached(self):
        t = self.cfg.get("target", 0)
        return t and self.stats["ok"] >= t

    # ---- worker ----
    def _worker(self, wid, mode, proxies, do_setup, do_claim=True):
        pidx = wid % max(1, len(proxies)) if proxies else 0
        proxy = proxies[pidx] if (mode == "proxy" and proxies) else None
        c = cl.GateClient(proxy=proxy, max_retries=0)  # spray: no internal retry, we loop fast
        throttle_streak = 0
        self.log(wid, "info", f"worker up  {('proxy=' + proxy) if proxy else 'direct IP'}")
        while self.running and not self._target_reached():
            try:
                r = flows.create_user(c)
                if isinstance(r, dict) and "auth_code" in r:
                    uc, auth = r["user_code"], r["auth_code"]
                    uid = r["pes_user_info"]["user_id"]
                    devid = r.get("_device_identifier")
                    setup = ""
                    if do_setup:
                        try:
                            lr = flows.login(c, uid, auth, devid)
                            sid = lr.get("session_id")
                            if sid:
                                flows.finish_setup(c, uid, sid)
                                setup = " +myclub"
                                if do_claim:
                                    try:
                                        cd = flows.claim_starter(uid, auth, devid, uc)
                                        setup += f" +GP{cd['gp']}"
                                    except Exception:
                                        setup += " (claim fail)"
                        except Exception:
                            setup = " (setup fail)"
                    with self.save_lock:
                        accounts.save(uc, auth, uid, devid)
                    self.stats["ok"] += 1
                    self.per_worker[wid]["ok"] += 1
                    throttle_streak = 0
                    self.log(wid, "ok", f"[+] {uc} created{setup}  (#{self.stats['ok']})")
                else:
                    res = r.get("result", "ERR") if isinstance(r, dict) else "ERR"
                    self.stats["fail"] += 1
                    throttle_streak += 1
                    self.log(wid, "err", f"[!] {res}  gate_CMD_CREATE_USER.php")
            except Exception as e:
                self.stats["fail"] += 1
                throttle_streak += 1
                self.log(wid, "err", f"[x] 500/Server Error  {type(e).__name__}")
            # proxy rotation: if this proxy is clearly dead/throttled, move to the next one
            if mode == "proxy" and proxies and throttle_streak >= 8:
                pidx = (pidx + 1) % len(proxies)
                proxy = proxies[pidx]
                c = cl.GateClient(proxy=proxy, max_retries=0)
                throttle_streak = 0
                self.log(wid, "info", f"rotate -> {proxy}")
        self.log(wid, "info", "worker stopped")


ENGINE = Engine()
