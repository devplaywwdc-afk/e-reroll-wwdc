"""
Enrich a game player id with public data from efootbase.com (name, overall, position,
card name, card-art URL). efootbase accepts the game's base player id directly
(https://efootbase.com/players/<game_id> -> player page), so no id mapping is needed.

Results are cached to disk (public data; one fetch per id, reused forever).
"""
import os
import re
import html
import json
import threading

import requests

HERE = os.path.dirname(__file__)
CACHE_FILE = os.path.join(HERE, "efootbase_cache.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_lock = threading.Lock()
_cache = {}
if os.path.exists(CACHE_FILE):
    try:
        _cache = json.load(open(CACHE_FILE, encoding="utf-8"))
    except Exception:
        _cache = {}

_sess = requests.Session()
_sess.headers["User-Agent"] = UA


def _save():
    try:
        tmp = CACHE_FILE + ".tmp"
        json.dump(_cache, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def lookup(player_id, timeout=12):
    """Return {id, name, overall, position, card, art} for a game player id (cached)."""
    key = str(player_id)
    with _lock:
        if key in _cache:
            c = _cache[key]
            for k in ("name", "card"):  # fix entries cached before html-unescape
                if isinstance(c.get(k), str) and "&#" in c[k]:
                    c[k] = html.unescape(c[k])
            return c
    info = {"id": key, "name": None, "overall": None, "position": None, "card": None, "art": None}
    try:
        url = f"https://efootbase.com/players/{key}"
        r = _sess.get(url, timeout=timeout, allow_redirects=False)
        # efootbase sends a duplicated Location header (".../x, .../x") that breaks
        # requests' auto-redirect — resolve it manually, taking the first URL.
        hops = 0
        while r.status_code in (301, 302, 307, 308) and hops < 3:
            loc = (r.headers.get("Location") or "").split(",")[0].strip()
            if not loc:
                break
            if loc.startswith("/"):
                loc = "https://efootbase.com" + loc
            r = _sess.get(loc, timeout=timeout, allow_redirects=False)
            hops += 1
        if r.status_code == 200:
            h = r.text
            m = re.search(r"<title>([^<]+)</title>", h)
            if m:
                title = html.unescape(m.group(1)).split("—")[0].split("|")[0].strip()
                # "Name · 82 AMF · Card Name"
                parts = [p.strip() for p in title.split("·")]
                if parts:
                    info["name"] = parts[0]
                if len(parts) >= 2:
                    mm = re.match(r"(\d{2,3})\s+([A-Z]{1,3})", parts[1])
                    if mm:
                        info["overall"] = int(mm.group(1))
                        info["position"] = mm.group(2)
                if len(parts) >= 3:
                    info["card"] = parts[2]
            arts = re.findall(r"cdn\.assets\.efootbase\.com/eFPlayers/players/\d+/v2/art-[0-9a-f]+\.webp", h)
            if arts:
                info["art"] = "https://" + arts[0]
    except Exception:
        pass
    with _lock:
        _cache[key] = info
        _save()
    return info


def lookup_many(ids, max_workers=8):
    """Enrich many ids concurrently (cache-first)."""
    out = {}
    todo = []
    with _lock:
        for i in ids:
            k = str(i)
            if k in _cache:
                out[k] = _cache[k]
            else:
                todo.append(k)
    if todo:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
            for info in ex.map(lookup, todo):
                out[info["id"]] = info
    return out
