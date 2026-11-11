"""
eFootball MyClub gacha (Agent/Scout) — pure API.

The game's gacha packs are "Agents". We never hardcode pack ids: CMD_GET_MYCLUB_AGENTLIST
is scanned live and an agent is considered FREE when one of its price options costs
0 coin AND 0 GP. CMD_USE_MYCLUB_AGENT draws it; the response's gameplayer_list holds the
pulled players. Request schema (recovered from libUE4 CmdUseMyclubAgent serializer):
  {agent_id, buy_type, gameplayer_id:{id,serial}, draft_id, count, void_player_ticket_id, advertising_id}
buy_type is an enum string: BUY_BY_COIN / BUY_BY_GP / BUY_BY_DRAFT / BUY_BY_TICKET / BUY_BY_VOID_TICKET.
"""
import os
import re
import sys
import shutil
import unicodedata

sys.path.insert(0, os.path.dirname(__file__))
import flows
import client as cl
import accounts
import efootbase

HITS_DIR = os.path.join(os.path.dirname(__file__), "gacha_hits")


# ---------------------------------------------------------------- scan
def _buy_type(price):
    if price.get("can_use_coin") == "YES":
        return "BUY_BY_COIN"
    if price.get("can_use_gp") == "YES":
        return "BUY_BY_GP"
    return "BUY_BY_COIN"  # ad/other free fallback


def scan_agents(client, uid, sid):
    """List all gacha agents and flag the free ones (0 coin & 0 GP for some count)."""
    b = flows._commons("CMD_GET_MYCLUB_AGENTLIST", uid, sid, flows.LOGIN_HASH)
    r = client.send("CMD_GET_MYCLUB_AGENTLIST", b)
    if not isinstance(r, dict) or r.get("result") != "NOERR":
        return {"all": [], "free": [], "free_count": 0,
                "error": r.get("result") if isinstance(r, dict) else "bad response"}
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
        # an "ad" agent needs a rewarded-ad token we can't mint -> free but not auto-drawable
        needs_ad = bool(free_opt) and pr.get("can_use_coin") == "NO" and pr.get("can_use_gp") == "NO"
        out.append({
            "agent_id": aid, "title": ag.get("title"), "sub": ag.get("subcategory"),
            "free": bool(free_opt), "needs_ad": needs_ad,
            "count": free_opt.get("multiple_count") if free_opt else None,
            "buy_type": _buy_type(pr) if free_opt else None,
            "limit": limit, "used": used, "remaining": max(0, limit - used),
            "expires": ag.get("expiration_date"),
        })
    free = [a for a in out if a["free"] and a["remaining"] > 0 and not a["needs_ad"]]
    return {"all": out, "free": free, "free_count": len(free)}


# ---------------------------------------------------------------- draw
def use_agent(client, uid, sid, agent_id, count, buy_type="BUY_BY_COIN"):
    """Draw an agent `count` times. Returns {result, players:[{id,serial,level_limit,rarity}]}."""
    b = flows._commons("CMD_USE_MYCLUB_AGENT", uid, sid, flows.LOGIN_HASH)
    b.update({
        "agent_id": agent_id, "buy_type": buy_type,
        "gameplayer_id": {"id": 0, "serial": 0},
        "draft_id": 0, "count": count,
        "void_player_ticket_id": 0, "advertising_id": 0,
    })
    r = client.send("CMD_USE_MYCLUB_AGENT", b)
    players = []
    if isinstance(r, dict):
        for e in r.get("gameplayer_list", []):
            g = e.get("gameplayer", {})
            gid = g.get("gameplayer_id", {})
            players.append({"id": gid.get("id"), "serial": gid.get("serial"),
                            "level_limit": (g.get("level_info") or {}).get("level_limit"),
                            "rarity": g.get("default_rarity")})
    return {"result": r.get("result") if isinstance(r, dict) else "ERR", "players": players}


def draw_all_free(client, uid, sid, scan=None, only_agent=0):
    """Open free draws the account has. only_agent>0 limits to that one agent_id.
    Returns the list of pulled players."""
    scan = scan or scan_agents(client, uid, sid)
    drawn = []
    for a in scan["free"]:
        if only_agent and a["agent_id"] != only_agent:
            continue
        for _ in range(a["remaining"]):
            res = use_agent(client, uid, sid, a["agent_id"], a["count"], a["buy_type"])
            if res["result"] != "NOERR":
                break
            for p in res["players"]:
                p["from_agent"] = a["title"]
            drawn += res["players"]
    return drawn


# ---------------------------------------------------------------- wanted filter + folders
def slug(name):
    """'Jude Bellingham' -> 'jude_bellingham' (ascii, lowercase, _-separated)."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-zA-Z0-9]+", "_", n).strip("_").lower()
    return n or "unknown"


def match_wanted(drawn_names, wanted):
    """Return the DISTINCT wanted players found among the drawn names, in `wanted` order.
    Case/space/accents-insensitive substring match (so 'Bellingham' matches 'Jude Bellingham')."""
    drawn_slugs = {slug(n) for n in drawn_names if n}
    hits = []
    for w in wanted:
        ws = slug(w)
        if any(ws == d or ws in d or d in ws for d in drawn_slugs):
            if w not in hits:
                hits.append(w)
    return hits


def folder_for(hits):
    """1 hit -> 'jude_bellingham'; 2+ -> '2_jude_bellingham_lamine_yamal'."""
    parts = [slug(h) for h in hits]
    if len(parts) == 1:
        return parts[0]
    return f"{len(parts)}_" + "_".join(parts)


def sort_account(user_code, hits):
    """Copy the account's .txt into gacha_hits/<folder>/ for each matched-set. Returns folder."""
    if not hits:
        return None
    folder = folder_for(hits)
    dst_dir = os.path.join(HITS_DIR, folder)
    os.makedirs(dst_dir, exist_ok=True)
    src = os.path.join(accounts.ACC_DIR, f"{user_code}.txt")
    dst = os.path.join(dst_dir, f"{user_code}.txt")
    try:
        if os.path.exists(src):
            shutil.copy2(src, dst)
        else:  # uploaded account not on disk -> write a minimal record
            pass
    except Exception:
        pass
    return folder


# ---------------------------------------------------------------- one account end-to-end
def run_account(acct, wanted, agent_id=0):
    """Login, open free packs (all, or only agent_id), enrich names, filter by `wanted`,
    sort into folder. Returns a report dict."""
    uc = acct["user_code"]
    try:
        c = cl.GateClient(user_code=uc)
        lr = flows.login(c, int(acct["user_id"]), acct["auth_code"], acct.get("device_identifier"))
        sid = lr.get("session_id")
        if not sid:
            return {"user_code": uc, "error": "login failed"}
        scan = scan_agents(c, int(acct["user_id"]), sid)
        drawn = draw_all_free(c, int(acct["user_id"]), sid, scan, only_agent=agent_id)
        info = efootbase.lookup_many([p["id"] for p in drawn if p["id"]])
        for p in drawn:
            e = info.get(str(p["id"]), {})
            p["name"] = e.get("name")
            p["overall"] = e.get("overall")
            p["position"] = e.get("position")
            p["art"] = e.get("art")
        names = [p.get("name") for p in drawn]
        hits = match_wanted(names, wanted) if wanted else []
        folder = sort_account(uc, hits) if hits else None
        drawn.sort(key=lambda x: (x.get("overall") or 0), reverse=True)
        return {"user_code": uc, "free_count": scan["free_count"], "drawn": len(drawn),
                "players": drawn, "hits": hits, "folder": folder}
    except Exception as e:
        return {"user_code": uc, "error": f"{type(e).__name__}: {e}"}
