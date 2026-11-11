"""
eFootball Account Injector — standalone core (no third-party deps).

The game stores its guest login as plain JSON (no encryption):
    /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat
    {"auth_code","user_code","user_id","version":"2.0.0","prev_leave_foreground_time":<ts>}
which equals a reroll .txt + a timestamp. Inject = overwrite that file via root adb.

Emulator only (adb root). Supports batch over many emulators.
"""
import os
import re
import sys
import json
import time
import glob
import tempfile
import subprocess

PKG = "jp.konami.pesam"
FILES = f"/data/user/0/{PKG}/files/SaveData"
AUTH_DIR = f"{FILES}/AUTH"
SYSTEM_DIR = f"{FILES}/SYSTEM"
DAT = f"{AUTH_DIR}/online_user_id_data.dat"

EMU_PORTS = {
    5555: "generic/MuMu", 7555: "MuMu", 5557: "MuMu", 5559: "MuMu",
    16384: "MuMu 12", 16416: "MuMu 12", 16448: "MuMu 12", 16480: "MuMu 12", 16512: "MuMu 12",
    5554: "LDPlayer/AVD", 5556: "LDPlayer", 5558: "LDPlayer",
    62001: "Nox", 62025: "Nox", 62026: "Nox",
    21503: "MEmu", 21513: "MEmu",
}
PROC_MAP = {
    "MuMuPlayer.exe": "MuMu", "MuMuVMMHeadless.exe": "MuMu", "MuMuNxDevice.exe": "MuMu",
    "dnplayer.exe": "LDPlayer", "LdVBoxHeadless.exe": "LDPlayer", "Ld9BoxHeadless.exe": "LDPlayer",
    "Nox.exe": "Nox", "NoxVMHandle.exe": "Nox", "MEmu.exe": "MEmu", "MEmuHeadless.exe": "MEmu",
}
# where emulators ship their own adb.exe (fallbacks if adb isn't on PATH)
ADB_HINTS = [
    r"C:\Program Files\Netease\MuMu Player 12\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
    r"C:\Program Files\MuMu\emulator\nemu\vmonitor\bin\adb_server.exe",
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files (x86)\Nox\bin\nox_adb.exe",
    r"C:\Program Files\Microvirt\MEmu\adb.exe",
]

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def resolve_adb():
    """Find a usable adb: bundled next to the exe, env ADB_PATH, PATH, then emulator hints."""
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    for c in [os.path.join(here, "adb.exe"), os.environ.get("ADB_PATH")]:
        if c and os.path.exists(c):
            return c
    # PATH
    try:
        p = subprocess.run(["adb", "version"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        if p.returncode == 0:
            return "adb"
    except Exception:
        pass
    for h in ADB_HINTS:
        if os.path.exists(h):
            return h
    return "adb"  # last resort (may fail -> surfaced to user)


ADB = resolve_adb()


def set_adb(path):
    global ADB
    ADB = path or "adb"


def _run(args, timeout=40):
    try:
        r = subprocess.run([ADB] + args, capture_output=True, text=True,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return f"__ERR__ {type(e).__name__}: {e}"


def _adb(dev, *args, timeout=40):
    return _run(["-s", dev] + list(args), timeout=timeout)


def _tasklist():
    if os.name != "nt":
        return ""
    try:
        return subprocess.run(["tasklist"], capture_output=True, text=True,
                              timeout=15, creationflags=CREATE_NO_WINDOW).stdout or ""
    except Exception:
        return ""


def _pc_families():
    out = _tasklist().lower()
    return sorted({fam for proc, fam in PROC_MAP.items() if proc.lower() in out})


def _label(dev, fams):
    m = re.search(r":(\d+)$", dev)
    if m:
        port = int(m.group(1))
        if port in EMU_PORTS:
            return EMU_PORTS[port]
        if 16384 <= port <= 17000 and (port - 16384) % 32 == 0:
            return "MuMu 12"
    return "/".join(fams) if fams else "emulator"


def connect_common(ports=None):
    for port in (ports or EMU_PORTS):
        _run(["connect", f"127.0.0.1:{port}"], timeout=5)


def list_devices(do_connect=True):
    if do_connect:
        connect_common()
    out = _run(["devices"])
    fams = _pc_families()
    devs = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        dev, state = parts[0], parts[1]
        d = {"device": dev, "state": state, "label": _label(dev, fams),
             "game": False, "root": False, "provisioned": False, "current_id": None}
        if state == "device":
            d["game"] = PKG in _adb(dev, "shell", "pm", "path", PKG)
            _adb(dev, "root"); time.sleep(0.3)
            d["root"] = "uid=0" in _adb(dev, "shell", "id")
            cur = _adb(dev, "shell", "cat", DAT)
            m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
            d["current_id"] = m.group(1) if m else None
            d["provisioned"] = "No such file" not in _adb(dev, "shell", "ls", FILES)
            d["usable"] = d["game"] and d["root"]
        else:
            d["usable"] = False
        devs.append(d)
    return devs


def _app_uid(dev):
    out = _adb(dev, "shell", "stat", "-c", "%u:%g", f"/data/user/0/{PKG}").strip()
    return out if re.match(r"^\d+:\d+$", out) else None


def inject_one(dev, acct, backup=True, clear_system=True, launch=True, log=lambda *_: None,
               backup_dir=None):
    """Inject one account into one device. log(msg) is called per step. Returns (ok, msg)."""
    _adb(dev, "root"); time.sleep(0.5); _adb(dev, "wait-for-device")
    if "uid=0" not in _adb(dev, "shell", "id"):
        log("  ✗ root ไม่ได้ (เปิด Root ใน emulator settings)")
        return False, "root ไม่ได้"
    _adb(dev, "shell", "am", "force-stop", PKG)
    uid = _app_uid(dev)
    if not uid:
        log("  ✗ หา uid ของเกมไม่เจอ (เกมติดตั้งยัง?)")
        return False, "no app uid"
    if backup:
        cur = _adb(dev, "shell", "cat", DAT)
        if cur.strip().startswith("{"):
            bd = backup_dir or os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "backups")
            os.makedirs(bd, exist_ok=True)
            m = re.search(r'"user_code"\s*:\s*"([^"]+)"', cur)
            fn = os.path.join(bd, f"{dev.replace(':','_')}_{m.group(1) if m else 'id'}_{int(time.time())}.dat")
            open(fn, "w", encoding="utf-8").write(cur.strip())
            log(f"  • backup ไอดีเดิม {m.group(1) if m else '?'}")
    payload = {"auth_code": acct["auth_code"], "user_code": acct["user_code"],
               "user_id": int(acct["user_id"]), "version": "2.0.0",
               "prev_leave_foreground_time": int(time.time())}
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    tmp = os.path.join(tempfile.gettempdir(), f"oid_{dev.replace(':','_')}.dat")
    open(tmp, "w", encoding="utf-8", newline="").write(text)
    _adb(dev, "push", tmp, "/data/local/tmp/oid_inject.dat")
    _adb(dev, "shell", "mkdir", "-p", AUTH_DIR)
    _adb(dev, "shell", "cp", "/data/local/tmp/oid_inject.dat", DAT)
    if clear_system:
        _adb(dev, "shell", "sh", "-c", f"rm -f {SYSTEM_DIR}/* 2>/dev/null")
    _adb(dev, "shell", "chown", "-R", uid, AUTH_DIR)
    _adb(dev, "shell", "restorecon", "-R", AUTH_DIR)
    _adb(dev, "shell", "rm", "-f", "/data/local/tmp/oid_inject.dat")
    ok = acct["user_code"] in _adb(dev, "shell", "cat", DAT)
    try:
        os.remove(tmp)
    except Exception:
        pass
    if ok and launch:
        _adb(dev, "shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    log(f"  {'✓' if ok else '✗'} {dev} ← {acct['user_code']}" + ("" if ok else " (verify fail)"))
    return ok, acct["user_code"] if ok else "verify fail"


def inject_batch(pairs, backup=True, clear_system=True, launch=True, log=lambda *_: None):
    """pairs = [(device, acct), ...]. Injects each (sequentially). Returns summary dict."""
    done, fail = 0, 0
    for dev, acct in pairs:
        log(f"[{dev}] inject {acct['user_code']} ...")
        ok, _ = inject_one(dev, acct, backup, clear_system, launch, log=log)
        done += ok
        fail += (not ok)
    log(f"=== เสร็จ: สำเร็จ {done} / ล้มเหลว {fail} ===")
    return {"done": done, "fail": fail}


# ---------------------------------------------------------------- account loading
def load_account(path):
    o = json.load(open(path, encoding="utf-8"))
    if "user_id" in o and "auth_code" in o:
        o.setdefault("user_code", os.path.splitext(os.path.basename(path))[0])
        return o
    raise ValueError("ไฟล์ไม่ใช่ account (ต้องมี user_id + auth_code)")


def load_folder(folder):
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*.txt")) + glob.glob(os.path.join(folder, "*.dat"))):
        try:
            out.append(load_account(p))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- CLI
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="eFootball emulator account injector")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    pi = sub.add_parser("inject")
    pi.add_argument("device"); pi.add_argument("file")
    pi.add_argument("--no-launch", action="store_true"); pi.add_argument("--no-backup", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        for d in list_devices():
            print(d)
    elif a.cmd == "inject":
        acct = load_account(a.file)
        ok, msg = inject_one(a.device, acct, backup=not a.no_backup, launch=not a.no_launch, log=print)
        print("RESULT:", "OK" if ok else "FAIL", msg)
    else:
        ap.print_help()
