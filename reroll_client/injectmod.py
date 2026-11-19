"""
Account injector (bundled into E-WWDC) — push a rerolled guest id into an
emulator so the game logs in as that account.

Overwrites /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat
via root adb (MuMu/LDPlayer userdebug). Adapted from pes_api/injector.py:
no `accounts` dependency, backups go to a writable dir next to the exe.
"""
import os
import re
import sys
import json
import time
import tempfile
import subprocess

PKG = "jp.konami.pesam"
FILES = f"/data/user/0/{PKG}/files/SaveData"
AUTH_DIR = f"{FILES}/AUTH"
SYSTEM_DIR = f"{FILES}/SYSTEM"
DAT = f"{AUTH_DIR}/online_user_id_data.dat"

EMU_PORTS = {
    5555: "generic/MuMu", 7555: "MuMu", 5557: "MuMu", 5559: "MuMu",
    16384: "MuMu 12", 16416: "MuMu 12", 16448: "MuMu 12", 16480: "MuMu 12",
    5554: "LDPlayer/AVD", 5556: "LDPlayer", 5558: "LDPlayer",
    62001: "Nox", 62025: "Nox", 62026: "Nox",
    21503: "MEmu", 21513: "MEmu",
}
PROC_MAP = {
    "MuMuPlayer.exe": "MuMu", "MuMuVMMHeadless.exe": "MuMu", "MuMuNxDevice.exe": "MuMu",
    "dnplayer.exe": "LDPlayer", "LdVBoxHeadless.exe": "LDPlayer", "Ld9BoxHeadless.exe": "LDPlayer",
    "Nox.exe": "Nox", "NoxVMHandle.exe": "Nox",
    "MEmu.exe": "MEmu", "MEmuHeadless.exe": "MEmu",
}

def _adb_path():
    """Prefer the adb.exe bundled into the exe (bin/adb.exe), so Inject works with
    no platform-tools installed. Falls back to ADB_PATH / PATH."""
    cands = []
    base = getattr(sys, "_MEIPASS", None)   # PyInstaller onefile extract dir
    if base:
        cands.append(os.path.join(base, "bin", "adb.exe"))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "adb.exe"))
    for c in cands:
        if os.path.exists(c):
            return c
    return os.environ.get("ADB_PATH", "adb")


ADB = _adb_path()


def _backup_dir():
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    d = os.path.join(base, "inject_backup")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        d = os.path.join(tempfile.gettempdir(), "ewwdc_inject_backup")
        os.makedirs(d, exist_ok=True)
    return d


def _run(args, timeout=40):
    try:
        r = subprocess.run([ADB] + args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except FileNotFoundError:
        return "__ERR__ adb-not-found"
    except Exception as e:  # noqa: BLE001
        return f"__ERR__ {type(e).__name__}: {e}"


def _adb(dev, *args, timeout=40):
    return _run(["-s", dev] + list(args), timeout=timeout)


def _run_raw(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _pc_emulators():
    if os.name != "nt":
        return []
    out = _run_raw(["tasklist"])
    fams = set()
    for proc, fam in PROC_MAP.items():
        if proc.lower() in out.lower():
            fams.add(fam)
    return sorted(fams)


def _is_emulator(dev):
    return bool(re.search(r":\d+$", dev)) or dev.startswith("emulator-")


def _label(dev, running_fams):
    m = re.search(r":(\d+)$", dev)
    if m:
        port = int(m.group(1))
        name = EMU_PORTS.get(port)
        if name:
            return name
        if 16384 <= port <= 17000 and (port - 16384) % 32 == 0:
            return "MuMu 12"
    if not _is_emulator(dev):
        return "โทรศัพท์ (USB)"   # physical device (serial), refined to model in list_devices
    if running_fams:
        return "/".join(running_fams)
    return "emulator"


def adb_ok():
    """True if the adb binary is runnable."""
    return "__ERR__ adb-not-found" not in _run(["version"], timeout=10)


def connect_common():
    for port in EMU_PORTS:
        _run(["connect", f"127.0.0.1:{port}"], timeout=6)


def list_devices(do_connect=True):
    """Connected emulators with label + game/root status. Raises nothing."""
    if not adb_ok():
        raise RuntimeError("หา adb ไม่เจอ — ติดตั้ง platform-tools แล้วตั้ง PATH หรือ ADB_PATH")
    if do_connect:
        connect_common()
    out = _run(["devices"])
    fams = _pc_emulators()
    devs = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        dev, state = parts[0], parts[1]
        # accept emulators (host:port / emulator-) AND physical phones (USB serial)
        if state != "device":
            devs.append({"device": dev, "state": state, "label": _label(dev, fams), "_vm": dev})
            continue
        info = {"device": dev, "state": "device", "label": _label(dev, fams)}
        info["emulator"] = _is_emulator(dev)
        # physical phone: refine label to the real model name
        if not info["emulator"]:
            model = _adb(dev, "shell", "getprop", "ro.product.model").strip()
            if model and "__ERR__" not in model:
                info["label"] = model + " (USB)"
        info["game"] = PKG in _adb(dev, "shell", "pm", "path", PKG)
        _adb(dev, "root")
        time.sleep(0.3)
        cur = _adb(dev, "shell", "cat", DAT)
        info["root"] = "uid=0" in _adb(dev, "shell", "id")
        m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
        info["current_id"] = m.group(1) if m else None
        info["provisioned"] = "No such file" not in _adb(dev, "shell", "ls", FILES)
        info["_vm"] = _adb(dev, "shell", "cat", "/proc/sys/kernel/random/boot_id").strip() or dev
        devs.append(info)
    return _dedup_vm(devs)


def _dedup_vm(devs):
    seen, out = {}, []
    for d in devs:
        key = d.get("_vm") or d["device"]
        if key in seen:
            seen[key].setdefault("alt_ports", []).append(d["device"])
            if d.get("game") and d.get("root") and not (seen[key].get("game") and seen[key].get("root")):
                d["alt_ports"] = seen[key]["alt_ports"]
                out[out.index(seen[key])] = d
                seen[key] = d
            continue
        d.setdefault("alt_ports", [])
        seen[key] = d
        out.append(d)
    return out


def _app_uid(dev):
    out = _adb(dev, "shell", "stat", "-c", "%u:%g", f"/data/user/0/{PKG}").strip()
    return out if re.match(r"^\d+:\d+$", out) else None


def inject(dev, acct, backup=True, clear_system=True, launch=True):
    """Write the account into the emulator's AUTH file + optionally launch.
    Returns {ok, steps:[...], current_id, error?}."""
    steps = []

    def step(msg, out=""):
        steps.append({"msg": msg, "out": (out or "").strip()[:200]})

    r = _adb(dev, "root")
    time.sleep(0.6)
    _adb(dev, "wait-for-device")
    if "uid=0" not in _adb(dev, "shell", "id"):
        step("adb root", r)
        return {"ok": False, "steps": steps, "error": "root ไม่ได้ (เปิด Root ใน emulator settings)"}
    step("adb root → uid=0 ✓")

    _adb(dev, "shell", "am", "force-stop", PKG)
    step(f"force-stop {PKG}")

    uid = _app_uid(dev)
    if not uid:
        return {"ok": False, "steps": steps, "error": f"หา uid ของ {PKG} ไม่เจอ (เกมติดตั้งยัง?)"}
    step(f"app uid = {uid}")

    if backup:
        cur = _adb(dev, "shell", "cat", DAT)
        if cur.strip().startswith("{"):
            bdir = _backup_dir()
            fn = os.path.join(bdir, f"{dev.replace(':', '_')}_{int(time.time())}.dat")
            try:
                open(fn, "w", encoding="utf-8").write(cur.strip())
            except OSError:
                pass
            m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
            step(f"backup ไอดีเดิม ({m.group(1) if m else '?'}) → {os.path.basename(fn)}")

    payload = {
        "auth_code": acct["auth_code"],
        "user_code": acct["user_code"],
        "user_id": int(acct["user_id"]),
        "version": "2.0.0",
        "prev_leave_foreground_time": int(time.time()),
    }
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    tmp = os.path.join(tempfile.gettempdir(), "oid_inject.dat")
    open(tmp, "w", encoding="utf-8", newline="").write(text)
    _adb(dev, "push", tmp, "/data/local/tmp/oid_inject.dat")
    _adb(dev, "shell", "mkdir", "-p", AUTH_DIR)
    cp = _adb(dev, "shell", "cp", "/data/local/tmp/oid_inject.dat", DAT)
    step(f"เขียน online_user_id_data.dat → {acct['user_code']}", cp)

    if clear_system:
        _adb(dev, "shell", "sh", "-c", f"rm -f {SYSTEM_DIR}/* 2>/dev/null")
        step("ล้าง SaveData/SYSTEM (ตัด device-bind เก่า)")

    _adb(dev, "shell", "chown", "-R", uid, AUTH_DIR)
    _adb(dev, "shell", "restorecon", "-R", AUTH_DIR)
    _adb(dev, "shell", "rm", "-f", "/data/local/tmp/oid_inject.dat")
    step("chown + restorecon ✓")

    back = _adb(dev, "shell", "cat", DAT)
    ok = acct["user_code"] in back
    step("verify readback", "OK" if ok else back)

    if ok and launch:
        _adb(dev, "shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
        step("เปิดเกม → login เป็นไอดีที่ inject")

    return {"ok": ok, "steps": steps, "current_id": acct["user_code"],
            "error": None if ok else "เขียนไฟล์แล้วแต่ verify ไม่ผ่าน"}
