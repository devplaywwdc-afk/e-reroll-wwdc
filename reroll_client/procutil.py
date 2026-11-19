"""No-flash subprocess handling for the packaged (windowed) .exe.

adb (and any console tool) pops a cmd/conhost window for a split second on each
spawn unless CREATE_NO_WINDOW + a hidden STARTUPINFO are set. The app is built
console=False, so those flashes are the only windows a user sees.

install() patches subprocess.Popen once so EVERY spawn (ours + any library's) is
hidden — no need to pass creationflags at each call site.
"""
from __future__ import annotations

import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
_IS_WIN = sys.platform == "win32"
_installed = False


def _hide(kw: dict) -> dict:
    if not _IS_WIN:
        return kw
    si = kw.get("startupinfo")
    if si is None:
        si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    kw["startupinfo"] = si
    kw["creationflags"] = kw.get("creationflags", 0) | CREATE_NO_WINDOW
    return kw


def install() -> None:
    """Globally hide the console for every subprocess spawn. Idempotent; no-op off
    Windows. Patching Popen covers run()/call()/check_output() too."""
    global _installed
    if _installed or not _IS_WIN:
        _installed = True
        return
    _orig = subprocess.Popen

    class _HiddenPopen(_orig):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **_hide(kwargs))

    subprocess.Popen = _HiddenPopen  # type: ignore[misc]
    _installed = True
