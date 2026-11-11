"""
eFootball account injector — push a rerolled guest id into an emulator/device so the
game logs in as that account.

The game stores its guest login as plain JSON (no encryption) at:
    /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat
    {"auth_code","user_code","user_id","version":"2.0.0","prev_leave_foreground_time":<ts>}
which is exactly our reroll .txt plus a timestamp. Inject = overwrite that file (root).

Needs root adb (`adb root` -> uid 0; works on MuMu/LDPlayer userdebug builds).
"""
import os
import re
import sys
import json
import time
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(__file__))
import accounts

PKG = "jp.konami.pesam"
FILES = f"/data/user/0/{PKG}/files/SaveData"
AUTH_DIR = f"{FILES}/AUTH"
SYSTEM_DIR = f"{FILES}/SYSTEM"
DAT = f"{AUTH_DIR}/online_user_id_data.dat"

# common emulator adb ports we try to connect + a best-guess label by port
EMU_PORTS = {
    5555: "generic/MuMu", 7555: "MuMu", 5557: "MuMu", 5559: "MuMu",
    16384: "MuMu 12", 16416: "MuMu 12", 16448: "MuMu 12", 16480: "MuMu 12",
    5554: "LDPlayer/AVD", 5556: "LDPlayer", 5558: "LDPlayer",
    62001: "Nox", 62025: "Nox", 62026: "Nox",
    21503: "MEmu", 21513: "MEmu",
}
# PC process -> emulator family (for a hint when the port is ambiguous)
PROC_MAP = {
    "MuMuPlayer.exe": "MuMu", "MuMuVMMHeadless.exe": "MuMu", "MuMuNxDevice.exe": "MuMu",
    "dnplayer.exe": "LDPlayer", "LdVBoxHeadless.exe": "LDPlayer", "Ld9BoxHeadless.exe": "LDPlayer",
    "Nox.exe": "Nox", "NoxVMHandle.exe": "Nox",
    "MEmu.exe": "MEmu", "MEmuHeadless.exe": "MEmu",
}

ADB = os.environ.get("ADB_PATH", "adb")


def _run(args, timeout=40):
    try:
        r = subprocess.run([ADB] + args, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"__ERR__ {type(e).__name__}: {e}"


def _adb(dev, *args, timeout=40):
    return _run(["-s", dev] + list(args), timeout=timeout)


def _pc_emulators():
    """Which emulator families are running on this PC (from tasklist)."""
    if os.name != "nt":
        return []
    out = _run_raw(["tasklist"])
    fams = set()
    for proc, fam in PROC_MAP.items():
        if proc.lower() in out.lower():
            fams.add(fam)
    return sorted(fams)


def _run_raw(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout or ""
    except Exception:
        return ""


def _label(dev, running_fams):
    """Best-guess emulator name for a device id like '127.0.0.1:5559'."""
    m = re.search(r":(\d+)$", dev)
    if m:
        port = int(m.group(1))
        name = EMU_PORTS.get(port)
        if name:
            return name
        # MuMu 12 uses 16384 + 32*n
        if 16384 <= port <= 17000 and (port - 16384) % 32 == 0:
            return "MuMu 12"
    if running_fams:
        return "/".join(running_fams)
    return "emulator"


def connect_common():
    """adb connect the usual emulator ports so they show up as devices."""
    for port in EMU_PORTS:
        _run(["connect", f"127.0.0.1:{port}"], timeout=6)


def list_devices(do_connect=True):
    """Return connected devices with emulator label + game/root status."""
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
        # emulator-only: skip physical/USB devices (serial, not host:port)
        if not (re.search(r":\d+$", dev) or dev.startswith("emulator-")):
            continue
        if state != "device":
            devs.append({"device": dev, "state": state, "label": _label(dev, fams), "_vm": dev})
            continue
        info = {"device": dev, "state": "device", "label": _label(dev, fams)}
        # game installed?
        info["game"] = PKG in _adb(dev, "shell", "pm", "path", PKG)
        # current logged-in id (if any) — needs root; try after root
        _adb(dev, "root")
        time.sleep(0.3)
        cur = _adb(dev, "shell", "cat", DAT)
        info["root"] = "uid=0" in _adb(dev, "shell", "id")
        m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
        info["current_id"] = m.group(1) if m else None
        info["provisioned"] = "No such file" not in _adb(dev, "shell", "ls", FILES)
        # boot_id is identical across every adb port of ONE running VM -> dedup key
        info["_vm"] = _adb(dev, "shell", "cat", "/proc/sys/kernel/random/boot_id").strip() or dev
        devs.append(info)
    return _dedup_vm(devs)


def _dedup_vm(devs):
    """Collapse the multiple adb ports MuMu/LDPlayer expose for one VM into a single row."""
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
    """Write the account into the emulator's AUTH file and (optionally) launch the game.
    Returns {ok, steps:[...], current_id, error?}."""
    steps = []

    def step(msg, out=""):
        steps.append({"msg": msg, "out": (out or "").strip()[:200]})

    # 1) root
    r = _adb(dev, "root")
    time.sleep(0.6)
    _adb(dev, "wait-for-device")
    if "uid=0" not in _adb(dev, "shell", "id"):
        step("adb root", r)
        return {"ok": False, "steps": steps, "error": "root ไม่ได้ (เปิด Root ใน emulator settings)"}
    step("adb root → uid=0 ✓")

    # 2) stop game
    _adb(dev, "shell", "am", "force-stop", PKG)
    step(f"force-stop {PKG}")

    uid = _app_uid(dev)
    if not uid:
        return {"ok": False, "steps": steps, "error": f"หา uid ของ {PKG} ไม่เจอ (เกมติดตั้งยัง?)"}
    step(f"app uid = {uid}")

    # 3) backup current
    if backup:
        cur = _adb(dev, "shell", "cat", DAT)
        if cur.strip().startswith("{"):
            bdir = os.path.join(accounts.ACC_DIR, "_inject_backup")
            os.makedirs(bdir, exist_ok=True)
            fn = os.path.join(bdir, f"{dev.replace(':','_')}_{int(time.time())}.dat")
            open(fn, "w", encoding="utf-8").write(cur.strip())
            m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
            step(f"backup ไอดีเดิม ({m.group(1) if m else '?'}) → {os.path.basename(fn)}")

    # 4) build + push the new .dat
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

    # 5) drop stale device/session bind
    if clear_system:
        _adb(dev, "shell", "sh", "-c", f"rm -f {SYSTEM_DIR}/* 2>/dev/null")
        step("ล้าง SaveData/SYSTEM (ตัด device-bind เก่า)")

    # 6) fix ownership + SELinux
    _adb(dev, "shell", "chown", "-R", uid, AUTH_DIR)
    _adb(dev, "shell", "restorecon", "-R", AUTH_DIR)
    _adb(dev, "shell", "rm", "-f", "/data/local/tmp/oid_inject.dat")
    step("chown + restorecon ✓")

    # 7) verify
    back = _adb(dev, "shell", "cat", DAT)
    ok = acct["user_code"] in back
    step("verify readback", "OK" if ok else back)

    # 8) launch
    if ok and launch:
        _adb(dev, "shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
        step("เปิดเกม → login เป็นไอดีที่ inject")

    return {"ok": ok, "steps": steps, "current_id": acct["user_code"],
            "error": None if ok else "เขียนไฟล์แล้วแต่ verify ไม่ผ่าน"}


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    pi = sub.add_parser("inject")
    pi.add_argument("device")
    pi.add_argument("file", help="path to a reroll .txt (or a user_code in accounts/)")
    pi.add_argument("--no-launch", action="store_true")
    pi.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        for d in list_devices():
            print(d)
    elif a.cmd == "inject":
        p = a.file
        if not os.path.exists(p):
            p = os.path.join(accounts.ACC_DIR, a.file if a.file.endswith(".txt") else a.file + ".txt")
        acct = json.load(open(p, encoding="utf-8"))
        res = inject(a.device, acct, backup=not a.no_backup, launch=not a.no_launch)
        for s in res["steps"]:
            print(" •", s["msg"], ("  "+s["out"]) if s["out"] else "")
        print("RESULT:", "OK" if res["ok"] else "FAIL", res.get("error") or "")
    else:
        ap.print_help()
