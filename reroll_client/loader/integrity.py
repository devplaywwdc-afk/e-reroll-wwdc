"""Integrity measurement M (L3).

    M = SHA256( b"RR1" || probe_files(exe) || probe_runtime() )

`probe_files` hashes the FROZEN, shipped exe bytes (PyInstaller onefile) — never
`run.py`. `release.py` measures the built exe with this SAME code and registers
the digest as `m_expected`; the running exe must compute a byte-identical M or
the server-wrapped K_brain won't unwrap (mass lockout). So the two code paths
are literally this one function.

Phase-1: probe_runtime() is NEUTRAL (anti-debug is report-only, NOT folded into
M yet). Folding anti-debug in is an L6 follow-up — do it only once the frozen
build is proven to measure identically at build and runtime, or honest users
get locked out.
"""
from __future__ import annotations

import hashlib
import sys

_MAGIC = b"RR1"
_CLEAN = b"\x00" * 32          # neutral runtime probe (phase 1)


def _probe_files(exe_path: str) -> bytes:
    h = hashlib.sha256()
    with open(exe_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.digest()


def _probe_runtime() -> bytes:
    return _CLEAN


def _default_target() -> str:
    # Frozen: the real exe. Unfrozen (dev/self-test): this file (crypto still
    # round-trips because release.py and runtime agree — but such an M will not
    # match a server build registered from a frozen exe).
    if getattr(sys, "frozen", False):
        return sys.executable
    return __file__


def measure(exe_path: str | None = None) -> bytes:
    """Return the raw 32-byte M. Pass exe_path from release.py (the built exe)."""
    target = exe_path or _default_target()
    return hashlib.sha256(_MAGIC + _probe_files(target) + _probe_runtime()).digest()
