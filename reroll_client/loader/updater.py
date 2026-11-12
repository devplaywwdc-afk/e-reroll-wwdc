"""Soft auto-update check against GitHub Releases (notice only, not forced).

Queries the public repo's latest release, compares its tag (v<version>) to
config.APP_VERSION, and returns where to download. The GUI shows a popup when a
newer version exists and offers a "download" button; nothing is forced.

Separate from the server's forced-build gate (which forces engine/brain compat);
this is for the customer-facing exe itself.
"""
from __future__ import annotations

import json
import urllib.request

from . import config


def _ver_tuple(s: str) -> tuple:
    s = (s or "").strip().lstrip("vV").split("+")[0].split("-")[0]
    out = []
    for p in s.split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out) or (0,)


def check_latest(timeout: float = 8.0) -> dict:
    """Return {latest, current, newer, url, notes, error}. Never raises."""
    res = {"latest": None, "current": config.APP_VERSION, "newer": False,
           "url": f"https://github.com/{config.GITHUB_REPO}/releases/latest",
           "notes": "", "error": None}
    try:
        url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "efootball-reroll-updater",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:  # GitHub cert via system CAs
            data = json.load(r)
        tag = data.get("tag_name") or ""
        res["latest"] = tag
        res["newer"] = _ver_tuple(tag) > _ver_tuple(config.APP_VERSION)
        res["notes"] = (data.get("body") or "")[:500]
        for a in data.get("assets", []):
            if (a.get("name") or "").lower().endswith(".exe"):
                res["url"] = a.get("browser_download_url") or res["url"]
                break
        else:
            res["url"] = data.get("html_url") or res["url"]
    except Exception as e:  # noqa: BLE001 — 404 (no releases) / offline: treat as "up to date"
        res["error"] = str(e)
    return res
