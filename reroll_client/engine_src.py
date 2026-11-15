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
                if _VERBOSE:
                    rc = last.get("result") if isinstance(last, dict) else "?"
                    _emit(f"[{r.status_code}] {cmd} -> {rc}")
            except requests.RequestException as e:
                last = {"result": "ERR_NETWORK", "_exc": type(e).__name__}
                if _VERBOSE:
                    _emit(f"[net] {cmd} -> ERR_NETWORK ({type(e).__name__})")
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


# --- efootbase enrichment (public player data: name / overall / art image) ---
_EFB_CACHE: dict = {}


def efb_lookup(pid, timeout=7):
    import re
    import html as _html
    key = str(pid)
    if key in _EFB_CACHE:
        return _EFB_CACHE[key]
    info = {"id": key, "name": None, "overall": None, "position": None, "art": None}
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
            arts = re.findall(
                r"cdn\.assets\.efootbase\.com/eFPlayers/players/\d+/v2/art-[0-9a-f]+\.webp", h)
            if arts:
                info["art"] = "https://" + arts[0]
    except Exception:
        pass
    _EFB_CACHE[key] = info
    return info


def efb_many(ids, workers=12):
    import concurrent.futures as cf
    out = {}
    todo = [str(i) for i in ids if str(i) not in _EFB_CACHE]
    for i in ids:
        if str(i) in _EFB_CACHE:
            out[str(i)] = _EFB_CACHE[str(i)]
    if todo:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for info in ex.map(efb_lookup, todo):
                out[info["id"]] = info
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
        pickup = [card(i) for i in pickup_ids if i]
        seen = set(pickup_ids)
        more = [card(x["id"]) for x in (a.get("pickup_cardtype_list") or [])
                if x.get("id") and x["id"] not in seen]
        allp = pickup + more
        allp.sort(key=lambda x: (x.get("overall") or 0), reverse=True)
        if allp:
            packs.append({"agent_id": aid, "title": a.get("title"), "players": allp})
    return packs


# ---------------------------------------------------------------- persistence
# EPIC/legendary highest; falls back to overall when rarities tie or are unknown.
_RARITY_RANK = {"EPIC": 4, "LEGENDARY": 4, "HIGHLIGHT": 3, "FEATURED": 3,
                "SPECIAL": 3, "RARE": 2, "NORMAL": 1, "STANDARD": 1}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


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


def _acc_records(folder):
    """Read every saved account record in a folder (any .txt/.dat), keyed by user_code.
    Filename scheme is irrelevant — the record JSON carries user_code."""
    recs = {}
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return recs
    for fn in names:
        if not fn.lower().endswith((".txt", ".dat")):
            continue
        fp = os.path.join(folder, fn)
        try:
            r = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        uc = r.get("user_code")
        if uc:
            r["_file"] = fp
            recs[uc] = r
    return recs


def _epic(players):
    """The standout card: highest rarity, then overall."""
    if not players:
        return None
    return sorted(players, key=lambda p: (_RARITY_RANK.get(str(p.get("rarity") or "").upper(), 0),
                                          p.get("overall") or 0), reverse=True)[0]


def _filename(scheme, rec, params):
    uc = _san(rec.get("user_code"), 24)
    coins = rec.get("coins", 0)
    players = rec.get("players") or []
    if scheme == "daily":
        if (params or {}).get("daily_show_players") and players:
            ep = _epic(players)
            base = f"{_san(ep.get('name'))}_{coins}_{uc}"
        else:
            base = f"{coins}_{uc}"
    elif scheme == "gacha":
        names = "+".join(_san(p.get("name"), 18) for p in players) if players else "none"
        base = f"{_san(names, 60)}_{coins}_{uc}"
    elif scheme == "merge":
        ep = _epic(players)
        base = f"{_san(ep.get('name')) if ep else 'none'}(epic)_coin{coins}_{uc}"
    else:  # reroll (+ fallback)
        base = uc
    return base + _ext(params)


def write_record(folder, rec, scheme, params, replace_old_uc=True):
    """Write an account record; delete any previous file for the same user_code so
    one account never leaves stale copies behind when it gets re-named."""
    os.makedirs(folder, exist_ok=True)
    rec = {k: v for k, v in rec.items() if k != "_file"}
    rec.setdefault("version", "2.1.0")
    path = os.path.join(folder, _filename(scheme, rec, params))
    if replace_old_uc:
        uc = rec.get("user_code")
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
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False))
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
    import concurrent.futures as cf
    import threading
    count = int((params or {}).get("count", 1))
    workers = max(1, min(64, int((params or {}).get("workers", 1) or 1)))
    prefix = (params or {}).get("prefix") or _B.get("default_prefix", "j3xdr")
    claim = bool((params or {}).get("claim", True))
    out = _out_dir(params)
    made, errors = [], []
    lock = threading.Lock()
    counter = {"n": 0}
    slots = {}
    t0 = time.time()

    def _wid():
        with lock:   # stable, unique 0..workers-1 slot per pool thread (no race)
            return slots.setdefault(threading.get_ident(), len(slots))

    def _stats():
        dt = max(0.001, time.time() - t0)
        ok = len(made)
        _event({"type": "stats", "ok": ok, "fail": len(errors), "elapsed": int(dt),
                "rate": round(ok / dt, 2), "perday": int(ok / dt * 86400),
                "running": not _stopped()})

    def one(_slot):
        if _stopped():
            return
        w = _wid()
        with lock:
            counter["n"] += 1
            i = counter["n"]
        try:
            c = GateClient()   # own session per worker (thread-safe)
            resp = create_user(c, prefix=prefix)
            if not isinstance(resp, dict) or "auth_code" not in resp:
                msg = (resp or {}).get("result", "?") if isinstance(resp, dict) else "?"
                with lock:
                    errors.append(f"#{i} CREATE_USER {msg}")
                log(f"[{i}/{count}] FAIL: {msg}")
                _event({"type": "log", "w": w, "lvl": "err", "t": f"[{i}] FAIL {msg}"})
                return
            uc, auth = resp["user_code"], resp["auth_code"]
            uid = resp["pes_user_info"]["user_id"]
            devid = resp.get("_device_identifier")
            coins = gp = 0
            setup = "skipped"
            try:
                lr = login(c, uid, auth, devid)
                sid = lr.get("session_id")
                if sid:
                    setup = finish_setup(c, uid, sid).get("result", "?")
                    if claim and not _stopped():
                        cs = claim_starter(uid, auth, devid, uc)
                        coins, gp = cs.get("coins", 0), cs.get("gp", 0)
            except Exception as e:
                setup = f"err:{e}"
            rec = {"user_code": uc, "user_id": uid, "auth_code": auth,
                   "device_identifier": devid, "coins": coins, "gp": gp,
                   "players": [], "module": "reroll"}
            with lock:
                write_record(out, rec, "reroll", params)
                made.append(uc)
            log(f"[{i}/{count}] OK {uc} uid={uid} coins={coins} myclub={setup}")
            _event({"type": "log", "w": w, "lvl": "ok", "t": f"[{i}] OK {uc} coins={coins}"})
        except Exception as e:
            with lock:
                errors.append(f"#{i} {type(e).__name__}: {e}")
            log(f"[{i}/{count}] ERROR: {e}")
            _event({"type": "log", "w": w, "lvl": "err", "t": f"[{i}] ERR {e}"})
        finally:
            _report(len(made), len(errors), count)
            _stats()

    _report(0, 0, count)
    _event({"type": "started", "workers": workers})
    log(f"เริ่ม reroll {count} บัญชี · {workers} worker พร้อมกัน")
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(one, range(count)):
            pass
    stopped = _stopped()
    lines = [f"สร้างสำเร็จ {len(made)}/{count}  ล้มเหลว {len(errors)}"]
    if stopped:
        lines.append("** หยุดโดยผู้ใช้ **")
    lines.append(f"โฟลเดอร์: {out}")
    if errors:
        lines.append(f"error ({min(len(errors), 10)} ตัวอย่างล่าสุด):")
        lines += ["- " + e for e in errors[-10:]]
    _summary(log, "REROLL", lines)
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
    recs = _acc_records(src)
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
    src = _src_dir(params)
    only = int((params or {}).get("agent", 0))
    wanted_order = [int(x) for x in (params or {}).get("wanted_ids", [])]
    wanted_set = set(wanted_order)
    recs = _acc_records(src)
    results, hit_accounts, fails = [], 0, 0
    log(f"gacha pass: {len(recs)} accounts @ {src}")
    _report(0, 0, len(recs))
    for uc, a in recs.items():
        if _stopped():
            log("** หยุดโดยผู้ใช้ **"); break
        try:
            c = GateClient(user_code=uc)
            lr = login(c, int(a["user_id"]), a["auth_code"], a.get("device_identifier"))
            sid = lr.get("session_id")
            if not sid:
                log(f"{uc}: login failed"); fails += 1
                _report(hit_accounts, fails, len(recs)); continue
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
            rec = dict(a); rec.pop("_file", None)
            rec.update({"coins": st["coins"], "gp": st["gp"],
                        "players": ordered, "module": "gacha"})
            write_record(src, rec, "gacha", params)
            if ordered:
                hit_accounts += 1
            log(f"{uc}: drawn={len(drawn)} hits={len(ordered)} coins={st['coins']}")
            results.append({"user_code": uc, "drawn": len(drawn),
                            "hits": [p["id"] for p in ordered]})
        except Exception as e:
            log(f"{uc}: ERROR {e}"); fails += 1
        _report(hit_accounts, fails, len(recs))
        time.sleep(1.0)
    _summary(log, "GACHA", [f"บัญชีที่ได้นักเตะเป้าหมาย {hit_accounts}/{len(recs)}  "
                            f"(พลาด {fails})", f"โฟลเดอร์: {src}"])
    return {"module": "gacha", "results": results,
            "summary": {"hit_accounts": hit_accounts, "total": len(recs)}}


def _daily(params, log):
    src = _src_dir(params)
    recs = _acc_records(src)
    show_players = bool((params or {}).get("daily_show_players"))
    log(f"daily pass: {len(recs)} accounts @ {src}")
    ok = claimed_total = fails = 0
    _report(0, 0, len(recs))
    for uc, a in recs.items():
        if _stopped():
            log("** หยุดโดยผู้ใช้ **"); break
        uid, auth = a["user_id"], a["auth_code"]
        c = GateClient(user_code=uc)
        try:
            lr = login(c, uid, auth, a.get("device_identifier"))
            if "session_id" not in lr:
                log(f"  {uc}: LOGIN fail {lr.get('result')}"); fails += 1
                _report(ok, fails, len(recs)); continue
            sid = lr["session_id"]
            mb = get_mailbox(c, uid, sid)
            ids = [p["present_id"] for p in mb.get("present_list", [])] if isinstance(mb, dict) else []
            claimed = 0
            if ids:
                rr = recv_present(c, uid, sid, ids)
                claimed = len(rr.get("receive_result_list", [])) if rr.get("result") == "NOERR" else 0
            st = account_status(c, uid, sid)
            rec = dict(a); rec.pop("_file", None)
            rec.update({"coins": st["coins"], "gp": st["gp"], "efp": st["efp"],
                        "module": "daily"})
            write_record(src, rec, "daily", params)
            pl = ""
            if show_players and rec.get("players"):
                pl = " | นักเตะ: " + ", ".join(p.get("name", "?") for p in rec["players"][:3])
            log(f"  {uc}: OK coins={st['coins']} claimed={claimed}{pl}")
            ok += 1; claimed_total += claimed
        except Exception as e:
            log(f"  {uc}: ERROR {e}"); fails += 1
        _report(ok, fails, len(recs))
        time.sleep(1.0)
    _summary(log, "DAILY", [f"ล็อกอินสำเร็จ {ok}/{len(recs)}  พลาด {fails}  "
                            f"รับของรวม {claimed_total} ชิ้น", f"โฟลเดอร์: {src}"])
    return {"module": "daily", "ok": ok, "total": len(recs), "claimed": claimed_total,
            "summary": {"ok": ok, "total": len(recs), "claimed": claimed_total}}


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


def _check(params, log):
    """Audit saved accounts: login each + report currency (GP/coins/EFP). Emits
    rows to the dashboard check table. Read-only — never writes the records."""
    import concurrent.futures as cf
    import threading
    src = _src_dir(params)
    recs = _acc_records(src)
    total = len(recs)
    workers = max(1, min(32, int((params or {}).get("workers", 5) or 5)))
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
                rep.update({"gp": st["gp"], "coins": st["coins"], "efp": st["efp"],
                            "mailbox": st["mailbox"], "players": len(a.get("players") or [])})
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
    _event({"type": "cdone", "total": total})
    _summary(log, "CHECK", [f"เช็ค {len(out)}/{total} บัญชี", f"โฟลเดอร์: {src}"])
    return {"module": "check", "results": out, "summary": {"total": total, "checked": len(out)}}


_DISPATCH = {"reroll": _reroll, "gacha": _gacha, "daily": _daily}


def run(brain: dict, session=None, params: dict | None = None):
    """Loader entrypoint. `session` is the live SessionCtx (heartbeat/lease)."""
    global _B, _LOG, _VERBOSE, _STOP, _PROGRESS, _EMIT
    _B = brain
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

    fn = _DISPATCH.get(module_id)
    if not fn:
        raise RuntimeError(f"unknown module {module_id!r}")
    return fn(params, log)
