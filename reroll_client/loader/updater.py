"""Soft auto-update check against GitHub Releases (notice only, not forced).

Queries the public repo's latest release, compares its tag (v<version>) to
config.APP_VERSION, and returns where to download. The GUI shows a popup when a
newer version exists and offers a "download" button; nothing is forced.

Separate from the server's forced-build gate (which forces engine/brain compat);
this is for the customer-facing exe itself.
"""
from __future__ import annotations

import json
import os
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


def fetch_patchnotes(timeout: float = 8.0) -> list:
    """Parse PATCHNOTE.md (on the repo's default branch) into [{version,name,date,notes}],
    newest-first. Lets patch history survive even when old GitHub releases are deleted.
    Never raises (returns [] on any error)."""
    out = []
    try:
        # GitHub contents API with the raw media type: returns the live file (NOT the
        # raw.githubusercontent CDN, which caches ~5 min and showed stale versions).
        url = f"https://api.github.com/repos/{config.GITHUB_REPO}/contents/PATCHNOTE.md"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github.raw+json",
            "User-Agent": "efootball-reroll-updater",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return out
    cur = None
    for line in text.splitlines():
        if line.startswith("## "):
            if cur:
                out.append(cur)
            head = line[3:].strip()
            ver, date = head, ""
            for sep in ("—", "·", " - "):
                if sep in head:
                    a, b = head.split(sep, 1)
                    ver, date = a.strip(), b.strip()
                    break
            cur = {"version": ver, "name": ver, "date": date, "notes": ""}
        elif cur is not None and not line.startswith("# "):
            cur["notes"] += line + "\n"
    if cur:
        out.append(cur)
    for c in out:
        c["notes"] = c["notes"].strip()
    return out


def list_releases(timeout: float = 8.0, limit: int = 30) -> list:
    """All published releases newest-first: [{version, name, date, notes}]. For the
    in-app patch-notes panel. Never raises (returns [] on any error)."""
    out = []
    try:
        url = f"https://api.github.com/repos/{config.GITHUB_REPO}/releases?per_page={int(limit)}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "efootball-reroll-updater",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        for rel in data:
            if rel.get("draft"):
                continue
            out.append({
                "version": rel.get("tag_name") or rel.get("name") or "",
                "name": rel.get("name") or rel.get("tag_name") or "",
                "date": (rel.get("published_at") or "")[:10],
                "notes": rel.get("body") or "",
            })
    except Exception:  # noqa: BLE001
        pass
    return out


def check_latest(timeout: float = 8.0) -> dict:
    """Return {latest, current, newer, url, notes, error}. Never raises."""
    res = {"latest": None, "current": config.APP_VERSION, "newer": False,
           "url": f"https://github.com/{config.GITHUB_REPO}/releases/latest",
           "size": 0, "notes": "", "error": None}
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
                res["size"] = int(a.get("size") or 0)
                break
        else:
            res["url"] = data.get("html_url") or res["url"]
    except Exception as e:  # noqa: BLE001 — 404 (no releases) / offline: treat as "up to date"
        res["error"] = str(e)
    return res


def download_update(url: str, dest: str, expected_size: int = 0,
                    on_progress=None, timeout: float = 30.0) -> str:
    """Stream the new .exe to `dest`, calling on_progress(got, total). Verifies the
    final size against the release asset's size (guards a truncated/corrupt download).
    Raises on any failure so the caller can fall back to the web download."""
    req = urllib.request.Request(url, headers={"User-Agent": "efootball-reroll-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or expected_size or 0)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if on_progress:
                    try:
                        on_progress(got, total)
                    except Exception:  # noqa: BLE001
                        pass
    sz = os.path.getsize(dest)
    if expected_size and sz != expected_size:
        raise RuntimeError(f"ขนาดไฟล์ไม่ตรง ({sz} != {expected_size}) — ดาวน์โหลดไม่สมบูรณ์")
    if sz < 1_000_000:   # a real build is ~48 MB; anything tiny is an error page
        raise RuntimeError(f"ไฟล์เล็กผิดปกติ ({sz} bytes)")
    return dest
