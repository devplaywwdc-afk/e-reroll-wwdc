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
import string
import struct
import sys
import time
import zlib

import requests
from Crypto.Cipher import AES

# ---------------------------------------------------------------- brain-backed crypto
_B: dict = {}


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
    raw = __import__("msgpack").packb(payload, use_bin_type=False)
    comp = zlib.compress(raw)
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
    return __import__("msgpack").unpackb(raw, raw=False, strict_map_key=False)


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
DEFAULT_UA = "PES/1.0 (ANDROID 14; {user_code}; SM-A526B; {ts}; 6.1.1; ; 11.1.1; )"


class GateError(Exception):
    pass


def new_rqid() -> int:
    return random.getrandbits(32)


class GateClient:
    def __init__(self, user_code="0", timeout=20, session=None,
                 max_retries=4, backoff=1.5):
        self.user_code = user_code
        self.timeout = timeout
        self.http = session or requests.Session()
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
            except requests.RequestException as e:
                last = {"result": "ERR_NETWORK", "_exc": type(e).__name__}
            res = last.get("result") if isinstance(last, dict) else None
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


def create_user(client, name=None, device=None, prefix=None):
    name = name or rand_name(prefix)
    dev = device or rand_device()
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


def get_mailbox(client, user_id, session_id):
    return client.send("CMD_GET_MAILBOX_INFO",
                       _commons("CMD_GET_MAILBOX_INFO", user_id, session_id, _B["login_hash"]))


def get_mainmenu(client, user_id, session_id):
    return client.send("CMD_GET_MAINMENU_INFO",
                       _commons("CMD_GET_MAINMENU_INFO", user_id, session_id, _B["login_hash"]))


def get_login_bonus(client, user_id, session_id):
    return client.send("CMD_GET_LOGIN_BONUS_INFO",
                       _commons("CMD_GET_LOGIN_BONUS_INFO", user_id, session_id, _B["login_hash"]))


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
        out.append({"agent_id": aid, "title": ag.get("title"),
                    "free": bool(free_opt), "needs_ad": needs_ad,
                    "count": free_opt.get("multiple_count") if free_opt else None,
                    "buy_type": bt if free_opt else None,
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


# ---------------------------------------------------------------- persistence
def _acc_dir(params):
    d = (params or {}).get("out_dir") or os.path.join(
        os.path.dirname(os.path.abspath(sys.argv[0])), "accounts")
    os.makedirs(d, exist_ok=True)
    return d


def save_account(acc_dir, user_code, auth_code, user_id, device_identifier=None):
    rec = {"auth_code": auth_code, "user_code": user_code,
           "user_id": user_id, "version": "2.0.0"}
    if device_identifier:
        rec["device_identifier"] = device_identifier
    path = os.path.join(acc_dir, f"{user_code}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
    return path


# ---------------------------------------------------------------- module entrypoints
def _reroll(params, log):
    count = int((params or {}).get("count", 1))
    prefix = (params or {}).get("prefix") or _B.get("default_prefix", "j3xdr")
    claim = bool((params or {}).get("claim", True))
    delay = float((params or {}).get("delay", 1.0))
    acc_dir = _acc_dir(params)
    made = []
    c = GateClient()
    for i in range(count):
        try:
            resp = create_user(c, prefix=prefix)
            if "auth_code" not in resp:
                log(f"[{i+1}/{count}] FAIL: {resp.get('result')}")
                time.sleep(delay); continue
            uc, auth = resp["user_code"], resp["auth_code"]
            uid = resp["pes_user_info"]["user_id"]
            devid = resp.get("_device_identifier")
            setup = "skipped"
            try:
                lr = login(c, uid, auth, devid)
                sid = lr.get("session_id")
                if sid:
                    setup = finish_setup(c, uid, sid).get("result", "?")
                    if claim:
                        claim_starter(uid, auth, devid, uc)
            except Exception as e:
                setup = f"err:{e}"
            save_account(acc_dir, uc, auth, uid, devid)
            made.append(uc)
            log(f"[{i+1}/{count}] OK {uc} uid={uid} myclub={setup}")
        except Exception as e:
            log(f"[{i+1}/{count}] ERROR: {e}")
        time.sleep(delay + random.random())
    log(f"created {len(made)}/{count} -> {acc_dir}")
    return {"module": "reroll", "created": made, "out_dir": acc_dir}


def _gacha(params, log):
    """Open free packs on saved accounts (filter by wanted player ids)."""
    acc_dir = _acc_dir(params)
    only = int((params or {}).get("agent", 0))
    wanted_ids = {int(x) for x in (params or {}).get("wanted_ids", [])}
    results = []
    for fn in sorted(f for f in os.listdir(acc_dir) if f.endswith(".txt")):
        a = json.load(open(os.path.join(acc_dir, fn), encoding="utf-8"))
        uc = a["user_code"]
        try:
            c = GateClient(user_code=uc)
            lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
            sid = lr.get("session_id")
            if not sid:
                log(f"{uc}: login failed"); continue
            drawn = draw_all_free(c, int(a["user_id"]), sid, only_agent=only)
            hits = [p["id"] for p in drawn if not wanted_ids or p["id"] in wanted_ids]
            log(f"{uc}: drawn={len(drawn)} hits={len(hits)}")
            results.append({"user_code": uc, "drawn": len(drawn), "hits": hits})
        except Exception as e:
            log(f"{uc}: ERROR {e}")
        time.sleep(1.0)
    return {"module": "gacha", "results": results}


def _daily(params, log):
    acc_dir = _acc_dir(params)
    accs = [json.load(open(os.path.join(acc_dir, f), encoding="utf-8"))
            for f in sorted(os.listdir(acc_dir)) if f.endswith(".txt")]
    log(f"daily pass: {len(accs)} accounts")
    ok = 0
    for a in accs:
        uc, uid, auth = a["user_code"], a["user_id"], a["auth_code"]
        c = GateClient(user_code=uc)
        try:
            lr = login(c, uid, auth, a.get("device_identifier"))
            if "session_id" not in lr:
                log(f"  {uc}: LOGIN fail {lr.get('result')}"); continue
            sid = lr["session_id"]
            mb = get_mailbox(c, uid, sid)
            ids = [p["present_id"] for p in mb.get("present_list", [])] if isinstance(mb, dict) else []
            claimed = 0
            if ids:
                rr = recv_present(c, uid, sid, ids)
                claimed = len(rr.get("receive_result_list", [])) if rr.get("result") == "NOERR" else 0
            log(f"  {uc}: login OK mailbox={len(ids)} claimed={claimed}")
            ok += 1
        except Exception as e:
            log(f"  {uc}: ERROR {e}")
        time.sleep(1.0)
    log(f"done: {ok}/{len(accs)} logged in")
    return {"module": "daily", "ok": ok, "total": len(accs)}


_DISPATCH = {"reroll": _reroll, "gacha": _gacha, "daily": _daily}


def run(brain: dict, session=None, params: dict | None = None):
    """Loader entrypoint. `session` is the live SessionCtx (heartbeat/lease)."""
    global _B
    _B = brain
    module_id = (params or {}).get("module") or os.environ.get("REROLL_MODULE", "reroll")

    def log(msg):
        print(msg, flush=True)

    fn = _DISPATCH.get(module_id)
    if not fn:
        raise RuntimeError(f"unknown module {module_id!r}")
    return fn(params, log)
