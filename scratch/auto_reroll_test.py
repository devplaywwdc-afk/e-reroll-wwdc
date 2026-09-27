import subprocess
import time
import os
import json

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

def adb_cmd(cmd):
    full = f'"{ADB}" -s {DEV} {cmd}'
    r = subprocess.run(full, shell=True, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout.strip()

def cap(name):
    adb_cmd(f"shell screencap -p /sdcard/{name}.png")
    out = os.path.join("scratch", f"{name}.png")
    subprocess.run(f'"{ADB}" -s {DEV} pull /sdcard/{name}.png {out}', shell=True, capture_output=True)
    return out

def get_auth():
    raw = adb_cmd("shell su -c 'cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat'")
    try:
        return json.loads(raw)
    except:
        return None

# Ensure adb connected
subprocess.run(f'"{ADB}" connect {DEV}', shell=True, capture_output=True)
curr = get_auth()
print(f"[*] Pre-reset Account: {curr}")

# 1. Stop game
print("[*] Force-stopping eFootball...")
adb_cmd("shell am force-stop jp.konami.pesam")
time.sleep(1)

# 2. Wipe AUTH and SYSTEM
print("[*] Wiping AUTH & SYSTEM...")
adb_cmd("shell su -c 'rm -rf /data/user/0/jp.konami.pesam/files/SaveData/AUTH/* /data/user/0/jp.konami.pesam/files/SaveData/SYSTEM/*'")
time.sleep(1)

# Verify wiped
wiped_auth = get_auth()
print(f"[*] Auth after wipe: {wiped_auth}")

# 3. Launch game
print("[*] Launching game...")
adb_cmd("shell monkey -p jp.konami.pesam -c android.intent.category.LAUNCHER 1")
print("[*] Waiting 15s for game engine to initialize...")
time.sleep(15)

cap("fresh_boot_15s")
print("[*] Screen captured: fresh_boot_15s.png")
