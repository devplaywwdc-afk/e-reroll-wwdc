"""Self-contained reroll/gacha/daily engine — the "logic pulled from server".

This whole file is the encrypted engine module artifact. It ships NO secrets:
every key, hash, keyword and template is read from `brain` (released per session,
decrypted in RAM). The loader decrypts this source with k_brain, exec()s it, and
calls `run(brain, session, params)`.

Mirrors pes_api/{gate_crypto,client,flows,gacha,daily,accounts}.py with all
constants externalized to `brain`.
"""
from __future__ import annotations

import base64
import hmac as _hmac
import hashlib
import json
import os
import random
import re
import string
import struct
import sys
import time
import zlib

import msgpack
import requests
from Crypto.Cipher import AES

# ---------------------------------------------------------------- brain-backed crypto
_B: dict = {}
_LOG = None          # run()'s logger (print → GUI queue)
_VERBOSE = False     # raw per-request logging (GUI "Log ดิบ" switch)
_STOP = None         # threading.Event set by the GUI "หยุด" button
_PROGRESS = None     # GUI live-summary callback: progress(ok, fail, total)
_EMIT = None         # dashboard event sink: emit({"type": ..., ...}) → SSE


def _emit(msg):
    if _LOG:
        _LOG(msg)
    else:
        print(msg, flush=True)


def _report(ok, fail, total):
    if _PROGRESS:
        try:
            _PROGRESS(ok, fail, total)
        except Exception:
            pass


def _event(ev):
    """Push a structured event to the web dashboard (worker grid / tables)."""
    if _EMIT:
        try:
            _EMIT(ev)
        except Exception:
            pass


def _stopped() -> bool:
    return bool(_STOP is not None and _STOP.is_set())


def _isleep(secs):
    """Sleep up to `secs`, but wake immediately on stop (so a big delay doesn't
    make the หยุด button feel dead)."""
    try:
        secs = float(secs or 0)
    except (TypeError, ValueError):
        return
    end = time.time() + secs
    while secs > 0 and not _stopped() and time.time() < end:
        time.sleep(max(0.0, min(0.2, end - time.time())))   # clamp: the remaining time can race to <0


def _aes_key() -> bytes:
    return bytes.fromhex(_B["aes_key"])


def _sign_seed() -> bytes:
    s = _B["sign_seed"]
    return s.encode() if isinstance(s, str) else s


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n]) * n


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        return data
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return data


def encrypt_body(payload: dict, iv: bytes | None = None) -> bytes:
    raw = msgpack.packb(payload, use_bin_type=False)
    comp = zlib.compress(raw, 1)   # level 1: the gate ignores ratio, speed > size
    iv = iv or os.urandom(16)
    ct = AES.new(_aes_key(), AES.MODE_CBC, iv).encrypt(_pkcs7_pad(comp))
    return iv + ct


def _decompress(comp: bytes) -> bytes:
    if comp[:2] == b"\x1f\x8b":
        return zlib.decompress(comp, 31)
    return zlib.decompress(comp)


def decrypt_body(body: bytes) -> dict:
    iv, ct = body[:16], body[16:]
    comp = _pkcs7_unpad(AES.new(_aes_key(), AES.MODE_CBC, iv).decrypt(ct))
    raw = _decompress(comp)
    return msgpack.unpackb(raw, raw=False, strict_map_key=False)


def make_sign(body: bytes, v5: int | None = None, v10: int | None = None) -> str:
    if v5 is None:
        v5 = struct.unpack("<I", os.urandom(4))[0]
    if v10 is None:
        v10 = struct.unpack("<I", os.urandom(4))[0]
    num = (v5 ^ v10) & 0xFFFFFFFF
    hkey = _sign_seed() + b"|" + str(num).encode()
    digest = _hmac.new(hkey, body, hashlib.sha256).digest()
    blob = digest + struct.pack("<I", v5) + struct.pack("<I", v10)
    return base64.b64encode(blob).decode()


# ---------------------------------------------------------------- gate client
RETRYABLE = {"ERR_TEMP_UNAVAILABLE", "ERR_BUSY", "ERR_MAINTENANCE"}
# game-server down / maintenance → not a client bug: stop the whole job, don't retry
MAINTENANCE = {"ERR_SERVERNOTREADY", "ERR_MAINTENANCE"}
DEFAULT_UA = "PES/1.0 (ANDROID 14; {user_code}; SM-A526B; {ts}; 6.1.1; ; 11.1.1; )"
_MAINT_HIT = False


def _maint_stop(res):
    """Konami gate is in maintenance / down. Show a plain message once and stop
    every worker via the shared stop Event (so the job ends immediately)."""
    global _MAINT_HIT
    if _MAINT_HIT:
        return
    _MAINT_HIT = True
    if _LOG:
        _LOG("")
        _LOG("เซิฟเกมกำลังปิดปรับปรุง — หยุดงานอัตโนมัติ รอเซิฟเปิดแล้วค่อยลองใหม่ "
             "(ไม่ใช่ปัญหาโปรแกรม/คีย์ · " + str(res) + ")")
    if _STOP is not None:
        try:
            _STOP.set()
        except Exception:
            pass


class GateError(Exception):
    pass


def new_rqid() -> int:
    return random.getrandbits(32)


class GateClient:
    def __init__(self, user_code="0", timeout=20, session=None,
                 max_retries=4, backoff=1.5, proxy=None):
        self.user_code = user_code
        self.timeout = timeout
        self.http = session or requests.Session()
        if proxy:
            self.http.proxies = {"http": proxy, "https": proxy}
        self.proxy = proxy
        self.max_retries = max_retries
        self.backoff = backoff

    def _headers(self, user_code, sign):
        ts = random.randint(1_700_000_000, 1_999_999_999)
        return {
            "Accept-Language": "jp",
            "User-Agent": DEFAULT_UA.format(user_code=user_code, ts=ts),
            "pes-custom-encrypt": "AES256",
            "Accept-Encoding": "gzip",
            "Content-Encoding": "gzip",
            "Cookie": f"sign={sign};",
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "Keep-Alive",
        }

    def send(self, cmd, payload, user_code=None, on_throttle=None):
        url = _B["gate_url"] % (_B["gate_host"], cmd)
        last = None
        for attempt in range(self.max_retries + 1):
            body = encrypt_body(payload)
            sign = make_sign(body)
            headers = self._headers(user_code or self.user_code, sign)
            try:
                r = self.http.post(url, data=body, headers=headers, timeout=self.timeout)
                last = self._parse(cmd, r)
                if _VERBOSE:
                    rc = last.get("result") if isinstance(last, dict) else "?"
                    _emit(f"[{r.status_code}] {cmd} -> {rc}")
            except requests.RequestException as e:
                last = {"result": "ERR_NETWORK", "_exc": type(e).__name__}
                if _VERBOSE:
                    _emit(f"[net] {cmd} -> ERR_NETWORK ({type(e).__name__})")
            res = last.get("result") if isinstance(last, dict) else None
            if res in MAINTENANCE:
                _maint_stop(res)
                return last
            if res in RETRYABLE or res == "ERR_NETWORK":
                if on_throttle:
                    on_throttle(attempt)
                if attempt < self.max_retries:
                    time.sleep((self.backoff ** attempt) * (0.5 + random.random()))
                    continue
            return last
        return last

    def _parse(self, cmd, r):
        raw = r.content
        try:
            return decrypt_body(raw)
        except Exception:
            pass
        import gzip
        data = raw
        try:
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return json.loads(data.decode("utf-8", "replace"))
        except Exception:
            return {"_status": r.status_code, "_raw_hex": raw[:64].hex(), "_len": len(raw)}


class Governor:
    """AIMD rate pacing shared by all reroll workers: additive-ish speed-up,
    multiplicative back-off. Keeps ONE source IP at its safe max — the server
    soft-throttles CREATE_USER per IP (ERR_TEMP_UNAVAILABLE)."""
    def __init__(self, start=0.3, lo=0.0, hi=6.0):
        import threading as _t
        self.delay = max(float(lo), float(start))
        self.lo, self.hi = float(lo), float(hi)
        self.lock = _t.Lock()
        self.ok = 0
        self.throttled = 0

    def on_success(self):
        with self.lock:
            self.ok += 1
            self.delay = max(self.lo, self.delay * 0.85)   # recover a touch faster

    def on_throttle(self):
        with self.lock:
            self.throttled += 1
            self.delay = min(self.hi, self.delay * 1.7 + 0.2)

    def wait(self):
        if _stopped():
            return
        with self.lock:
            d = self.delay
        if d > 0:
            # ±50% jitter so 64 workers don't all sleep-then-fire in lockstep
            # (that lockstep is what made the counter freeze then jump in bursts)
            d *= (0.5 + random.random())
        _isleep(d)


# ---------------------------------------------------------------- flows
MAX_NAME = 15


def rand_name(prefix=None):
    prefix = (prefix or _B.get("default_prefix", "j3xdr")).strip()[:MAX_NAME - 2]
    tail = MAX_NAME - len(prefix) - 1
    return f"{prefix}_" + "".join(random.choices(string.ascii_letters, k=max(1, tail)))


def rand_device():
    model, gpu, soc = random.choice(_B["device_profiles"])
    return {
        "device_identifier": os.urandom(32).hex(),
        "model_name": model, "gpu_name": gpu, "soc_name": soc,
        "os_version": random.choice(["13", "14", "15"]),
        "phy_mem_used_mib": random.randint(800, 1400),
        "phy_mem_available_mib": random.randint(2500, 5000),
    }


def _commons(cmd, user_id, session_id, hash_val):
    return {
        "msgid": cmd, "rqid": new_rqid(), "user_id": user_id,
        "session_id": session_id, "my_platform": "ANDROID",
        "s_keyword": _B["s_keyword"], "hash": hash_val,
        "client_version": _B.get("gate_client_version", "6.1.1"),
        "platform": "ANDROID",
        "device": {"device_identifier": _B["device_id"]},
    }


def create_user(client, name=None, device=None, prefix=None, check_name=False):
    name = name or rand_name(prefix)
    dev = device or rand_device()
    # CMD_CHECK_STRING (name validation) is NOT required to create a usable account —
    # verified: accounts created without it still log in and claim daily/mailbox. Off by
    # default → 1 gate request/account instead of 2 (random names rarely collide; a bad
    # name just fails CREATE_USER and the worker retries). The per-IP throttle counts
    # CREATE_USER only, so skipping CHECK_STRING saves bandwidth, not the account ceiling.
    if check_name:
        chk = _commons("CMD_CHECK_STRING", 0, "", _B["device_hash"])
        chk["device"] = {"device_identifier": dev["device_identifier"]}
        chk["str_list"] = [name]
        r = client.send("CMD_CHECK_STRING", chk)
        if r.get("result") != "NOERR":
            raise GateError(f"CHECK_STRING failed: {r}")
    p = _commons("CMD_CREATE_USER", 0, "", _B["device_hash"])
    p["device"] = {"device_identifier": dev["device_identifier"]}
    p.update({
        "lang": "TH", "region": "REGION_ASIA", "pcspec": "NotImplement",
        "user_eula_season": "2025", "user_eula_info": _B["eula_info"],
        "pes_user_info": {
            "user_id": 0, "user_name": name, "is_deleted": "NO", "platform": "ANDROID",
            "psn_user_id": {"account_id": "NotImplement", "online_id": "", "is_valid_cache": "NO"},
            "xuid": "NotImplement", "steamid": "NotImplement",
            "nsw_user_id": {"nsa_id": "NotImplement", "issuer_id": 0},
            "avatar": {"is_already_set": "NO", "player_id": 0, "special_id": 0,
                       "background_id": 0, "special_id_background": 0,
                       "is_enable_player": "YES", "is_enable_background": "YES",
                       "material_key_player": "", "material_key_background": ""},
            "fameIdList": [],
            "user_banner": {"banner_id": {"unique_id": 0}, "is_valid": "NO"},
        },
        "cross_platform_option": "NO", "age": "2001-02-28",
        "os_version": dev["os_version"], "model_name": dev["model_name"],
        "gpu_name": dev["gpu_name"], "soc_name": dev["soc_name"],
        "survey_results": _B["survey"], "user_age_kind": "ADULT", "is_rooting": "NO",
        "phy_mem_used_mib": dev["phy_mem_used_mib"],
        "phy_mem_available_mib": dev["phy_mem_available_mib"],
        "app_storage_used_mib": 29640, "app_storage_available_mib": 79976,
        "data_storage_used_mib": 29640, "data_storage_available_mib": 79976,
        "payment_store_link_send_info": {"country_code": ""},
        "kid_challenge_id": "", "is_savedata_existing": "NO",
    })
    resp = client.send("CMD_CREATE_USER", p)
    if isinstance(resp, dict) and "auth_code" in resp:
        resp["_device_identifier"] = dev["device_identifier"]
    return resp


def login(client, user_id, auth_code, device_identifier=None):
    t = dict(_B["templates"]["CMD_LOGIN"])
    t["rqid"] = new_rqid()
    t["user_id"] = user_id
    t["auth_code"] = auth_code
    t["session_id"] = ""
    t["s_keyword"] = _B["s_keyword"]
    t["hash"] = _B["login_hash"]
    t["device"] = {"device_identifier": device_identifier or _B["device_id"]}
    return client.send("CMD_LOGIN", t)


def get_konami_link(client, user_id, session_id, device_identifier=None, lang="th"):
    """Fetch the my.konami.net account-link URL for a logged-in account.
    Returns {url, expires, link_status, result}. URL lives ~20 min."""
    b = _commons("CMD_GET_IS_KONAMIID_LINK", user_id, session_id, _B["login_hash"])
    b.update({
        "steam_id": "NotImplement", "xuid": "NotImplement", "account_id": "NotImplement",
        "platform": "ANDROID", "region": "REGION_ASIA",
        "is_needless_url": "NO", "is_need_info_url": "YES",
    })
    if device_identifier:
        b["device"] = {"device_identifier": device_identifier}
    r = client.send("CMD_GET_IS_KONAMIID_LINK", b)
    if not isinstance(r, dict):
        return {"url": "", "expires": 0, "link_status": None, "result": "?"}
    url = r.get("mykonami_url", "") or ""
    # in-game webview uses a locale path (/th/...); insert it to match
    if url and lang and "/account-link/" in url and f"/{lang}/account-link/" not in url:
        url = url.replace("my.konami.net/account-link/", f"my.konami.net/{lang}/account-link/")
    return {"url": url, "expires": r.get("url_expiration_date", 0),
            "link_status": r.get("link_status"), "result": r.get("result")}


def get_mailbox(client, user_id, session_id):
    return client.send("CMD_GET_MAILBOX_INFO",
                       _commons("CMD_GET_MAILBOX_INFO", user_id, session_id, _B["login_hash"]))


def get_mainmenu(client, user_id, session_id):
    return client.send("CMD_GET_MAINMENU_INFO",
                       _commons("CMD_GET_MAINMENU_INFO", user_id, session_id, _B["login_hash"]))


def get_login_bonus(client, user_id, session_id):
    return client.send("CMD_GET_LOGIN_BONUS_INFO",
                       _commons("CMD_GET_LOGIN_BONUS_INFO", user_id, session_id, _B["login_hash"]))


def play_sugoroku(client, user_id, session_id):
    """Play today's Daily Game (sugoroku) once: GET board -> SET one SUCCESS roll.
    1 play/account/day; the reward lands in the MAILBOX, so call this BEFORE the
    mailbox claim in the same session (RECV_PRESENT then collects it). Over days the
    32-node board reaches the Epic go-around reward.
    Verified live payload: {sugoroku_result:'SUCCESS', fail_reason:3, kind:<GET.kind>, stage_id:300}."""
    g = client.send("CMD_GET_SUGOROKU",
                    _commons("CMD_GET_SUGOROKU", user_id, session_id, _B["login_hash"]))
    if not isinstance(g, dict) or g.get("result") != "NOERR":
        return {"played": False, "reason": g.get("result") if isinstance(g, dict) else "err"}
    if g.get("is_sugoroku") != "YES":
        return {"played": False, "reason": "unavailable"}  # already played today / not active
    b = _commons("CMD_SET_SUGOROKU", user_id, session_id, _B["login_hash"])
    b.update({"sugoroku_result": "SUCCESS", "fail_reason": 3,
              "kind": g.get("kind", 1), "stage_id": 300})
    r = client.send("CMD_SET_SUGOROKU", b)
    if not isinstance(r, dict) or r.get("result") != "NOERR":
        return {"played": False, "reason": r.get("result") if isinstance(r, dict) else "err"}
    return {"played": True, "before": r.get("before_location"),
            "after": r.get("after_current_location"), "around": r.get("after_go_around_num")}


def recv_present(client, user_id, session_id, present_ids):
    body = {
        "msgid": "CMD_RECV_PRESENT", "rqid": new_rqid(), "user_id": user_id,
        "session_id": session_id, "my_platform": "ANDROID", "s_keyword": _B["s_keyword"],
        "present_list": [{"present_id": pid, "breakdown_present_id": 0} for pid in present_ids],
        "advertising_id": 0,
    }
    return client.send("CMD_RECV_PRESENT", body)


def account_status(client, user_id, session_id, mailbox=None):
    mm = get_mainmenu(client, user_id, session_id)
    mb = mailbox if mailbox is not None else get_mailbox(client, user_id, session_id)
    gp = mm.get("gp", 0) if isinstance(mm, dict) else 0
    coins = (mm.get("coin_info") or {}).get("coin", 0) if isinstance(mm, dict) else 0
    efp = mb.get("total_efootballpoint_num", 0) if isinstance(mb, dict) else 0
    return {"gp": gp, "coins": coins, "efp": efp,
            "mailbox": len(mb.get("present_list", [])) if isinstance(mb, dict) else 0}


def claim_daily(client, user_id, session_id, retries=6, wait=3.0):
    claimed = []
    seen = {}
    for attempt in range(max(1, retries)):
        try:
            get_login_bonus(client, user_id, session_id)
        except Exception:
            pass
        mb = get_mailbox(client, user_id, session_id)
        pres = mb.get("present_list", []) if isinstance(mb, dict) else []
        for p in pres:
            seen.setdefault(p.get("present_id"), p)
        if not pres:
            break
        recv_present(client, user_id, session_id, [p["present_id"] for p in pres])
        time.sleep(1.0)
        mb2 = get_mailbox(client, user_id, session_id)
        remaining = {p.get("present_id") for p in (mb2.get("present_list") or [])}
        for pid in [p["present_id"] for p in pres if p["present_id"] not in remaining]:
            i = seen.get(pid, {})
            claimed.append({"type": i.get("present_type"), "name": i.get("name"),
                            "count": i.get("count"), "reason": i.get("reason")})
        if not remaining:
            break
        if attempt < retries - 1:
            time.sleep(wait)
    return {"result": "NOERR", "claimed": claimed}


def claim_starter(user_id, auth_code, device_identifier=None, user_code=None,
                  tries=7, wait=4.0):
    claimed_all = []
    for _ in range(max(1, tries)):
        c = GateClient(user_code=user_code)
        sid = login(c, user_id, auth_code, device_identifier).get("session_id")
        if not sid:
            time.sleep(wait); continue
        st0 = account_status(c, user_id, sid)
        if st0["mailbox"] == 0 and (st0["gp"] > 0 or claimed_all):
            return {"claimed": claimed_all, "gp": st0["gp"], "coins": st0["coins"], "efp": st0["efp"]}
        cd = claim_daily(c, user_id, sid, retries=1)
        if cd["claimed"]:
            claimed_all = cd["claimed"]
        time.sleep(wait)
    c = GateClient(user_code=user_code)
    sid = login(c, user_id, auth_code, device_identifier).get("session_id")
    st = account_status(c, user_id, sid) if sid else {"gp": 0, "coins": 0, "efp": 0}
    return {"claimed": claimed_all, "gp": st["gp"], "coins": st["coins"], "efp": st["efp"]}


def _rewrite_serials(obj, new_uid):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "serial" and isinstance(v, int):
                obj[k] = new_uid * 10**9 + (v % 10**9)
            else:
                _rewrite_serials(v, new_uid)
    elif isinstance(obj, list):
        for v in obj:
            _rewrite_serials(v, new_uid)


def finish_setup(client, user_id, session_id):
    import copy
    tl = _commons("CMD_GET_USEABLE_TEAM_LIST", user_id, session_id, _B["login_hash"])
    tl.update({"list_type": "BASE_TEAM_SELECT", "event_id": 0})
    client.send("CMD_GET_USEABLE_TEAM_LIST", tl)
    p = copy.deepcopy(_B["entry_tpl"])
    p["rqid"] = new_rqid()
    p["user_id"] = user_id
    p["session_id"] = session_id
    p["s_keyword"] = _B["s_keyword"]
    _rewrite_serials(p, user_id)
    return client.send("CMD_SET_MYCLUB_ENTRY_INFO", p)


# ---------------------------------------------------------------- gacha
def scan_agents(client, uid, sid):
    b = _commons("CMD_GET_MYCLUB_AGENTLIST", uid, sid, _B["login_hash"])
    r = client.send("CMD_GET_MYCLUB_AGENTLIST", b)
    if not isinstance(r, dict) or r.get("result") != "NOERR":
        return {"all": [], "free": [], "free_count": 0}
    prices = {p["agent_id"]: p for p in r.get("agent_price_list", [])}
    out = []
    for ag in r.get("agent_list", []):
        aid = ag.get("agent_id")
        free_opt = None
        for opt in prices.get(aid, {}).get("price_list", []):
            pr = opt.get("price", {})
            if pr.get("coin_price", 1) == 0 and pr.get("gp_price", 1) == 0:
                free_opt = opt
                break
        limit = ag.get("limit_count", 0) or 0
        used = ag.get("use_count", 0) or 0
        pr = (free_opt or {}).get("price", {})
        needs_ad = bool(free_opt) and pr.get("can_use_coin") == "NO" and pr.get("can_use_gp") == "NO"
        bt = "BUY_BY_COIN"
        if pr.get("can_use_gp") == "YES":
            bt = "BUY_BY_GP"
        coin_p = gp_p = None   # cheapest non-zero coin / gp price across options
        for opt in prices.get(aid, {}).get("price_list", []):
            pr2 = opt.get("price", {})
            cp, gpp = pr2.get("coin_price") or 0, pr2.get("gp_price") or 0
            if cp > 0:
                coin_p = cp if coin_p is None else min(coin_p, cp)
            if gpp > 0:
                gp_p = gpp if gp_p is None else min(gp_p, gpp)
        out.append({"agent_id": aid, "title": ag.get("title"),
                    "free": bool(free_opt), "needs_ad": needs_ad,
                    "count": free_opt.get("multiple_count") if free_opt else None,
                    "buy_type": bt if free_opt else None,
                    "coin": coin_p, "gp": gp_p,
                    "remaining": max(0, limit - used)})
    free = [a for a in out if a["free"] and a["remaining"] > 0 and not a["needs_ad"]]
    return {"all": out, "free": free, "free_count": len(free)}


def use_agent(client, uid, sid, agent_id, count, buy_type="BUY_BY_COIN"):
    b = _commons("CMD_USE_MYCLUB_AGENT", uid, sid, _B["login_hash"])
    b.update({"agent_id": agent_id, "buy_type": buy_type,
              "gameplayer_id": {"id": 0, "serial": 0}, "draft_id": 0, "count": count,
              "void_player_ticket_id": 0, "advertising_id": 0})
    r = client.send("CMD_USE_MYCLUB_AGENT", b)
    players = []
    if isinstance(r, dict):
        for e in r.get("gameplayer_list", []):
            g = e.get("gameplayer", {})
            gid = g.get("gameplayer_id", {})
            players.append({"id": gid.get("id"), "serial": gid.get("serial"),
                            "rarity": g.get("default_rarity")})
    return {"result": r.get("result") if isinstance(r, dict) else "ERR", "players": players}


def draw_all_free(client, uid, sid, scan=None, only_agent=0):
    scan = scan or scan_agents(client, uid, sid)
    drawn = []
    for a in scan["free"]:
        if only_agent and a["agent_id"] != only_agent:
            continue
        for _ in range(a["remaining"]):
            res = use_agent(client, uid, sid, a["agent_id"], a["count"], a["buy_type"])
            if res["result"] != "NOERR":
                break
            drawn += res["players"]
    return drawn


def spin_to_target(client, uid, sid, wanted_set, need, pay_coin, coins,
                   scan=None, spin_agents=None, _stop=None):
    """Draw packs until `need` distinct wanted players are obtained. Free draws first;
    if pay_coin, keep drawing with the account's own coins (no cap) until the target is
    met OR coins can't afford another draw. Returns {drawn, coins_left, got}.

    spin_agents: restrict to these agent_ids (the pack(s) the wanted players came from);
    empty/None = every free pack + (if pay_coin) every coin pack.
    """
    scan = scan or scan_agents(client, uid, sid)
    want = set(wanted_set or [])
    got = set()
    drawn = []
    agents = scan.get("all", [])
    if spin_agents:
        sset = {int(x) for x in spin_agents}
        agents = [a for a in agents if a.get("agent_id") in sset]

    def _take(players):
        for p in players:
            drawn.append(p)
            if p.get("id") in want:
                got.add(p["id"])

    def _done():
        return len(got) >= need

    for a in agents:
        if _done() or (_stop and _stop()):
            break
        aid = a.get("agent_id")
        # free draws (limited by remaining)
        if a.get("free") and not a.get("needs_ad"):
            for _ in range(a.get("remaining") or 0):
                if _done() or (_stop and _stop()):
                    break
                res = use_agent(client, uid, sid, aid, a.get("count") or 1, a.get("buy_type") or "BUY_BY_COIN")
                if res.get("result") != "NOERR":
                    break
                _take(res.get("players") or [])
        # paid draws with the account's coins (no cap — stop when it can't afford)
        price = a.get("coin")
        if pay_coin and price and price > 0:
            while coins >= price and not _done() and not (_stop and _stop()):
                res = use_agent(client, uid, sid, aid, 1, "BUY_BY_COIN")
                if res.get("result") != "NOERR":
                    break   # gate rejected (out of coins / price mismatch) — stop this pack
                coins -= price
                _take(res.get("players") or [])
    return {"drawn": drawn, "coins_left": coins, "got": len(got)}


# --- efootbase enrichment (public player data: name / overall / art image) ---
import threading as _thr
_EFB_CACHE: dict = {}
_PESDB_LOCK = _thr.Lock()        # pesdb rate-limits hard (429 "Please wait"):
_PESDB_NEXT = [0.0]              # global min-interval gate (~1 req / 0.7s) + retry
_PESDB_GAP = 0.7

# persistent name cache: resolved names survive across runs/launches so a re-scan is
# instant (the slow part was re-fetching every card's name each scan).
_CACHE_DIR = os.environ.get("TEMP") or os.environ.get("TMP") or "."
_EFB_CACHE_FILE = os.path.join(_CACHE_DIR, "ewwdc_name_cache.json")
_EFB_CACHE_LOADED = [False]
_EFB_CACHE_LOCK = _thr.Lock()


def _efimg_art(pid):
    return f"https://efimg.com/efootballhub22/images/player_cards/{pid}_l.png"


def _load_name_cache():
    with _EFB_CACHE_LOCK:
        if _EFB_CACHE_LOADED[0]:
            return
        _EFB_CACHE_LOADED[0] = True
        try:
            with open(_EFB_CACHE_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k, v in d.items():
                    _EFB_CACHE.setdefault(str(k), v)
        except Exception:  # noqa: BLE001
            pass


_EFB_SAVE_LOCK = _thr.Lock()
_EFB_LAST_SAVE = [0.0]


def _save_name_cache(force=False):
    """Persist the resolved-name cache. DEBOUNCED: at most one full-file write per 5s
    (force=True bypasses). Without this, a 100k-account check would rewrite the whole
    growing cache file once per account across many threads — O(n) disk thrash."""
    now = time.time()
    with _EFB_SAVE_LOCK:
        if not force and now - _EFB_LAST_SAVE[0] < 5.0:
            return
        _EFB_LAST_SAVE[0] = now
    try:
        with _EFB_CACHE_LOCK:
            keep = {k: v for k, v in _EFB_CACHE.items()
                    if isinstance(v, dict) and v.get("name")}
        with open(_EFB_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(keep, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def _pesdb_gate():
    with _PESDB_LOCK:
        w = _PESDB_NEXT[0] - time.time()
        if w > 0:
            time.sleep(w)
        _PESDB_NEXT[0] = time.time() + _PESDB_GAP


def efb_lookup(pid, timeout=7):
    import re
    import html as _html
    key = str(pid)
    if key in _EFB_CACHE:
        return _EFB_CACHE[key]
    # art: deterministic efhub/efimg card image from the id — no scrape needed
    info = {"id": key, "name": None, "overall": None, "position": None,
            "art": f"https://efimg.com/efootballhub22/images/player_cards/{key}_l.png"}
    try:
        s = requests.Session()
        s.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 Chrome/120.0 Safari/537.36")
        r = s.get(f"https://efootbase.com/players/{key}", timeout=timeout,
                  allow_redirects=False)
        hops = 0
        while r.status_code in (301, 302, 307, 308) and hops < 3:
            loc = (r.headers.get("Location") or "").split(",")[0].strip()
            if not loc:
                break
            if loc.startswith("/"):
                loc = "https://efootbase.com" + loc
            r = s.get(loc, timeout=timeout, allow_redirects=False)
            hops += 1
        if r.status_code == 200:
            h = r.text
            m = re.search(r"<title>([^<]+)</title>", h)
            if m:
                title = _html.unescape(m.group(1)).split("—")[0].split("|")[0].strip()
                parts = [p.strip() for p in title.split("·")]
                if parts:
                    info["name"] = parts[0]
                if len(parts) >= 2:
                    mm = re.match(r"(\d{2,3})\s+([A-Z]{1,3})", parts[1])
                    if mm:
                        info["overall"] = int(mm.group(1))
                        info["position"] = mm.group(2)
            # (art comes from the deterministic efimg URL set above — no scrape)
    except Exception:
        pass
    # NOTE: the old pesdb.net fallback was removed — it was serialized (0.7s gate) +
    # retried 5x and is currently flaky, which dragged a 128-card scan to ~2 min for
    # the ~20% of cards efootbase misses. Those cards now just keep their efimg image
    # (always present) with no name; speed matters far more than a few missing names.
    if not info["name"]:
        info["name"] = None   # unresolved — UI shows the card by image + id
    _EFB_CACHE[key] = info
    return info


def efb_many(ids, workers=16):
    import concurrent.futures as cf
    _load_name_cache()
    out = {}
    todo = [str(i) for i in ids if str(i) not in _EFB_CACHE]
    for i in ids:
        if str(i) in _EFB_CACHE:
            e = _EFB_CACHE[str(i)]
            if isinstance(e, dict) and not e.get("art"):
                e["art"] = _efimg_art(i)   # fill art for entries cached before efimg
            out[str(i)] = e
    if todo:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for info in ex.map(efb_lookup, todo):
                out[info["id"]] = info
        _save_name_cache()   # persist newly-resolved names
    return out


def pack_players(client, uid, sid, agent_ids):
    """For each agent, return its showcase players enriched with name/overall/art,
    split into pickup (top featured) + more (the rest of the notable cards)."""
    b = _commons("CMD_GET_MYCLUB_AGENTLIST", uid, sid, _B["login_hash"])
    r = client.send("CMD_GET_MYCLUB_AGENTLIST", b)
    if not isinstance(r, dict) or r.get("result") != "NOERR":
        return []
    want = {int(x) for x in agent_ids}
    byid = {a.get("agent_id"): a for a in r.get("agent_list", [])}
    allids = []
    for aid in want:
        a = byid.get(aid, {})
        allids += [p.get("gameplayer_id", {}).get("id") for p in (a.get("pickup_list") or [])]
        allids += [x.get("id") for x in (a.get("pickup_cardtype_list") or [])]
    info = efb_many([i for i in allids if i])

    def card(gid):
        e = info.get(str(gid), {})
        return {"id": gid, "name": e.get("name"), "overall": e.get("overall"),
                "position": e.get("position"), "art": e.get("art")}

    packs = []
    for aid in want:
        a = byid.get(aid)
        if not a:
            continue
        pickup_ids = [p.get("gameplayer_id", {}).get("id") for p in (a.get("pickup_list") or [])]
        pickup = [{**card(i), "featured": True} for i in pickup_ids if i]
        seen = set(pickup_ids)
        more = [{**card(x["id"]), "featured": False} for x in (a.get("pickup_cardtype_list") or [])
                if x.get("id") and x["id"] not in seen]
        pickup.sort(key=lambda x: (x.get("overall") or 0), reverse=True)
        more.sort(key=lambda x: (x.get("overall") or 0), reverse=True)
        allp = pickup + more   # featured (pickup/Epic headline) first, then the rest
        if allp:
            packs.append({"agent_id": aid, "title": a.get("title"), "players": allp})
    return packs


# ---------------------------------------------------------------- persistence
# EPIC/legendary highest; falls back to overall when rarities tie or are unknown.
_RARITY_RANK = {"EPIC": 4, "LEGENDARY": 4, "HIGHLIGHT": 3, "FEATURED": 3,
                "SPECIAL": 3, "RARE": 2, "NORMAL": 1, "STANDARD": 1}
# the gate's own `default_rarity` is a 0..4 tier int (verified on real rosters):
#   0 = base squad (Standard)   2 = Special   3 = Highlight/Featured   4 = Epic (top:
#   Epic / Epic-Big Time / Legendary all land here — the roster does NOT split them).
# This is the only card-tier signal the login roster carries, so filtering/sorting by it
# needs ZERO per-card lookups (fast) — unlike the fine card-type which needs efootbase.
_RARITY_TIER = {0: "Standard", 1: "Bronze", 2: "Special", 3: "Highlight", 4: "Epic"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def _rarity_int(r):
    try:
        return int(r)
    except (TypeError, ValueError):
        return _RARITY_RANK.get(str(r or "").upper(), 0)   # legacy string rarities


def _tier_name(r):
    i = _rarity_int(r)
    return _RARITY_TIER.get(i, f"r{i}")


def _name_cfg(params):
    """User-configurable filename pattern (gacha / rename tool). Missing = legacy name."""
    c = (params or {}).get("name_cfg") or {}
    order = c.get("order") or ["coins", "names", "uc"]
    return {
        "coin_prefix": str(c.get("coin_prefix", "")),          # char(s) before the coin number
        "name_join": (str(c.get("name_join", "+")) or "+"),    # join player 2,3,4...
        "sep": (str(c.get("sep", "_")) or "_"),                # between parts
        "max_names": int(c.get("max_names", 0) or 0),          # 0 = all names
        "order": [k for k in order if k in ("coins", "names", "uc")] or ["coins", "names", "uc"],
    }


def _build_name(rec, params):
    """Build a filename from the user's name_cfg: e.g. coin_prefix='C', order=[coins,names,uc]
    → 'C100_Henry+Messi_<uc>.txt'. Names joined by name_join in record order (accumulated)."""
    cfg = _name_cfg(params)
    uc = _san(rec.get("user_code"), 24)
    coins = rec.get("coins", 0)
    players = rec.get("players") or []
    nl = [_san(p.get("name") or ("#" + str(p.get("id"))), 18) for p in players]
    if cfg["max_names"] > 0:
        nl = nl[:cfg["max_names"]]
    names = cfg["name_join"].join(nl) if nl else "none"
    parts = []
    for key in cfg["order"]:
        if key == "coins":
            parts.append(f"{cfg['coin_prefix']}{coins}")
        elif key == "names":
            parts.append(_san(names, 90))
        elif key == "uc":
            parts.append(uc)
    return cfg["sep"].join(p for p in parts if p) + _ext(params)


def _default_dir():
    return os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "accounts")


def _out_dir(params):
    """Where results are written (reroll output; merge destination)."""
    d = (params or {}).get("out_dir") or _default_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _src_dir(params):
    """Source folder of saved accounts (gacha/daily read from here, write back here)."""
    d = (params or {}).get("src_dir") or (params or {}).get("out_dir") or _default_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _ext(params):
    f = str((params or {}).get("fmt", "txt")).lstrip(".").lower()
    return ".dat" if f == "dat" else ".txt"


def _san(s, maxlen=40):
    s = _BAD.sub("", str(s if s is not None else "")).strip().strip(".")
    return (s[:maxlen]).strip() or "x"


def _acc_records(folder, on_progress=None, workers=16):
    """Read every saved account record in a folder (any .txt/.dat), keyed by user_code.
    Filename scheme is irrelevant — the record JSON carries user_code.

    Reads files in parallel (I/O-bound: disk + json.load release the GIL), so a
    100k-account folder loads in seconds instead of minutes. `on_progress(done,
    total)` is called (throttled) during the load so the UI never looks frozen."""
    import concurrent.futures as cf
    recs = {}
    try:
        names = sorted(fn for fn in os.listdir(folder)
                       if fn.lower().endswith((".txt", ".dat")))
    except OSError:
        return recs
    total = len(names)
    if on_progress:
        on_progress(0, total)

    def _rd(fn):
        fp = os.path.join(folder, fn)
        try:
            r = json.load(open(fp, encoding="utf-8"))
        except Exception:
            return None
        uc = r.get("user_code")
        if not uc:
            return None
        r["_file"] = fp
        return (uc, r)

    done = 0
    step = max(1, total // 40)   # ~40 progress ticks max
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(_rd, names):   # ordered → sorted last-write-wins on dup uc
            done += 1
            if res:
                recs[res[0]] = res[1]
            if on_progress and (done % step == 0 or done == total):
                on_progress(done, total)
    return recs


def _epic(players):
    """The standout card: highest rarity, then overall."""
    if not players:
        return None
    return sorted(players, key=lambda p: (_rarity_int(p.get("rarity")),
                                          p.get("overall") or 0), reverse=True)[0]


def _filename(scheme, rec, params):
    # user-configurable pattern (gacha/rename/daily): e.g. C100_Henry+Messi_<uc>.txt
    # daily uses it for a coin-prefixed name with no player names (order=[coins,uc]).
    if scheme in ("gacha", "rename", "daily") and (params or {}).get("name_cfg"):
        return _build_name(rec, params)
    uc = _san(rec.get("user_code"), 24)
    coins = rec.get("coins", 0)
    players = rec.get("players") or []
    if scheme == "daily":
        if (params or {}).get("daily_show_players") and players:
            ep = _epic(players)
            base = f"{_san(ep.get('name'))}_{coins}_{uc}"
        else:
            base = f"{coins}_{uc}"
    elif scheme in ("gacha", "rename"):
        names = "+".join(_san(p.get("name"), 18) for p in players) if players else "none"
        base = f"{_san(names, 60)}_{coins}_{uc}"
    elif scheme == "merge":
        ep = _epic(players)
        base = f"{_san(ep.get('name')) if ep else 'none'}(epic)_coin{coins}_{uc}"
    else:  # reroll (+ fallback)
        base = uc
    return base + _ext(params)


def build_uc_index(folder):
    """One-time {user_code: filepath} scan. Lets per-account dedup be O(1) instead of
    an os.listdir + json.load of the WHOLE folder on every single write — that scan
    was O(folder) per account, i.e. quadratic over a run, so the counter visibly
    stalled longer the more files piled up."""
    idx = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return idx
    for fn in names:
        if not fn.lower().endswith((".txt", ".dat")):
            continue
        fp = os.path.join(folder, fn)
        try:
            uc = json.load(open(fp, encoding="utf-8")).get("user_code")
        except Exception:
            continue
        if uc:
            idx[uc] = fp
    return idx


def build_uc_index_from(recs):
    """Build the {user_code: filepath} dedup index from ALREADY-LOADED records
    (recs = _acc_records() output) — avoids a SECOND os.listdir + json.load of the
    whole folder. Saves one full-folder read/parse pass (big at 100k+ accounts)."""
    return {uc: r.get("_file") for uc, r in recs.items() if r.get("_file")}


_SOCKS_PORTS = {"1080", "1081", "1085", "9050"}


def _norm_proxy(s):
    """Add a scheme if the user typed a bare host:port (or user:pass@host:port). Guess
    SOCKS5 for the usual SOCKS ports, else HTTP — requests needs a scheme to route."""
    s = str(s).strip()
    if not s or "://" in s:
        return s
    port = s.rsplit(":", 1)[-1] if ":" in s else ""
    return ("socks5://" if port in _SOCKS_PORTS else "http://") + s


_THORDATA_GATEWAYS = ["t.pr.thordata.net", "t.na.thordata.net", "t.eu.thordata.net"]


def _expand_thordata(url):
    """A thordata residential proxy line -> its 3 regional entry gateways (pr/na/eu),
    same credentials + port. Spreading workers across the 3 entry hosts lifts the
    single-gateway ceiling by ~19% (measured) for free — same account, same bandwidth.
    Any non-thordata proxy is returned unchanged."""
    from urllib.parse import urlsplit
    try:
        u = url if "://" in url else "http://" + url
        sp = urlsplit(u)
        host = sp.hostname or ""
        if not host.endswith(".thordata.net"):
            return [url]
        scheme = sp.scheme or "http"
        userinfo = ""
        if sp.username:
            userinfo = sp.username + ((":" + sp.password) if sp.password else "") + "@"
        port = ":" + str(sp.port) if sp.port else ":9999"
        return [f"{scheme}://{userinfo}{g}{port}" for g in _THORDATA_GATEWAYS]
    except Exception:
        return [url]


def start_uc_index(folder):
    """Non-blocking dedup index for reroll (which has no pre-loaded records): return an
    empty {uc:path} + its lock immediately and fill it in a background thread (setdefault,
    so a worker's live write always wins). A big output folder used to json.load every
    file before the first account — that scan is what made the run hang after Start."""
    import threading
    idx = {}
    lock = threading.Lock()

    def _bg():
        got = build_uc_index(folder)
        with lock:
            for k, v in got.items():
                idx.setdefault(k, v)

    threading.Thread(target=_bg, daemon=True).start()
    return idx, lock


def write_record(folder, rec, scheme, params, replace_old_uc=True,
                 uc_index=None, idx_lock=None, keep_name=False):
    """Write an account record; delete any previous file for the same user_code so
    one account never leaves stale copies behind when it gets re-named.

    keep_name=True: overwrite the record's ORIGINAL file in place (rec["_file"]) and
    keep its filename — never rename or delete the user's existing file. (Used by
    daily/gacha when the user ticks "ไม่เปลี่ยนชื่อไฟล์เดิม".)

    Hot loops pass a prebuilt uc_index (+ its lock): dedup is then a dict lookup and
    at most one os.remove — no folder scan. The write itself needs no lock (each
    account owns a unique path), so callers do it OUTSIDE their counter lock."""
    os.makedirs(folder, exist_ok=True)
    src_file = rec.get("_file")
    rec = {k: v for k, v in rec.items() if k != "_file"}
    rec.setdefault("version", "2.1.0")
    if keep_name and src_file and os.path.isfile(src_file):
        # write back to the exact same file — no new name, no dedup/remove
        with open(src_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False))
        return src_file
    path = os.path.join(folder, _filename(scheme, rec, params))
    uc = rec.get("user_code")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
    if replace_old_uc and uc is not None:
        if uc_index is not None:
            if idx_lock is not None:
                idx_lock.acquire()
            try:
                old = uc_index.get(uc)
                if old and os.path.abspath(old) != os.path.abspath(path):
                    try:
                        os.remove(old)
                    except OSError:
                        pass
                uc_index[uc] = path
            finally:
                if idx_lock is not None:
                    idx_lock.release()
        else:
            # legacy O(N) scan — kept for any non-hot caller that passes no index
            for fn in os.listdir(folder):
                if not fn.lower().endswith((".txt", ".dat")):
                    continue
                fp = os.path.join(folder, fn)
                if os.path.abspath(fp) == os.path.abspath(path):
                    continue
                try:
                    old = json.load(open(fp, encoding="utf-8"))
                except Exception:
                    continue
                if old.get("user_code") == uc:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
    return path


def _summary(log, title, lines):
    log("")
    log("====== สรุปผล " + title + " ======")
    for ln in lines:
        log("  " + ln)
    log("=" * 30)


# ---------------------------------------------------------------- module entrypoints
def _reroll(params, log):
    """Create `count` accounts across `workers` parallel threads (speed scales with
    the user's machine + network). Cooperative stop via the _STOP event; a result
    summary reports ok/fail counts and a sample of the errors."""
    import threading
    count = int((params or {}).get("count", 0) or 0)   # <=0 = unlimited (run until หยุด)
    workers = max(1, min(500, int((params or {}).get("workers", 1) or 1)))
    unlimited = count <= 0
    prefix = (params or {}).get("prefix") or _B.get("default_prefix", "j3xdr")
    # claim decoupled: a brand-new account's starter mailbox is NOT ready at
    # creation time, so the old inline claim_starter spun 7x (~35s/acct) and still
    # got nothing ~90% of the time. Default off → reroll just create+setup+save
    # (~2s/acct); the daily pass claims later when the mailbox is actually filled.
    claim = bool((params or {}).get("claim", False))
    # finish_setup (CMD_GET_USEABLE_TEAM_LIST + CMD_SET_MYCLUB_ENTRY_INFO) = 2 extra
    # gate requests/account. Default OFF → reroll = CHECK_STRING + CREATE_USER only
    # (2 req/acct instead of 5 → ~2.5x more accounts/sec on one IP). Turn on only if a
    # bare account can't later claim daily/gacha without the myClub entry being set.
    fsetup = bool((params or {}).get("finish_setup", False))
    delay = float((params or {}).get("delay", 0) or 0)      # floor หน่วง (วิ) — governor คุมต่อเอง
    show_errors = bool((params or {}).get("show_errors", True))
    # proxy pool (optional): one slot per worker, round-robin. Each line = one equal share
    # (repeat a line to give it more workers). Empty box = this machine's IP only.
    # 'direct' / 'local' / 'home' = a home-IP slot (None). If ANY real proxy is given,
    # a home-IP slot is auto-added → mixed mode (home + proxy fire together); add more
    # 'direct' lines, or repeat the proxy line, to shift the home:proxy ratio.
    raw_px = (params or {}).get("proxies")
    if isinstance(raw_px, str):
        raw_px = raw_px.splitlines()
    _DIRECT = {"direct", "local", "home", "none", "ip"}
    pool, has_proxy, has_thordata = [], False, False
    for p in (raw_px or []):
        s = str(p).strip()
        if not s or s.startswith("#"):
            continue
        if s.lower() in _DIRECT:
            pool.append(None)
        else:
            exp = _expand_thordata(_norm_proxy(s))   # add scheme if missing; thordata → 3 gateways
            pool.extend(exp)
            has_proxy = True
            if len(exp) > 1:
                has_thordata = True
    # auto-mix the home IP for a WEAK single proxy only. A strong rotating proxy
    # (thordata, expanded to 3 gateways) is faster alone — home-IP workers just hit the
    # per-IP throttle and drag throughput down (measured 7.2/s mixed vs 9.3/s proxy-only),
    # so skip the home slot there. Type 'direct' to force the home IP back into the pool.
    if has_proxy and None not in pool and not has_thordata:
        pool.insert(0, None)
    proxies = pool or [None]
    out = _out_dir(params)
    made, errors = [], []
    lock = threading.Lock()
    counter = {"n": 0}
    done = {"f": False}
    t0 = time.time()
    tgt = "∞" if unlimited else count
    gov = Governor(start=max(0.3, delay), lo=delay)   # AIMD: keeps 1 IP at its safe max
    uc_index, idx_lock = start_uc_index(out)

    def _stats():
        dt = max(0.001, time.time() - t0)
        ok = len(made)
        _event({"type": "stats", "ok": ok, "fail": len(errors), "elapsed": int(dt),
                "rate": round(ok / dt, 2), "perday": int(ok / dt * 86400),
                "running": not _stopped()})

    def worker(w):
        c = GateClient(proxy=proxies[w % len(proxies)])   # own session + IP per worker
        while not _stopped():
            with lock:
                if not unlimited and counter["n"] >= count:
                    return
                counter["n"] += 1
                i = counter["n"]
            try:
                resp = create_user(c, prefix=prefix)   # client.py already retries soft throttles
                if not isinstance(resp, dict) or "auth_code" not in resp:
                    msg = (resp or {}).get("result", "?") if isinstance(resp, dict) else "?"
                    if msg in RETRYABLE or msg == "ERR_NETWORK":
                        gov.on_throttle()
                    with lock:
                        errors.append(f"#{i} CREATE_USER {msg}")
                    if show_errors:
                        _event({"type": "log", "w": w, "lvl": "err", "t": f"[{i}] FAIL {msg}"})
                    gov.wait()
                    continue
                uc, auth = resp["user_code"], resp["auth_code"]
                uid = resp["pes_user_info"]["user_id"]
                devid = resp.get("_device_identifier")
                coins = gp = 0
                setup = "skipped"
                # login/finish_setup/claim only when asked — a plain reroll (both off)
                # stops at create_user, so it's just 2 gate requests per account.
                if fsetup or claim:
                    try:
                        lr = login(c, uid, auth, devid)
                        sid = lr.get("session_id")
                        if sid:
                            if fsetup:
                                setup = finish_setup(c, uid, sid).get("result", "?")
                            if claim and not _stopped():
                                cs = claim_starter(uid, auth, devid, uc)
                                coins, gp = cs.get("coins", 0), cs.get("gp", 0)
                    except Exception as e:
                        setup = f"err:{e}"
                rec = {"user_code": uc, "user_id": uid, "auth_code": auth,
                       "device_identifier": devid, "coins": coins, "gp": gp,
                       "players": [], "module": "reroll"}
                write_record(out, rec, "reroll", params, uc_index=uc_index, idx_lock=idx_lock)
                with lock:
                    made.append(uc)
                gov.on_success()
                _event({"type": "log", "w": w, "lvl": "ok", "t": f"[{i}] OK {uc} coins={coins}"})
            except Exception as e:
                gov.on_throttle()
                with lock:
                    errors.append(f"#{i} {type(e).__name__}: {e}")
                if show_errors:
                    _event({"type": "log", "w": w, "lvl": "err", "t": f"[{i}] ERR {e}"})
            gov.wait()

    peak = {"r": 0.0}   # highest windowed accounts/วิ seen = the IP's real ceiling

    def monitor():   # one stats/progress pulse ~2x/s (decoupled from per-account → no UI flood)
        prev_t, prev_ok = time.time(), 0
        while not done["f"]:
            _report(len(made), len(errors), count)
            _stats()
            now = time.time()
            ok = len(made)
            dt = now - prev_t
            if dt >= 1.0:   # windowed rate over the last ~1s, not the cumulative average
                wr = (ok - prev_ok) / dt
                if wr > peak["r"]:
                    peak["r"] = wr
                prev_t, prev_ok = now, ok
            time.sleep(0.4)

    _report(0, 0, count)
    _event({"type": "started", "workers": workers})
    px_note = (" · proxy " + str(len(proxies)) + " IP") if proxies != [None] else " · IP เครื่อง"
    log(f"เริ่ม reroll {tgt} บัญชี · {workers} worker{px_note}"
        + (" (0=ไม่จำกัด จนกว่าจะกดหยุด)" if unlimited else ""))
    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()
    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    done["f"] = True
    stopped = _stopped()
    dt_total = max(0.001, time.time() - t0)
    n_made = len(made)
    ip_n = 1 if proxies == [None] else len(proxies)
    log(f"governor: ok={gov.ok} throttled={gov.throttled} final_delay={gov.delay:.2f}s")
    log(f"ceiling: {n_made} acct / {dt_total:.0f}s = {n_made / dt_total:.2f}/วิ sustained"
        f" · peak {peak['r']:.2f}/วิ · {ip_n} IP · {workers} worker"
        f" · {n_made / dt_total / ip_n:.2f}/วิ ต่อ IP")
    lines = [f"สร้างสำเร็จ {len(made)}  ล้มเหลว {len(errors)}"
             + ("" if unlimited else f"  (เป้า {count})")]
    if stopped:
        lines.append("** หยุดโดยผู้ใช้ **")
    lines.append(f"โฟลเดอร์: {out}")
    if errors:
        lines.append(f"error ({min(len(errors), 10)} ตัวอย่างล่าสุด):")
        lines += ["- " + e for e in errors[-10:]]
    _summary(log, "REROLL", lines)
    if (params or {}).get("post_gacha") and made and not stopped:
        log("── สุ่มกาชาฟรีต่อหลัง reroll (เปิดแพ็คฟรีทั้งหมด) ──")
        gp = dict(params)
        gp.pop("action", None)
        gp["module"] = "gacha"
        gp["src_dir"] = out
        gp["out_dir"] = out
        gp["wanted_ids"] = (params or {}).get("wanted_ids") or []  # [] = keep all
        try:
            _gacha(gp, log)
        except Exception as e:  # noqa: BLE001
            log(f"กาชาต่อผิดพลาด: {e}")
    _stats()
    _event({"type": "stopped"})
    _event({"type": "done", "module": "reroll",
            "summary": {"ok": len(made), "fail": len(errors), "total": count, "stopped": stopped}})
    return {"module": "reroll", "created": made, "failed": len(errors), "out_dir": out,
            "stopped": stopped, "errors": errors,
            "summary": {"ok": len(made), "fail": len(errors), "total": count}}


def _gacha_scan(params, log):
    """Login ONE saved account, scan free packs, return pack players (+art) to pick from."""
    src = _src_dir(params)
    recs = _acc_records(src, on_progress=lambda d, t: log(f"  โหลดบัญชี {d}/{t}"))
    if not recs:
        log("ไม่พบไฟล์บัญชีในโฟลเดอร์ (เลือกโฟลเดอร์ที่มีไฟล์บัญชีก่อน)")
        return {"module": "gacha", "action": "scan", "packs": []}
    a = next(iter(recs.values()))
    log(f"สแกนจากบัญชี {a['user_code']} (1 ไอดีพอ — แพ็คทั้งเซิร์ฟเหมือนกัน)")
    c = GateClient(user_code=a["user_code"])
    lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
    sid = lr.get("session_id")
    if not sid:
        log("login บัญชีที่ใช้สแกนไม่สำเร็จ")
        return {"module": "gacha", "action": "scan", "packs": []}
    scan = scan_agents(c, int(a["user_id"]), sid)
    meta = {x["agent_id"]: x for x in scan["all"]}
    all_ids = [x["agent_id"] for x in scan["all"]]
    free_ids = {x["agent_id"] for x in scan["free"]}
    log(f"เจอ {len(all_ids)} แพ็ค (ฟรีกดได้ {len(free_ids)}) — กำลังดึงรายชื่อนักเตะ")
    packs = pack_players(c, int(a["user_id"]), sid, all_ids) if all_ids else []

    def _why(s):
        if not s.get("free"):
            return "ซื้อไม่ได้ (ไม่มีออปชันฟรี)"
        if s.get("needs_ad"):
            return "ต้องดูโฆษณาก่อน (กดอัตโนมัติไม่ได้)"
        if (s.get("remaining") or 0) <= 0:
            return "ครบโควตาแล้ว"
        return "ซื้อไม่ได้"

    for p in packs:
        s = meta.get(p["agent_id"], {})
        p["buyable"] = p["agent_id"] in free_ids
        p["free"] = p["buyable"]
        p["coin"] = s.get("coin")
        p["gp"] = s.get("gp")
        p["reason"] = "" if p["buyable"] else _why(s)
        if not p["buyable"]:
            log(f"  ข้ามแพ็ค {p.get('title') or p['agent_id']}: {p['reason']}")
    packs.sort(key=lambda p: (not p["buyable"], -(p["players"][0]["overall"] or 0) if p["players"] else 0))
    buy_n = sum(1 for p in packs if p["buyable"])
    log(f"ได้ {sum(len(p['players']) for p in packs)} นักเตะจาก {len(packs)} แพ็ค (กดฟรีได้ {buy_n})")
    return {"module": "gacha", "action": "scan", "packs": packs}


def _gacha(params, log):
    """Open free packs on saved accounts; name files by the hit players in the
    user's filter order + coins + user_code, and store the players in the record."""
    if (params or {}).get("action") == "scan":
        return _gacha_scan(params, log)
    import threading
    src = _src_dir(params)
    only = int((params or {}).get("agent", 0))
    delay = float((params or {}).get("delay", 0) or 0)
    keep_name = bool((params or {}).get("keep_filename", True))   # legacy: overwrite in place
    # accumulate (DEFAULT ON): when hunting a target, append each newly-hit player to the
    # ones already named on the file (union by id) and rename → C100_Henry+Messi_<uc>.txt.
    # This is why a 2nd/3rd hit must rename (old keep_name silently kept the stale name).
    accumulate = bool((params or {}).get("accumulate", True))
    workers = max(1, min(500, int((params or {}).get("workers", 32) or 32)))
    wanted_order = [int(x) for x in (params or {}).get("wanted_ids", [])]
    wanted_set = set(wanted_order)
    target_mode = (params or {}).get("target_mode") or ""   # "any" / "two" / "all"
    pay_coin = bool((params or {}).get("pay_coin"))
    spin_agents = [int(x) for x in (params or {}).get("spin_agents", []) if str(x).strip()]
    spinning = bool(wanted_set and (target_mode or pay_coin))   # spin-to-target mode
    # split hits into their own folder: accounts that satisfy the target get moved to a
    # separate folder (custom hit_dir, or auto = <src>/<wanted names joined by +>).
    split_hits = bool((params or {}).get("split_hits", True)) and bool(wanted_set)
    need_split = 1 if target_mode in ("", "any") else (2 if target_mode == "two" else len(wanted_set))
    need_split = max(1, min(need_split, len(wanted_set))) if wanted_set else 1
    hit_dir = None
    if split_hits:
        hit_dir = (params or {}).get("hit_dir") or None
        if not hit_dir:
            wn = efb_many(list(wanted_order))   # resolve wanted names for the folder name
            parts = [_san((wn.get(str(i), {}).get("name") or ("id" + str(i))).lower().replace(" ", "_"), 24)
                     for i in wanted_order]
            hit_dir = os.path.join(src, _san("+".join(p for p in parts if p) or "hits", 100))
        os.makedirs(hit_dir, exist_ok=True)
        log(f"แยกไอดีที่ได้เป้าหมาย → {hit_dir}")
    _recs_d = _acc_records(src, on_progress=lambda d, t: log(f"  โหลดบัญชี {d}/{t}"))
    recs = list(_recs_d.items())
    total = len(recs)
    gov = Governor(start=max(0.3, delay), lo=delay)   # AIMD: self-tunes to the IP
    cnt = {"hit": 0, "fail": 0}
    idx = {"i": 0}
    results = []
    lock = threading.Lock()
    log(f"gacha pass: {total} accounts @ {src} · {workers} worker (adaptive)")
    _report(0, 0, total)

    def _next():
        with lock:
            if idx["i"] >= total:
                return None
            it = recs[idx["i"]]
            idx["i"] += 1
            return it

    uc_index = build_uc_index_from(_recs_d)   # reuse loaded records (no 2nd folder read)
    idx_lock = threading.Lock()

    def worker():
        c = GateClient()   # reused across accounts (keep-alive); user_code set per account
        while not _stopped():
            item = _next()
            if item is None:
                return
            gov.wait()
            if _stopped():
                return
            uc, a = item
            c.user_code = uc
            c.http.cookies.clear()   # reused session: drop any server-set cookie per account
            try:
                lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
                sid = lr.get("session_id")
                if not sid:
                    res = lr.get("result") if isinstance(lr, dict) else "?"
                    if res in RETRYABLE or res == "ERR_NETWORK":
                        gov.on_throttle()
                    with lock:
                        cnt["fail"] += 1
                        log(f"{uc}: login failed ({res})")
                        _report(cnt["hit"], cnt["fail"], total)
                    continue
                if _stopped():
                    return
                if spinning:
                    scan = scan_agents(c, int(a["user_id"]), sid)
                    coins0 = account_status(c, int(a["user_id"]), sid)["coins"]
                    need = 1 if target_mode == "any" else (2 if target_mode == "two" else len(wanted_set))
                    need = max(1, min(need, len(wanted_set)))
                    sp = spin_to_target(c, int(a["user_id"]), sid, wanted_set, need,
                                        pay_coin, coins0, scan=scan,
                                        spin_agents=spin_agents, _stop=_stopped)
                    drawn = sp["drawn"]
                else:
                    drawn = draw_all_free(c, int(a["user_id"]), sid, only_agent=only)
                info = efb_many([p["id"] for p in drawn if p.get("id")])
                by_id = {}
                for p in drawn:
                    e = info.get(str(p.get("id")), {})
                    by_id[p["id"]] = {"id": p["id"], "name": e.get("name") or str(p.get("id")),
                                      "overall": e.get("overall"), "rarity": p.get("rarity")}
                if wanted_set:
                    # keep only wanted, in the exact order the user ticked them
                    ordered = [by_id[i] for i in wanted_order if i in by_id]
                else:
                    ordered = sorted(by_id.values(), key=lambda x: x.get("overall") or 0, reverse=True)
                st = account_status(c, int(a["user_id"]), sid)
                rec = dict(a)   # keep _file so keep_name can overwrite in place
                # accumulate: union previously-named players (from the record) with this
                # run's hits, preserving old order and appending new ones → name grows with +
                if wanted_set and accumulate:
                    merged = list(a.get("players") or [])
                    seen = {p.get("id") for p in merged}
                    new_hits = [p for p in ordered if p.get("id") not in seen]
                    for p in new_hits:
                        merged.append(p)
                        seen.add(p.get("id"))
                    rec_players = merged
                    this_keep = False   # must rename to reflect the added player(s)
                    got_new = bool(new_hits)
                else:
                    rec_players = ordered
                    this_keep = keep_name
                    got_new = bool(ordered)
                rec.update({"coins": st["coins"], "gp": st["gp"],
                            "players": rec_players, "module": "gacha"})
                gov.on_success()
                # does the (accumulated) account now satisfy the target? → move to hit folder
                n_wanted = sum(1 for p in rec_players if p.get("id") in wanted_set)
                is_hit = bool(wanted_set) and n_wanted >= need_split
                if split_hits and is_hit and hit_dir:
                    write_record(hit_dir, rec, "gacha", params, replace_old_uc=False)
                    srcf = a.get("_file")
                    if srcf and os.path.isfile(srcf):
                        try:
                            os.remove(srcf)
                        except OSError:
                            pass
                        with idx_lock:
                            uc_index.pop(uc, None)
                else:
                    write_record(src, rec, "gacha", params, uc_index=uc_index, idx_lock=idx_lock, keep_name=this_keep)
                with lock:
                    if got_new:
                        cnt["hit"] += 1
                    results.append({"user_code": uc, "drawn": len(drawn),
                                    "hits": [p["id"] for p in ordered], "moved": bool(split_hits and is_hit)})
                    mv = " → แยกโฟลเดอร์" if (split_hits and is_hit) else ""
                    log(f"{uc}: drawn={len(drawn)} hits={len(ordered)} coins={st['coins']}{mv}")
                    _report(cnt["hit"], cnt["fail"], total)
            except Exception as e:
                gov.on_throttle()
                with lock:
                    cnt["fail"] += 1
                    log(f"{uc}: ERROR {e}")
                    _report(cnt["hit"], cnt["fail"], total)

    nthreads = min(workers, max(1, total))
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(nthreads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if _stopped():
        log("** หยุดโดยผู้ใช้ **")
    log(f"governor: ok={gov.ok} throttled={gov.throttled} final_delay={gov.delay:.2f}s")
    _summary(log, "GACHA", [f"บัญชีที่ได้นักเตะเป้าหมาย {cnt['hit']}/{total}  "
                            f"(พลาด {cnt['fail']})", f"โฟลเดอร์: {src}"])
    return {"module": "gacha", "results": results,
            "summary": {"hit_accounts": cnt["hit"], "total": total}}


def _daily(params, log):
    """Claim daily/starter presents on every saved account, in parallel.

    Threaded like _check, but paced by a shared AIMD Governor instead of a fixed
    worker count: all `workers` threads share one source IP, so the governor speeds
    up while Konami answers cleanly and backs off the moment it throttles — it
    self-finds the IP's safe rate. `workers` is the ceiling, not the target."""
    import threading
    src = _src_dir(params)
    _recs_d = _acc_records(src, on_progress=lambda d, t: log(f"  โหลดบัญชี {d}/{t}"))
    recs = list(_recs_d.items())
    total = len(recs)
    show_players = bool((params or {}).get("daily_show_players"))
    keep_name = bool((params or {}).get("keep_filename", True))   # default: don't rename user's files
    do_sugoroku = bool((params or {}).get("sugoroku", True))       # เกมประจำวัน (default ON)
    delay = float((params or {}).get("delay", 0) or 0)
    workers = max(1, min(500, int((params or {}).get("workers", 64) or 64)))
    gov = Governor(start=max(0.3, delay), lo=delay)   # AIMD: self-tunes to the IP
    log(f"daily pass: {total} accounts @ {src} · {workers} worker (adaptive)")
    cnt = {"ok": 0, "claimed": 0, "fail": 0, "sugoroku": 0}
    idx = {"i": 0}
    lock = threading.Lock()
    _report(0, 0, total)

    def _next():
        with lock:
            if idx["i"] >= total:
                return None
            it = recs[idx["i"]]
            idx["i"] += 1
            return it

    uc_index = build_uc_index_from(_recs_d)   # reuse loaded records (no 2nd folder read)
    idx_lock = threading.Lock()

    def worker():
        # persistent worker: pull the next account until the queue drains or stop.
        # checking _stopped() each loop makes หยุด single-press (no pre-queued backlog).
        # ONE GateClient per worker, reused across accounts → TCP/TLS keep-alive to the
        # gate host instead of a fresh handshake per account (set user_code per account).
        c = GateClient()
        while not _stopped():
            item = _next()
            if item is None:
                return
            gov.wait()
            if _stopped():
                return
            uc, a = item
            uid, auth = a["user_id"], a["auth_code"]
            c.user_code = uc
            c.http.cookies.clear()   # reused session: drop any server-set cookie per account
            try:
                lr = login(c, uid, auth, a.get("device_identifier"))
                if not isinstance(lr, dict) or "session_id" not in lr:
                    res = lr.get("result") if isinstance(lr, dict) else "?"
                    if res in RETRYABLE or res == "ERR_NETWORK":
                        gov.on_throttle()      # IP throttle → slow every worker down
                    with lock:
                        cnt["fail"] += 1
                        log(f"  {uc}: LOGIN fail {res}")
                        _report(cnt["ok"], cnt["fail"], total)
                    continue
                sid = lr["session_id"]
                if _stopped():
                    return
                # trigger today's login bonus FIRST — the gate delivers the daily
                # reward into the mailbox, so it must be claimed below (recv_present).
                try:
                    get_login_bonus(c, uid, sid)
                except Exception:  # noqa: BLE001
                    pass
                # Daily Game (sugoroku) — reward also lands in the mailbox, so play it
                # BEFORE the claim. Reuses the open session (no re-login). GET gates on
                # is_sugoroku=YES → no wasted SET / no error when already played today.
                sug = None
                if do_sugoroku:
                    try:
                        sug = play_sugoroku(c, uid, sid)
                    except Exception as e:  # noqa: BLE001
                        sug = {"played": False, "reason": str(e)}
                mb = get_mailbox(c, uid, sid)
                ids = [p["present_id"] for p in mb.get("present_list", [])] if isinstance(mb, dict) else []
                claimed = 0
                if ids:
                    rr = recv_present(c, uid, sid, ids)
                    claimed = len(rr.get("receive_result_list", [])) if rr.get("result") == "NOERR" else 0
                # nothing claimed → mailbox unchanged, reuse it (skip one get_mailbox)
                st = account_status(c, uid, sid, mailbox=(None if ids else mb))
                rec = dict(a)   # keep _file so keep_name can overwrite in place
                rec.update({"coins": st["coins"], "gp": st["gp"], "efp": st["efp"],
                            "module": "daily"})
                gov.on_success()               # clean answer → let the governor speed up
                pl = ""
                if show_players and rec.get("players"):
                    pl = " | นักเตะ: " + ", ".join(p.get("name", "?") for p in rec["players"][:3])
                # write off the counter lock (unique path per account; dedup via index)
                write_record(src, rec, "daily", params, uc_index=uc_index, idx_lock=idx_lock, keep_name=keep_name)
                sg_played = bool(sug and sug.get("played"))
                with lock:
                    cnt["ok"] += 1
                    cnt["claimed"] += claimed
                    if sg_played:
                        cnt["sugoroku"] += 1
                    got = (f"เก็บ Daily Login + กล่องจดหมาย {claimed} ชิ้น" if claimed
                           else "เก็บครบแล้ว (ไม่มีของใหม่ในกล่อง)")
                    sgtxt = " · เกมประจำวัน✓" if sg_played else ""
                    log(f"  {uc}: OK · {got}{sgtxt} · coins={st['coins']}{pl}")
                    _report(cnt["ok"], cnt["fail"], total)
            except Exception as e:
                gov.on_throttle()
                with lock:
                    cnt["fail"] += 1
                    log(f"  {uc}: ERROR {e}")
                    _report(cnt["ok"], cnt["fail"], total)

    nthreads = min(workers, max(1, total))
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(nthreads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if _stopped():
        log("** หยุดโดยผู้ใช้ **")
    log(f"governor: ok={gov.ok} throttled={gov.throttled} final_delay={gov.delay:.2f}s")
    _summary(log, "DAILY", [f"ล็อกอินสำเร็จ {cnt['ok']}/{total}  พลาด {cnt['fail']}",
                            f"รับ Daily Login + กล่องจดหมาย รวม {cnt['claimed']} ชิ้น",
                            f"เล่นเกมประจำวัน (sugoroku) สำเร็จ {cnt['sugoroku']} บัญชี",
                            f"โฟลเดอร์: {src}"])
    return {"module": "daily", "ok": cnt["ok"], "total": total, "claimed": cnt["claimed"],
            "summary": {"ok": cnt["ok"], "total": total, "claimed": cnt["claimed"]}}


def _merge(params, log):
    """Collect saved files across several folders, group by user_code, union the
    players, and write one combined file per account named
    <epicPlayer>(epic)_coin<coins>_<user_code>."""
    dirs = list((params or {}).get("merge_dirs") or [])
    if not dirs:
        d = (params or {}).get("src_dir") or (params or {}).get("out_dir")
        dirs = [d] if d else []
    dirs = [d for d in dirs if d and os.path.isdir(d)]
    out = _out_dir(params)
    merged = {}
    for d in dirs:
        for uc, r in _acc_records(d).items():
            m = merged.setdefault(uc, {"user_code": uc, "players": [], "coins": 0})
            for k in ("user_id", "auth_code", "device_identifier", "gp", "efp"):
                v = r.get(k)
                if v not in (None, "", 0) or k not in m:
                    m[k] = v if v is not None else m.get(k)
            m["coins"] = max(m.get("coins", 0) or 0, r.get("coins", 0) or 0)
            seen = {p.get("id") for p in m["players"]}
            for p in (r.get("players") or []):
                if p.get("id") not in seen:
                    m["players"].append(p); seen.add(p.get("id"))
    n = 0
    for uc, m in merged.items():
        m["module"] = "merge"
        write_record(out, m, "merge", params, replace_old_uc=False)
        n += 1
    _summary(log, "MERGE", [f"รวม {n} บัญชี จาก {len(dirs)} โฟลเดอร์",
                            f"โฟลเดอร์ปลายทาง: {out}"])
    return {"module": "merge", "merged": n, "out_dir": out,
            "summary": {"merged": n, "dirs": len(dirs)}}


def _rename(params, log):
    """แก้ไขชื่อไฟล์: rebuild every saved file's name from a user pattern (name_cfg).

    coins + player names each come from one of three sources:
      keep   — whatever the record already stores (no login, instant)
      live   — log in and refresh (coins from account_status; names from the owned roster,
               filtered by card_types tiers, top `rename_top` by rarity→overall)
      manual — a fixed value the user typed (rename_coin / rename_name)
    Old file is removed and rewritten under the new name (dedup by user_code)."""
    import concurrent.futures as cf
    import threading
    src = _src_dir(params)
    recs = _acc_records(src, on_progress=lambda d, t: log(f"  โหลดบัญชี {d}/{t}"))
    total = len(recs)
    coin_src = str((params or {}).get("rename_coin_source", "keep"))
    name_src = str((params or {}).get("rename_name_source", "keep"))
    manual_coin = (params or {}).get("rename_coin")
    manual_name = (params or {}).get("rename_name")
    top_n = int((params or {}).get("rename_top", 3) or 3)
    _ct = (params or {}).get("card_types")
    card_types = set(int(x) for x in _ct) if _ct else None
    live = coin_src == "live" or name_src == "live"
    workers = max(1, min(32, int((params or {}).get("workers", 8) or 8)))
    log(f"แก้ไขชื่อไฟล์ {total} ไฟล์ @ {src}  (coins={coin_src}, name={name_src})")
    uc_index = build_uc_index_from(recs)   # recs already loaded — no 2nd folder read
    idx_lock = threading.Lock()
    cnt = {"ok": 0, "fail": 0}
    lock = threading.Lock()
    _report(0, 0, total)

    def one(item):
        uc, a = item
        if _stopped():
            return
        rec = dict(a)
        err = None
        if live:
            try:
                c = GateClient(user_code=uc)
                lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
                sid = lr.get("session_id")
                if not sid:
                    err = "login failed"
                else:
                    if coin_src == "live":
                        st = account_status(c, int(a["user_id"]), sid)
                        rec["coins"] = st["coins"]
                    if name_src == "live":
                        roster = (lr.get("entry_info") or {}).get("gameplayer_list") or []
                        pl = [{"id": p.get("gameplayer_id", {}).get("id"),
                               "rarity": _rarity_int(p.get("default_rarity"))}
                              for p in roster if p.get("gameplayer_id", {}).get("id")]
                        if card_types is not None:
                            pl = [p for p in pl if p["rarity"] in card_types]
                        pl.sort(key=lambda p: p["rarity"], reverse=True)
                        pl = pl[:max(1, top_n)]
                        info = efb_many([p["id"] for p in pl])
                        for p in pl:
                            p["name"] = info.get(str(p["id"]), {}).get("name") or ("#" + str(p["id"]))
                        rec["players"] = pl
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
        if coin_src == "manual" and manual_coin is not None:
            try:
                rec["coins"] = int(manual_coin)
            except (TypeError, ValueError):
                rec["coins"] = manual_coin
        if name_src == "manual" and manual_name:
            # one or more names, split on '+' → player list the pattern joins back
            rec["players"] = [{"id": None, "name": s.strip()} for s in str(manual_name).split("+") if s.strip()]
        if err:
            with lock:
                cnt["fail"] += 1
            log(f"  {uc}: {err}")
            _report(cnt["ok"], cnt["fail"], total)
            return
        newpath = write_record(src, rec, "rename", params, uc_index=uc_index, idx_lock=idx_lock)
        with lock:
            cnt["ok"] += 1
        log(f"  {uc}: → {os.path.basename(newpath)}")
        _report(cnt["ok"], cnt["fail"], total)

    with cf.ThreadPoolExecutor(max_workers=(workers if live else 1)) as ex:
        for _ in ex.map(one, list(recs.items())):
            pass
    _summary(log, "RENAME", [f"เปลี่ยนชื่อ {cnt['ok']}/{total} ไฟล์  (พลาด {cnt['fail']})",
                             f"โฟลเดอร์: {src}"])
    return {"module": "rename", "renamed": cnt["ok"], "total": total,
            "summary": {"renamed": cnt["ok"], "total": total}}


def _check(params, log):
    """Audit saved accounts: login each + report currency (GP/coins/EFP). Emits
    rows to the dashboard check table. Read-only — never writes the records."""
    import concurrent.futures as cf
    import threading
    src = _src_dir(params)
    files = (params or {}).get("files")
    if files:   # individual .txt/.dat files picked in the UI
        recs = {}
        for fp in files:
            try:
                r = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            uc = r.get("user_code")
            if uc:
                r["_file"] = fp
                recs[uc] = r
    else:
        recs = _acc_records(src, on_progress=lambda d, t: log(f"  โหลดบัญชี {d}/{t}"))
    total = len(recs)
    workers = max(1, min(32, int((params or {}).get("workers", 5) or 5)))
    # #5 Filter Card Type: keep only these default_rarity tiers (ints). Empty/None = all.
    # Filtering BEFORE name-resolve means far fewer efootbase lookups → faster scan.
    _ct = (params or {}).get("card_types")
    card_types = set(int(x) for x in _ct) if _ct else None
    resolve_names = bool((params or {}).get("resolve_names", True))   # #3 name search needs names
    _event({"type": "cstart", "total": total})
    log(f"เช็คไอดี {total} บัญชี @ {src} · {workers} worker")
    done = {"n": 0}
    lock = threading.Lock()
    out = []

    def chk(item):
        uc, a = item
        if _stopped():
            return
        rep = {"user_code": uc}
        try:
            c = GateClient(user_code=uc)
            lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
            sid = lr.get("session_id")
            if not sid:
                rep["error"] = "login failed"
            else:
                st = account_status(c, int(a["user_id"]), sid)
                # OWNED cards straight from the login response (the account's real roster),
                # not the saved record — id works with efimg art, carries rarity + sell_gp.
                roster = (lr.get("entry_info") or {}).get("gameplayer_list") or []
                players = [{"id": p.get("gameplayer_id", {}).get("id"),
                            "rarity": _rarity_int(p.get("default_rarity")),
                            "sell_gp": p.get("sell_gp")}
                           for p in roster if p.get("gameplayer_id", {}).get("id")]
                n_all = len(players)
                if card_types is not None:   # #5 pre-filter by tier (fast, no lookups)
                    players = [p for p in players if p["rarity"] in card_types]
                if resolve_names and players:   # resolve names only for the KEPT cards
                    info = efb_many([p["id"] for p in players])
                    for p in players:
                        e = info.get(str(p["id"]), {})
                        p["name"] = e.get("name")
                        p["overall"] = e.get("overall")
                        p["art"] = e.get("art")
                        p["tier"] = _tier_name(p["rarity"])
                else:
                    for p in players:
                        p["tier"] = _tier_name(p["rarity"])
                rep.update({"gp": st["gp"], "coins": st["coins"], "efp": st["efp"],
                            "n_all": n_all,
                            "mailbox": st["mailbox"],
                            "players": players, "n_players": len(players),
                            # creds for inline Gen Link / Inject (stay local in the app)
                            "user_id": a.get("user_id"), "auth_code": a.get("auth_code"),
                            "device_identifier": a.get("device_identifier")})
        except Exception as e:
            rep["error"] = f"{type(e).__name__}: {e}"
        with lock:
            done["n"] += 1
            d = done["n"]
            out.append(rep)
        log("  " + uc + ": " + (rep.get("error")
            or f"GP {rep.get('gp')} · เหรียญ {rep.get('coins')} · EFP {rep.get('efp')}"))
        _event({"type": "crow", "rep": rep, "done": d, "total": total})

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(chk, list(recs.items())):
            pass
    _save_name_cache(force=True)   # flush any debounced names resolved during this run
    _event({"type": "cdone", "total": total})
    _summary(log, "CHECK", [f"เช็ค {len(out)}/{total} บัญชี", f"โฟลเดอร์: {src}"])
    return {"module": "check", "results": out, "summary": {"total": total, "checked": len(out)}}


def _parse_records_text(txt):
    """Parse pasted Code: a JSON dict, a JSON list, or one JSON record per line."""
    txt = (txt or "").strip()
    if not txt:
        return []
    try:
        j = json.loads(txt)
        return [x for x in (j if isinstance(j, list) else [j]) if isinstance(x, dict)]
    except Exception:
        pass
    out = []
    for line in txt.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            j = json.loads(line)
            if isinstance(j, dict):
                out.append(j)
        except Exception:
            pass
    return out


def _genlink_records(params, log):
    """Collect account records from pasted text, explicit files (.txt/.dat), or a
    folder. Each needs user_id + auth_code (device_identifier/user_code optional)."""
    recs, seen = [], set()

    def _add(r):
        if not isinstance(r, dict):
            return
        uid, auth = r.get("user_id"), r.get("auth_code")
        if uid in (None, "") or not auth:
            return
        key = str(uid)
        if key in seen:
            return
        seen.add(key)
        recs.append(r)

    txt = (params or {}).get("text")
    if txt:
        for r in _parse_records_text(txt):
            _add(r)
    for fp in ((params or {}).get("files") or []):
        try:
            _add(json.load(open(fp, encoding="utf-8")))   # .dat is JSON, same as .txt
        except Exception as e:  # noqa: BLE001
            log(f"อ่านไฟล์ไม่ได้ {os.path.basename(str(fp))}: {e}")
    src = (params or {}).get("src_dir")
    if src and os.path.isdir(src):
        for _uc, r in _acc_records(src).items():
            _add(r)
    return recs


def _genlink(params, log):
    """KONAMI ID account-link URL generator. Unlimited, no cooldown — rides the
    user's existing session entitlement. Emits one 'glink' event per account."""
    import threading
    recs = _genlink_records(params, log)
    total = len(recs)
    if not total:
        log("ไม่พบบัญชี (วาง Code / เลือกไฟล์ .txt/.dat / โฟลเดอร์)")
        _event({"type": "gldone", "ok": 0, "total": 0})
        return {"ok": 0, "total": 0, "links": []}
    lang = (params or {}).get("lang") or "th"
    delay = float((params or {}).get("delay", 0) or 0)
    workers = max(1, min(64, int((params or {}).get("workers", 8) or 8)))
    gov = Governor(start=max(0.3, delay), lo=delay)
    log(f"genlink: {total} บัญชี · {workers} worker")
    cnt = {"ok": 0, "fail": 0}
    links = []
    idx = {"i": 0}
    lock = threading.Lock()
    _report(0, 0, total)

    def _next():
        with lock:
            if idx["i"] >= total:
                return None
            r = recs[idx["i"]]
            idx["i"] += 1
            return r

    def worker():
        c = GateClient()
        while not _stopped():
            a = _next()
            if a is None:
                return
            gov.wait()
            if _stopped():
                return
            uid, auth = a.get("user_id"), a.get("auth_code")
            devid = a.get("device_identifier")
            uc = a.get("user_code") or str(uid)
            c.user_code = str(uc)
            c.http.cookies.clear()
            try:
                lr = login(c, int(uid), auth, devid)
                sid = lr.get("session_id") if isinstance(lr, dict) else None
                if not sid:
                    res = lr.get("result") if isinstance(lr, dict) else "?"
                    if res in RETRYABLE or res == "ERR_NETWORK":
                        gov.on_throttle()
                    with lock:
                        cnt["fail"] += 1
                        log(f"  {uc}: login fail {res}")
                        _report(cnt["ok"], cnt["fail"], total)
                    _event({"type": "glink", "uc": uc, "ok": False, "err": f"login {res}"})
                    continue
                link = get_konami_link(c, int(uid), sid, devid, lang=lang)
                gov.on_success()
                if link.get("result") == "NOERR" and link.get("url"):
                    exp = int(link.get("expires") or 0)
                    with lock:
                        cnt["ok"] += 1
                        links.append({"user_code": uc, "user_id": uid, "url": link["url"],
                                      "expires": exp, "link_status": link.get("link_status")})
                        log(f"  {uc}: OK")
                        _report(cnt["ok"], cnt["fail"], total)
                    _event({"type": "glink", "uc": uc, "ok": True, "url": link["url"],
                            "expires": exp, "user_id": uid})
                else:
                    with lock:
                        cnt["fail"] += 1
                        log(f"  {uc}: link fail {link.get('result')}")
                        _report(cnt["ok"], cnt["fail"], total)
                    _event({"type": "glink", "uc": uc, "ok": False, "err": str(link.get("result"))})
            except Exception as e:  # noqa: BLE001
                gov.on_throttle()
                with lock:
                    cnt["fail"] += 1
                    log(f"  {uc}: ERROR {e}")
                    _report(cnt["ok"], cnt["fail"], total)
                _event({"type": "glink", "uc": uc, "ok": False, "err": str(e)})

    nthreads = min(workers, max(1, total))
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(nthreads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log(f"genlink เสร็จ: ok={cnt['ok']} fail={cnt['fail']}")
    _event({"type": "gldone", "ok": cnt["ok"], "total": total})
    return {"ok": cnt["ok"], "total": total, "links": links}


_DISPATCH = {"reroll": _reroll, "gacha": _gacha, "daily": _daily, "genlink": _genlink}


def run(brain: dict, session=None, params: dict | None = None):
    """Loader entrypoint. `session` is the live SessionCtx (heartbeat/lease)."""
    global _B, _LOG, _VERBOSE, _STOP, _PROGRESS, _EMIT, _MAINT_HIT
    _B = brain
    _MAINT_HIT = False
    module_id = (params or {}).get("module") or os.environ.get("REROLL_MODULE", "reroll")

    def log(msg):
        print(msg, flush=True)

    _LOG = log
    _VERBOSE = bool((params or {}).get("verbose"))
    _STOP = (params or {}).get("stop")
    _PROGRESS = (params or {}).get("progress")
    _EMIT = (params or {}).get("emit")

    action = (params or {}).get("action")
    if action == "merge":
        return _merge(params, log)
    if action == "scan":
        return _gacha_scan(params, log)
    if action == "check":
        return _check(params, log)
    if action == "rename":
        return _rename(params, log)

    fn = _DISPATCH.get(module_id)
    if not fn:
        raise RuntimeError(f"unknown module {module_id!r}")
    return fn(params, log)
