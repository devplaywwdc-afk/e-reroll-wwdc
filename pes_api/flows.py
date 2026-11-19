"""
High-level eFootball gate flows: create_user (reroll) + login + daily claim.
Built on client.GateClient / gate_crypto. Verified live 2026-11-11.
"""
import os
import sys
import json
import random
import string

sys.path.insert(0, os.path.dirname(__file__))
import gate_crypto as gc
import client as cl

HERE = os.path.dirname(__file__)
TEMPLATES = json.load(open(os.path.join(HERE, "templates.json"), encoding="utf-8"))

# device/eula constants recovered from this device's real create/login.
# (Reuse one valid (device_identifier, hash) pair; hash is the device hash from CREATE.)
DEVICE_ID = "ae2efa4abb162b4f353a2cbb3b06cd84345726ebcd129c1b92000e79f638ef38"
DEVICE_HASH = "097679ed613607ef3d17b04e51784568"   # CREATE_USER hash (device hash)
LOGIN_HASH = "e8bbc34f90e46bbbbb6ef7a7a74df3ba"     # static hash used by LOGIN & authed cmds
S_KEYWORD = TEMPLATES["CMD_LOGIN"]["s_keyword"]      # constant on this device

EULA_INFO = {
    "eula_ip_country_a3": "THA",
    "eula_select_country_a3": "THA",
    "eula_ip_state": "NONE",
    "eula_select_state": "",
    "eula_version": {
        "eula": "8.00",
        "eula_privacy_notice": "4.00",
        "eula_myclub": "6.00",
        "eula_japan_version": "8.00",
        "eula_myclub_japan": "7.00",
    },
    "eula_country_age_kind": "BIRTH_YEAR_MONTH",
    "eula_privacy_action": "CONFIRM",
}

SURVEY = [{"survey_id": 35, "answer_id": 2}, {"survey_id": 36, "answer_id": 4}]


DEFAULT_PREFIX = "j3xdr"
MAX_NAME = 15  # eFootball in-game name hard limit (16+ -> ERR_INVALIDARG)


def rand_name(prefix: str = DEFAULT_PREFIX):
    """In-game name = '<prefix>_<random>', total capped at 15 chars (server limit).
    prefix default 'j3xdr' -> 'j3xdr_' + 9 random letters."""
    prefix = (prefix or DEFAULT_PREFIX).strip()[:MAX_NAME - 2]  # leave room for '_' + >=1 char
    tail = MAX_NAME - len(prefix) - 1
    return f"{prefix}_" + "".join(random.choices(string.ascii_letters, k=max(1, tail)))


# Device fingerprints to spread accounts across (server does NOT bind hash to device_identifier,
# so a random device_identifier + the valid app hash is accepted — see docs).
_DEVICE_PROFILES = [
    ("SM-A526B", "Adreno (TM) 619", "SM7225"),
    ("SM-G991B", "Adreno (TM) 660", "SM8350"),
    ("SM-A546E", "Mali-G68", "s5e8835"),
    ("Pixel 7", "Mali-G710", "Tensor G2"),
    ("SM-S918B", "Adreno (TM) 740", "SM8550"),
    ("M2101K6G", "Mali-G96", "mt6877"),
    ("CPH2451", "Adreno (TM) 730", "SM8475"),
    ("2201116SG", "Adreno (TM) 730", "SM8450"),
]


def rand_device():
    """A fresh random device fingerprint (identifier + model/gpu/soc)."""
    model, gpu, soc = random.choice(_DEVICE_PROFILES)
    return {
        "device_identifier": os.urandom(32).hex(),  # like hex(widevine deviceUniqueId)
        "model_name": model,
        "gpu_name": gpu,
        "soc_name": soc,
        "os_version": random.choice(["13", "14", "15"]),
        "phy_mem_used_mib": random.randint(800, 1400),
        "phy_mem_available_mib": random.randint(2500, 5000),
    }


def _commons(cmd, user_id, session_id, hash_val):
    return {
        "msgid": cmd,
        "rqid": cl.new_rqid(),
        "user_id": user_id,
        "session_id": session_id,
        "my_platform": "ANDROID",
        "s_keyword": S_KEYWORD,
        "hash": hash_val,
        "client_version": "6.1.1",
        "platform": "ANDROID",
        "device": {"device_identifier": DEVICE_ID},
    }


# ---------------------------------------------------------------- reroll
def create_user(client: cl.GateClient, name: str | None = None,
                device: dict | None = None, prefix: str = DEFAULT_PREFIX) -> dict:
    """Create a fresh guest account (random device fingerprint by default).
    In-game name is '<prefix>_<random>' unless an explicit `name` is given.
    Returns the full CREATE_USER response. device_identifier is randomized per account;
    the app `hash` is reused (server does not bind it to the device)."""
    name = name or rand_name(prefix)
    dev = device or rand_device()
    # 1) name availability check (server expects it before create)
    chk = _commons("CMD_CHECK_STRING", 0, "", DEVICE_HASH)
    chk["device"] = {"device_identifier": dev["device_identifier"]}
    chk["str_list"] = [name]
    r = client.send("CMD_CHECK_STRING", chk)
    if r.get("result") != "NOERR":
        raise cl.GateError(f"CHECK_STRING failed: {r}")
    # 2) create
    p = _commons("CMD_CREATE_USER", 0, "", DEVICE_HASH)
    p["device"] = {"device_identifier": dev["device_identifier"]}
    p.update({
        "lang": "TH",
        "region": "REGION_ASIA",
        "pcspec": "NotImplement",
        "user_eula_season": "2025",
        "user_eula_info": EULA_INFO,
        "pes_user_info": {
            "user_id": 0, "user_name": name, "is_deleted": "NO", "platform": "ANDROID",
            "psn_user_id": {"account_id": "NotImplement", "online_id": "", "is_valid_cache": "NO"},
            "xuid": "NotImplement", "steamid": "NotImplement",
            "nsw_user_id": {"nsa_id": "NotImplement", "issuer_id": 0},
            "avatar": {
                "is_already_set": "NO", "player_id": 0, "special_id": 0,
                "background_id": 0, "special_id_background": 0,
                "is_enable_player": "YES", "is_enable_background": "YES",
                "material_key_player": "", "material_key_background": "",
            },
            "fameIdList": [],
            "user_banner": {"banner_id": {"unique_id": 0}, "is_valid": "NO"},
        },
        "cross_platform_option": "NO",
        "age": "2001-02-28",
        "os_version": dev["os_version"],
        "model_name": dev["model_name"],
        "gpu_name": dev["gpu_name"],
        "soc_name": dev["soc_name"],
        "survey_results": SURVEY,
        "user_age_kind": "ADULT",
        "is_rooting": "NO",
        "phy_mem_used_mib": dev["phy_mem_used_mib"],
        "phy_mem_available_mib": dev["phy_mem_available_mib"],
        "app_storage_used_mib": 29640, "app_storage_available_mib": 79976,
        "data_storage_used_mib": 29640, "data_storage_available_mib": 79976,
        "payment_store_link_send_info": {"country_code": ""},
        "kid_challenge_id": "",
        "is_savedata_existing": "NO",
    })
    resp = client.send("CMD_CREATE_USER", p)
    # remember the device for login (login reuses the same device_identifier)
    if isinstance(resp, dict) and "auth_code" in resp:
        resp["_device_identifier"] = dev["device_identifier"]
    return resp


# ---------------------------------------------------------------- login
def login(client: cl.GateClient, user_id: int, auth_code: str,
          device_identifier: str | None = None) -> dict:
    """Log an existing account in. Returns LOGIN response (incl session_id)."""
    t = dict(TEMPLATES["CMD_LOGIN"])
    t["rqid"] = cl.new_rqid()
    t["user_id"] = user_id
    t["auth_code"] = auth_code
    t["session_id"] = ""
    t["s_keyword"] = S_KEYWORD
    t["hash"] = LOGIN_HASH
    t["device"] = {"device_identifier": device_identifier or DEVICE_ID}
    return client.send("CMD_LOGIN", t)


def get_konami_link(client, user_id, session_id, lang="th") -> dict:
    """Get the KONAMI ID account-link URL for this account (pure API, no game UI).
    Returns {url, expires, link_status}. The URL lets a buyer bind the account to their own
    KONAMI ID at my.konami.net; it is short-lived (see `expires`)."""
    b = _commons("CMD_GET_IS_KONAMIID_LINK", user_id, session_id, LOGIN_HASH)
    b.update({
        "steam_id": "NotImplement", "xuid": "NotImplement", "account_id": "NotImplement",
        "platform": "ANDROID", "region": "REGION_ASIA",
        "is_needless_url": "NO", "is_need_info_url": "YES",
    })
    r = client.send("CMD_GET_IS_KONAMIID_LINK", b)
    url = r.get("mykonami_url", "")
    # the in-game webview uses a locale path (/th/...); insert it to match
    if url and lang and "/account-link/" in url and f"/{lang}/account-link/" not in url:
        url = url.replace("my.konami.net/account-link/", f"my.konami.net/{lang}/account-link/")
    return {
        "url": url,
        "expires": r.get("url_expiration_date", 0),
        "link_status": r.get("link_status"),
        "result": r.get("result"),
    }


def server_status(sample=None):
    """Probe the gate to tell real maintenance from 'game works but a feature is gated'.
    Pings an unauth command, and (if `sample` account given) login + mainmenu + agentlist.
    Returns {status, checked, client_version, checks:[{cmd,result,maintenance_end,show_info}]}."""
    import os as _os, datetime as _dt
    c = cl.GateClient(max_retries=0)
    checks = []

    def rec(name, r):
        d = {"cmd": name,
             "result": r.get("result") if isinstance(r, dict) else str(r),
             "maintenance_end": (r.get("maintenance_end_time") or None) if isinstance(r, dict) else None,
             "show_info": (r.get("show_info") or None) if isinstance(r, dict) else None}
        if d["maintenance_end"]:
            try:
                d["maintenance_end_str"] = _dt.datetime.fromtimestamp(int(d["maintenance_end"])).strftime("%d/%m %H:%M")
            except Exception:
                pass
        checks.append(d)
        return d

    try:
        b = _commons("CMD_CHECK_STRING", 0, "", DEVICE_HASH)
        b["device"] = {"device_identifier": _os.urandom(32).hex()}
        b["str_list"] = ["efb_" + "".join(random.choices(string.ascii_letters, k=6))]
        rec("CHECK_STRING", c.send("CMD_CHECK_STRING", b))
    except Exception as e:
        checks.append({"cmd": "CHECK_STRING", "result": f"EXC {type(e).__name__}: {e}"})

    if sample:
        try:
            lr = login(c, int(sample["user_id"]), sample["auth_code"], sample.get("device_identifier"))
            rec("LOGIN", lr)
            sid = lr.get("session_id")
            if sid:
                rec("MAINMENU", get_mainmenu(c, int(sample["user_id"]), sid))
                rec("AGENTLIST (กาชา)", c.send("CMD_GET_MYCLUB_AGENTLIST",
                    _commons("CMD_GET_MYCLUB_AGENTLIST", int(sample["user_id"]), sid, LOGIN_HASH)))
        except Exception as e:
            checks.append({"cmd": "LOGIN", "result": f"EXC {type(e).__name__}: {e}"})

    oks = [x for x in checks if x["result"] == "NOERR"]
    maint = [x for x in checks if x.get("maintenance_end") or x.get("show_info")]
    core = next((x for x in checks if x["cmd"] == "CHECK_STRING"), None)
    if core and core["result"] != "NOERR" and (core.get("maintenance_end") or "EXC" in str(core["result"])):
        status = "down"       # gate ปิดทั้งระบบ / ติดต่อไม่ได้
    elif maint:
        status = "partial"    # เล่นได้แต่บาง feature ปิด
    elif len(oks) == len(checks):
        status = "ok"
    else:
        status = "issue"      # บาง command error (ไม่ใช่ maintenance) — อาจ version-gate
    return {"status": status, "checked": _dt.datetime.now().strftime("%H:%M:%S"),
            "client_version": _commons("x", 0, "", "")["client_version"],
            "checks": checks}


def play_sugoroku(client, user_id, session_id):
    """Play today's Daily Game (sugoroku) once: GET board -> SET one SUCCESS roll.
    1 play/account/day; reward lands in the mailbox (claim separately). Returns a report.
    Verified payload: {sugoroku_result:'SUCCESS', fail_reason:3, kind:<GET.kind>, stage_id:300}."""
    g = client.send("CMD_GET_SUGOROKU", _commons("CMD_GET_SUGOROKU", user_id, session_id, LOGIN_HASH))
    if not isinstance(g, dict) or g.get("result") != "NOERR":
        return {"played": False, "reason": g.get("result") if isinstance(g, dict) else "err"}
    if g.get("is_sugoroku") != "YES":
        return {"played": False, "reason": "unavailable"}  # already played today / not active
    b = _commons("CMD_SET_SUGOROKU", user_id, session_id, LOGIN_HASH)
    b.update({"sugoroku_result": "SUCCESS", "fail_reason": 3, "kind": g.get("kind", 1), "stage_id": 300})
    r = client.send("CMD_SET_SUGOROKU", b)
    if not isinstance(r, dict) or r.get("result") != "NOERR":
        return {"played": False, "reason": r.get("result") if isinstance(r, dict) else "err"}
    pres = []
    for p in (r.get("get_squares_present_list") or []) + (r.get("get_go_around_present_list") or []):
        pres.append({"type": p.get("present_type"),
                     "name": (p.get("agent_info") or {}).get("title")
                             or (p.get("item_info") or {}).get("item_type") or p.get("name"),
                     "count": p.get("count")})
    return {"played": True, "before": r.get("before_location"), "after": r.get("after_current_location"),
            "around": r.get("after_go_around_num"), "presents": pres}


def get_mailbox(client, user_id, session_id):
    return client.send("CMD_GET_MAILBOX_INFO",
                       _commons("CMD_GET_MAILBOX_INFO", user_id, session_id, LOGIN_HASH))


def get_mainmenu(client, user_id, session_id):
    return client.send("CMD_GET_MAINMENU_INFO",
                       _commons("CMD_GET_MAINMENU_INFO", user_id, session_id, LOGIN_HASH))


def get_roster(login_resp):
    """Extract the owned-player roster from a LOGIN response (entry_info.gameplayer_list)."""
    gp = (login_resp.get("entry_info") or {}).get("gameplayer_list", []) if isinstance(login_resp, dict) else []
    out = []
    for p in gp:
        gid = p.get("gameplayer_id", {})
        out.append({
            "id": gid.get("id"),
            "serial": gid.get("serial"),
            "level": (p.get("level_info") or {}).get("level"),
            "level_limit": (p.get("level_info") or {}).get("level_limit"),
            "is_lock": p.get("is_lock") == "YES",
            "team_id": p.get("initial_base_team_id"),
            "uniform": p.get("uniform_number"),
            "type": p.get("type"),
        })
    return out


# currency names: GP (Game Points), eFootball Coins, eFootball Points (EFP)
def account_status(client, user_id, session_id, mailbox=None):
    """Live balances from the real game: gp, coins, efp (+ present count)."""
    mm = get_mainmenu(client, user_id, session_id)
    mb = mailbox if mailbox is not None else get_mailbox(client, user_id, session_id)
    gp = mm.get("gp", 0) if isinstance(mm, dict) else 0
    coins = (mm.get("coin_info") or {}).get("coin", 0) if isinstance(mm, dict) else 0
    efp = mb.get("total_efootballpoint_num", 0) if isinstance(mb, dict) else 0
    return {"gp": gp, "coins": coins, "efp": efp,
            "mailbox": len(mb.get("present_list", [])) if isinstance(mb, dict) else 0}


def claim_daily(client, user_id, session_id, retries: int = 6, wait: float = 3.0):
    """Claim everything in the mailbox (daily login bonus lands here).

    A brand-new account hasn't "settled" server-side: RECV_PRESENT may even ACK SUCCESS_ADDED
    and then roll back, so we DON'T trust the ack — we re-read the mailbox and only count a
    present as claimed once it has actually disappeared from the box. We also call
    GET_LOGIN_BONUS_INFO each round (the daily-login popup is what makes the bonus claimable),
    and retry until the box drains or `retries` runs out.
    Returns the claimed items (type/name/count) + whether any LOGIN_BONUS was among them."""
    import time as _t
    claimed = []
    had_login_bonus = False
    seen = {}  # present_id -> item dict (from the mailbox, before claim)
    for attempt in range(max(1, retries)):
        try:
            get_login_bonus(client, user_id, session_id)  # "opens" today's bonus
        except Exception:
            pass
        mb = get_mailbox(client, user_id, session_id)
        pres = mb.get("present_list", []) if isinstance(mb, dict) else []
        if any(p.get("reason") == "LOGIN_BONUS" for p in pres):
            had_login_bonus = True
        for p in pres:
            seen.setdefault(p.get("present_id"), p)
        if not pres:
            break  # box empty -> everything (if any) is done
        recv_present(client, user_id, session_id, [p["present_id"] for p in pres])
        _t.sleep(1.0)
        mb2 = get_mailbox(client, user_id, session_id)
        remaining = {p.get("present_id") for p in (mb2.get("present_list") or [])}
        gone = [pid for pid in [p["present_id"] for p in pres] if pid not in remaining]
        for pid in gone:
            i = seen.get(pid, {})
            claimed.append({"type": i.get("present_type"), "name": i.get("name"),
                            "count": i.get("count"), "title": i.get("title"),
                            "reason": i.get("reason")})
        if not remaining:
            break  # fully drained
        if attempt < retries - 1:
            _t.sleep(wait)  # still settling -> wait and try the leftovers again
    return {"result": "NOERR", "claimed": claimed, "had_login_bonus": had_login_bonus}


def claim_starter(user_id, auth_code, device_identifier=None, user_code=None,
                  tries: int = 7, wait: float = 4.0):
    """Durably claim a freshly-rerolled account's starter box + first daily-login bonus.

    A new account needs ~10-30s to "settle": before that, RECV_PRESENT drains the box only
    transiently (the server restores it). So we use a FRESH login each round and treat the
    claim as done only once a later round sees the mailbox empty with GP credited.
    Returns {claimed, gp, coins, efp, had_login_bonus}."""
    import time as _t
    claimed_all = []
    for _ in range(max(1, tries)):
        c = cl.GateClient(user_code=user_code)
        sid = login(c, user_id, auth_code, device_identifier).get("session_id")
        if not sid:
            _t.sleep(wait); continue
        st0 = account_status(c, user_id, sid)
        if st0["mailbox"] == 0 and (st0["gp"] > 0 or claimed_all):
            return {"claimed": claimed_all, "gp": st0["gp"], "coins": st0["coins"],
                    "efp": st0["efp"], "had_login_bonus": bool(claimed_all)}
        cd = claim_daily(c, user_id, sid, retries=1)
        if cd["claimed"]:
            claimed_all = cd["claimed"]
        _t.sleep(wait)
    c = cl.GateClient(user_code=user_code)
    sid = login(c, user_id, auth_code, device_identifier).get("session_id")
    st = account_status(c, user_id, sid) if sid else {"gp": 0, "coins": 0, "efp": 0}
    return {"claimed": claimed_all, "gp": st["gp"], "coins": st["coins"],
            "efp": st["efp"], "had_login_bonus": bool(claimed_all)}


def get_login_bonus(client, user_id, session_id):
    return client.send("CMD_GET_LOGIN_BONUS_INFO",
                       _commons("CMD_GET_LOGIN_BONUS_INFO", user_id, session_id, LOGIN_HASH))


_ENTRY_TPL = None


def _rewrite_serials(obj, new_uid: int):
    """Rewrite every gameplayer/coach serial (= old_uid*1e9 + offset) for new_uid."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "serial" and isinstance(v, int):
                obj[k] = new_uid * 10**9 + (v % 10**9)
            else:
                _rewrite_serials(v, new_uid)
    elif isinstance(obj, list):
        for v in obj:
            _rewrite_serials(v, new_uid)


def finish_setup(client: cl.GateClient, user_id: int, session_id: int):
    """Enter MyClub (pick the starter team) so a rerolled account is lobby-ready.
    eFootball has no forced playable tutorial — SET_MYCLUB_ENTRY_INFO is all that's needed."""
    global _ENTRY_TPL
    if _ENTRY_TPL is None:
        _ENTRY_TPL = json.load(open(os.path.join(HERE, "entry_barcelona.json"), encoding="utf-8"))
    # list teams (mimics the real flow; also validates session)
    tl = _commons("CMD_GET_USEABLE_TEAM_LIST", user_id, session_id, LOGIN_HASH)
    tl.update({"list_type": "BASE_TEAM_SELECT", "event_id": 0})
    client.send("CMD_GET_USEABLE_TEAM_LIST", tl)
    # build entry from the Barcelona template with this account's serials
    import copy
    p = copy.deepcopy(_ENTRY_TPL)
    p["rqid"] = cl.new_rqid()
    p["user_id"] = user_id
    p["session_id"] = session_id
    p["s_keyword"] = S_KEYWORD
    _rewrite_serials(p, user_id)
    return client.send("CMD_SET_MYCLUB_ENTRY_INFO", p)


def recv_present(client, user_id, session_id, present_ids: list[int]):
    """Claim mailbox presents by id. Uses the lean commons RECV_PRESENT expects."""
    body = {
        "msgid": "CMD_RECV_PRESENT",
        "rqid": cl.new_rqid(),
        "user_id": user_id,
        "session_id": session_id,
        "my_platform": "ANDROID",
        "s_keyword": S_KEYWORD,
        "present_list": [{"present_id": pid, "breakdown_present_id": 0} for pid in present_ids],
        "advertising_id": 0,
    }
    return client.send("CMD_RECV_PRESENT", body)
