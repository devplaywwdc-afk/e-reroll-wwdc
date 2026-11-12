"""Anti-debug / anti-hook (L6) — Windows, SOFT gate.

Phase-1 decision (follows the hard-won PC pitfall): this is a *soft* runtime
signal that STOPS the run when a debugger/injector is present, but is **NOT**
folded into the integrity M. Folding anti-debug into M risks a frozen-only
false-positive locking out every honest user; keeping it separate means a false
positive at worst stops one run (retryable) instead of bricking the build.

Checks (best-effort, fail-open on API error):
  - IsDebuggerPresent / CheckRemoteDebuggerPresent
  - NtQueryInformationProcess: ProcessDebugPort / ProcessDebugFlags / DebugObject
  - loaded-module scan for known injector/debugger DLL name fragments

Set REROLL_ALLOW_DEBUG=1 to bypass (for developing the tool itself).
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

# name fragments of known injectors/debuggers/dumpers (lowercase).
# deliberately does NOT include legit runtime dlls (pythonXX, vcruntime, api-ms).
_BAD_MODULES = (
    "frida", "gum", "linjector", "winjector",
    "cheatengine", "cheat-engine", "vehdebug", "speedhack",
    "titanhide", "scylla", "scyllahide",
    "x64dbg", "x32dbg", "ollydbg", "dbghelp_hook",
    "pe-sieve", "pesieve", "megadumper", "mega_dumper",
    "ida", "idag", "ida64",
)


def _is_windows() -> bool:
    return os.name == "nt"


def _basic_debugger_flags() -> list[str]:
    flags: list[str] = []
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if k32.IsDebuggerPresent():
            flags.append("IsDebuggerPresent")
        present = wintypes.BOOL(0)
        if k32.CheckRemoteDebuggerPresent(
                k32.GetCurrentProcess(), ctypes.byref(present)) and present.value:
            flags.append("RemoteDebugger")
    except Exception:
        pass
    return flags


def _ntquery_flags() -> list[str]:
    flags: list[str] = []
    try:
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.GetCurrentProcess()
        # ProcessDebugPort = 7 (nonzero => debugger)
        port = ctypes.c_void_p(0)
        rl = ctypes.c_ulong(0)
        if ntdll.NtQueryInformationProcess(
                h, 7, ctypes.byref(port), ctypes.sizeof(port),
                ctypes.byref(rl)) == 0 and port.value:
            flags.append("DebugPort")
        # ProcessDebugFlags = 31 (0 => being debugged)
        dbgflags = ctypes.c_ulong(1)
        if ntdll.NtQueryInformationProcess(
                h, 31, ctypes.byref(dbgflags), ctypes.sizeof(dbgflags),
                ctypes.byref(rl)) == 0 and dbgflags.value == 0:
            flags.append("DebugFlags")
        # ProcessDebugObjectHandle = 30 (nonzero => debugger)
        obj = ctypes.c_void_p(0)
        if ntdll.NtQueryInformationProcess(
                h, 30, ctypes.byref(obj), ctypes.sizeof(obj),
                ctypes.byref(rl)) == 0 and obj.value:
            flags.append("DebugObject")
    except Exception:
        pass
    return flags


def _module_flags() -> list[str]:
    flags: list[str] = []
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.GetCurrentProcess()
        arr = (wintypes.HMODULE * 1024)()
        needed = wintypes.DWORD(0)
        if not psapi.EnumProcessModules(h, arr, ctypes.sizeof(arr),
                                        ctypes.byref(needed)):
            return flags
        count = min(1024, needed.value // ctypes.sizeof(wintypes.HMODULE))
        buf = ctypes.create_unicode_buffer(260)
        seen = set()
        for i in range(count):
            if psapi.GetModuleFileNameExW(h, arr[i], buf, 260):
                name = os.path.basename(buf.value).lower()
                for bad in _BAD_MODULES:
                    if bad in name and name not in seen:
                        seen.add(name)
                        flags.append(f"mod:{name}")
    except Exception:
        pass
    return flags


def detect_threats() -> list[str]:
    """Return a list of threat flags (empty = clean). Never raises."""
    if not _is_windows():
        return []
    if os.environ.get("REROLL_ALLOW_DEBUG") == "1":
        return []
    out: list[str] = []
    out += _basic_debugger_flags()
    out += _ntquery_flags()
    out += _module_flags()
    return out


def enforce() -> None:
    """Stop the run if a debugger/injector is present (soft gate)."""
    threats = detect_threats()
    if threats:
        print(f"[security] refusing to run — {', '.join(threats)}", file=sys.stderr)
        os._exit(3)
