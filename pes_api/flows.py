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


def rand_name():
    return "efb_" + "".join(random.choices(string.ascii_letters, k=10))


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
                device: dict | None = None) -> dict:
    """Create a fresh guest account (random device fingerprint by default).
    Returns the full CREATE_USER response. device_identifier is randomized per account;
    the app `hash` is reused (server does not bind it to the device)."""
    name = name or rand_name()
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


def get_mailbox(client, user_id, session_id):
    return client.send("CMD_GET_MAILBOX_INFO",
                       _commons("CMD_GET_MAILBOX_INFO", user_id, session_id, LOGIN_HASH))


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
