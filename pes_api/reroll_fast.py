"""
Parallel reroll with an adaptive rate governor + optional multi-IP (proxy) pool.

The gate server soft-throttles CREATE_USER per source IP (ERR_TEMP_UNAVAILABLE). This
driver:
  * runs N worker threads,
  * shares an AIMD governor that backs off on throttle and speeds up on success,
  * spreads workers over a proxy/IP pool if given (one machine, one IP otherwise).

To hit ~1M/day you need many source IPs (a VPS fleet, or a proxy list here). From a
single IP the server caps you — the governor just keeps that single IP at its safe max.

Usage:
  python pes_api/reroll_fast.py 500                 # 500 accts, default 4 workers, 1 IP
  python pes_api/reroll_fast.py 500 --workers 8
  python pes_api/reroll_fast.py 500 --no-setup      # create-only (2x faster, no MyClub)
  python pes_api/reroll_fast.py 500 --proxies proxies.txt   # one proxy per line (IP rotation)
"""
import sys
import os
import time
import threading
import argparse

sys.path.insert(0, os.path.dirname(__file__))
import client as cl
import flows
import accounts


class Governor:
    """AIMD pacing shared by all workers: additive speed-up, multiplicative back-off."""
    def __init__(self, start=0.3, lo=0.0, hi=15.0):
        self.delay = start
        self.lo, self.hi = lo, hi
        self.lock = threading.Lock()
        self.ok = 0
        self.throttled = 0

    def on_success(self):
        with self.lock:
            self.ok += 1
            self.delay = max(self.lo, self.delay * 0.90)

    def on_throttle(self, *_):
        with self.lock:
            self.throttled += 1
            self.delay = min(self.hi, self.delay * 2.0 + 0.3)

    def wait(self):
        with self.lock:
            d = self.delay
        if d > 0:
            time.sleep(d)


class Counter:
    def __init__(self, total):
        self.total = total
        self.made = 0
        self.fail = 0
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            if self.made + self.fail >= self.total:
                return False
            return True

    def win(self):
        with self.lock:
            self.made += 1
            return self.made

    def lose(self):
        with self.lock:
            self.fail += 1


def worker(wid, gov: Governor, cnt: Counter, proxy, do_setup, lock):
    c = cl.GateClient(proxy=proxy)
    while cnt.take():
        try:
            resp = flows.create_user(c)  # already retries soft throttles internally
            # feed governor from the *result* too (first-level pacing)
            if "auth_code" not in resp:
                if resp.get("result") in cl.RETRYABLE:
                    gov.on_throttle()
                cnt.lose()
                gov.wait()
                continue
            uc, auth = resp["user_code"], resp["auth_code"]
            uid = resp["pes_user_info"]["user_id"]
            devid = resp.get("_device_identifier")
            if do_setup:
                lr = flows.login(c, uid, auth, devid)
                sid = lr.get("session_id")
                if sid:
                    flows.finish_setup(c, uid, sid)
            with lock:
                accounts.save(uc, auth, uid, devid)
            n = cnt.win()
            gov.on_success()
            print(f"[{n}/{cnt.total}] w{wid} {uc}  (delay={gov.delay:.2f}s ok={gov.ok} thr={gov.throttled})")
        except Exception as e:
            cnt.lose()
            gov.on_throttle()
            print(f"[w{wid}] ERROR {type(e).__name__}: {e}")
        gov.wait()


def reroll_fast(count, workers=4, proxies=None, do_setup=True):
    gov = Governor()
    cnt = Counter(count)
    save_lock = threading.Lock()
    proxies = proxies or [None]
    ths = []
    for i in range(workers):
        px = proxies[i % len(proxies)]
        t = threading.Thread(target=worker, args=(i, gov, cnt, px, do_setup, save_lock))
        t.start()
        ths.append(t)
    t0 = time.time()
    for t in ths:
        t.join()
    dt = time.time() - t0
    rate = cnt.made / dt if dt else 0
    print(f"\ncreated {cnt.made}/{count} in {dt:.0f}s  ->  {rate:.2f}/s  ({rate*86400:,.0f}/day)"
          f"  [throttled {gov.throttled}, final delay {gov.delay:.2f}s]")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("count", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-setup", action="store_true", help="create-only, skip MyClub entry")
    ap.add_argument("--proxies", help="file with one proxy URL per line (http://host:port)")
    a = ap.parse_args()
    px = None
    if a.proxies:
        px = [l.strip() for l in open(a.proxies) if l.strip() and not l.startswith("#")]
        print(f"loaded {len(px)} proxies")
    reroll_fast(a.count, workers=a.workers, proxies=px, do_setup=not a.no_setup)
