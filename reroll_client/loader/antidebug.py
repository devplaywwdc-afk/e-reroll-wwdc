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


def _peb_flags() -> list[str]:
    """PEB.BeingDebugged (+NtGlobalFlag heap markers) read via NtQueryInformationProcess
    ProcessBasicInformation, so a NOP'd kernel32!IsDebuggerPresent is still caught."""
    flags: list[str] = []
    try:
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _PBI(ctypes.Structure):
            _fields_ = [("Reserved1", ctypes.c_void_p),
                        ("PebBaseAddress", ctypes.c_void_p),
                        ("Reserved2", ctypes.c_void_p * 2),
                        ("UniqueProcessId", ctypes.c_void_p),
                        ("Reserved3", ctypes.c_void_p)]
        pbi = _PBI()
        rl = ctypes.c_ulong(0)
        if ntdll.NtQueryInformationProcess(k32.GetCurrentProcess(), 0,
                ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(rl)) == 0 and pbi.PebBaseAddress:
            being = ctypes.c_ubyte(0)
            # PEB.BeingDebugged is at offset 0x02 on both x86/x64
            ctypes.memmove(ctypes.byref(being),
                           ctypes.c_void_p(pbi.PebBaseAddress + 0x02), 1)
            if being.value:
                flags.append("PEB.BeingDebugged")
    except Exception:
        pass
    return flags


def _hwbp_flags() -> list[str]:
    """Hardware breakpoints (DR0-DR3) on the current thread => a debugger set them."""
    flags: list[str] = []
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        CONTEXT_DEBUG_REGISTERS = 0x00100000 | 0x00000010

        class _CTX(ctypes.Structure):
            _fields_ = [("pad0", ctypes.c_byte * 48),
                        ("ContextFlags", ctypes.c_ulong),
                        ("pad1", ctypes.c_byte * 24),
                        ("Dr0", ctypes.c_ulonglong), ("Dr1", ctypes.c_ulonglong),
                        ("Dr2", ctypes.c_ulonglong), ("Dr3", ctypes.c_ulonglong),
                        ("Dr6", ctypes.c_ulonglong), ("Dr7", ctypes.c_ulonglong),
                        ("rest", ctypes.c_byte * 1200)]
        ctx = _CTX()
        ctx.ContextFlags = CONTEXT_DEBUG_REGISTERS
        if k32.GetThreadContext(k32.GetCurrentThread(), ctypes.byref(ctx)):
            if ctx.Dr0 or ctx.Dr1 or ctx.Dr2 or ctx.Dr3 or ctx.Dr7:
                flags.append("HardwareBreakpoint")
    except Exception:
        pass
    return flags


def detect_threats() -> list[str]:
    """Return a list of threat flags (empty = clean). Never raises."""
    if not _is_windows():
        return []
    # REROLL_ALLOW_DEBUG is a DEV convenience only — it must NOT work in a shipped
    # (frozen) build, or an attacker just sets it to strip anti-debug before a RAM
    # dump. In a PyInstaller onefile exe sys.frozen is set -> the bypass is ignored.
    if os.environ.get("REROLL_ALLOW_DEBUG") == "1" and not getattr(sys, "frozen", False):
        return []
    out: list[str] = []
    out += _basic_debugger_flags()
    out += _ntquery_flags()
    out += _peb_flags()
    out += _hwbp_flags()
    out += _module_flags()
    return out


def enforce() -> None:
    """Stop the run if a debugger/injector is present (soft gate)."""
    threats = detect_threats()
    if threats:
        print(f"[security] refusing to run — {', '.join(threats)}", file=sys.stderr)
        os._exit(3)


def watch(stop_event, interval: float = 25.0) -> None:
    """Background re-check: catches a debugger/injector ATTACHED mid-run (after the
    one-shot enforce() at start). Exits the process on detection. Daemon-safe.

    25s interval: the per-cycle EnumProcessModules scan is the heaviest check, so a
    tighter loop put a small hitch into the UI every few seconds. An attach is still
    caught within one cycle; the dump/patch surface it guards is not time-critical."""
    import threading

    def _loop():
        while not stop_event.wait(interval):
            threats = detect_threats()
            if threats:
                print(f"[security] threat mid-run — {', '.join(threats)}", file=sys.stderr)
                os._exit(3)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t
