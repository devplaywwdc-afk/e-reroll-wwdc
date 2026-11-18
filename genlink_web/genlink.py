"""
eFootball 11.1.1 — KONAMI ID account-link URL generator (pure API, no game UI).

Self-contained service entrypoint for the web backend. Given a rerolled account's
credentials (user_id, auth_code, device_identifier) it logs in and returns the
short-lived my.konami.net account-link URL that a buyer uses to bind the account
to their own KONAMI ID.

    from genlink import generate_link
    r = generate_link(user_id=2055734095,
                      auth_code="f807b34d81cfaf6f3485b955429b8421ed248e74",
                      device_identifier="ae2efa4abb162b4f353a2cbb3b06cd84345726ebcd129c1b92000e79f638ef38")
    # -> {"ok": True, "url": "https://my.konami.net/th/account-link/...",
    #     "expires": 1763400000, "expires_in": 1200, "link_status": "...", "result": "NOERR"}

The URL lives ~20 min (`expires` is a unix timestamp; `expires_in` is seconds left).
Requesting again returns a fresh URL at no extra cost to the caller — but the gate
only hands out a new URL once the previous one has expired (enforce the 20-min
cooldown in your own layer; see SPEC).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import client as cl
import gate_crypto as gc


# ---- constants recovered from this device's real create/login (11.1.1) ----
LOGIN_HASH = "e8bbc34f90e46bbbbb6ef7a7a74df3ba"   # static hash used by LOGIN & authed cmds
S_KEYWORD = "ok\x04~dR\x18\\"                      # device-constant login keyword
DEFAULT_DEVICE_ID = "ae2efa4abb162b4f353a2cbb3b06cd84345726ebcd129c1b92000e79f638ef38"


def _commons(cmd, user_id, session_id, hash_val, device_identifier):
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
        "device": {"device_identifier": device_identifier},
    }


def login(client, user_id, auth_code, device_identifier):
    """Log an existing account in. Returns LOGIN response (incl session_id)."""
    t = _commons("CMD_LOGIN", user_id, "", LOGIN_HASH, device_identifier)
    t.update({
        "auth_code": auth_code,
        "lang": "TH",
        "country_code": [],
        "region": "REGION_ASIA",
        "pcspec": "NotImplement",
        "cross_platform_option": "NO",
        "is_disable_sharing": "NO",
        "os_version": "14",
        "model_name": "SM-A526B",
        "gpu_name": "Adreno (TM) 619",
        "soc_name": "SM7225",
        "is_rooting": "YES",
        "phy_mem_used_mib": 949,
        "phy_mem_available_mib": 3079,
        "app_storage_used_mib": 29647,
        "app_storage_available_mib": 79969,
        "data_storage_used_mib": 29647,
        "data_storage_available_mib": 79969,
        "payment_store_link_send_info": {"country_code": ""},
        "is_savedata_existing": "NO",
    })
    return client.send("CMD_LOGIN", t)


def get_konami_link(client, user_id, session_id, device_identifier, lang="th"):
    """Get the KONAMI ID account-link URL. Returns {url, expires, link_status, result}."""
    b = _commons("CMD_GET_IS_KONAMIID_LINK", user_id, session_id, LOGIN_HASH, device_identifier)
    b.update({
        "steam_id": "NotImplement", "xuid": "NotImplement", "account_id": "NotImplement",
        "platform": "ANDROID", "region": "REGION_ASIA",
        "is_needless_url": "NO", "is_need_info_url": "YES",
    })
    r = client.send("CMD_GET_IS_KONAMIID_LINK", b)
    url = r.get("mykonami_url", "")
    # in-game webview uses a locale path (/th/...); insert it to match
    if url and lang and "/account-link/" in url and f"/{lang}/account-link/" not in url:
        url = url.replace("my.konami.net/account-link/", f"my.konami.net/{lang}/account-link/")
    return {
        "url": url,
        "expires": r.get("url_expiration_date", 0),
        "link_status": r.get("link_status"),
        "result": r.get("result"),
    }


def generate_link(user_id, auth_code, device_identifier=None, lang="th", timeout=20):
    """Full flow: login -> fetch KONAMI account-link URL.

    Returns a dict:
      ok           bool    True only when result == "NOERR" and a url is present
      url          str     the my.konami.net account-link URL (empty on failure)
      expires      int     unix timestamp the URL expires at (0 on failure)
      expires_in   int     seconds left until expiry (<=0 means expired)
      link_status  str     gate's link status
      result       str     gate result code ("NOERR" on success)
      error        str     present only when ok is False (reason)

    Does NOT raise on gate errors — always returns the dict so the web layer can
    refund tokens and surface a clean message. Only raises on programmer error.
    """
    device_identifier = device_identifier or DEFAULT_DEVICE_ID
    c = cl.GateClient(user_code=str(user_id), timeout=timeout)

    lr = login(c, int(user_id), auth_code, device_identifier)
    sid = lr.get("session_id") if isinstance(lr, dict) else None
    if not sid:
        return {"ok": False, "url": "", "expires": 0, "expires_in": 0,
                "link_status": None, "result": (lr or {}).get("result"),
                "error": f"login failed: {lr}"}

    link = get_konami_link(c, int(user_id), sid, device_identifier, lang=lang)
    if link.get("result") != "NOERR" or not link.get("url"):
        return {"ok": False, "url": link.get("url", ""), "expires": link.get("expires", 0),
                "expires_in": 0, "link_status": link.get("link_status"),
                "result": link.get("result"), "error": f"link fetch failed: {link}"}

    expires = int(link["expires"]) or 0
    return {"ok": True, "url": link["url"], "expires": expires,
            "expires_in": max(0, expires - int(time.time())),
            "link_status": link.get("link_status"), "result": "NOERR"}


if __name__ == "__main__":
    # smoke test: python genlink.py <user_id> <auth_code> [device_identifier]
    uid, auth = sys.argv[1], sys.argv[2]
    dev = sys.argv[3] if len(sys.argv) > 3 else None
    import json
    print(json.dumps(generate_link(uid, auth, dev), ensure_ascii=False, indent=2))
