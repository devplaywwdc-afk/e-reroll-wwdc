"""Stable per-machine HWID (device anchor, L0).

Must be byte-identical across /session and /heartbeat on the same machine, and
stable across reboots. Windows: HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid
(set at OS install, survives reboots) blended with the MAC-derived node id as a
fallback. The raw identifiers are hashed so we never ship them in the clear.
"""
from __future__ import annotations

import functools
import hashlib
import platform
import uuid


def _machine_guid() -> str:
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography", 0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        try:
            val, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(val)
        finally:
            winreg.CloseKey(key)
    except Exception:
        return ""


@functools.lru_cache(maxsize=1)
def hwid() -> str:
    parts = [_machine_guid(), platform.node(), f"{uuid.getnode():012x}"]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]
