import subprocess
import time
import os
import json
from PIL import Image

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

def ensure_connected():
    subprocess.run([ADB, "connect", DEV], capture_output=True)

def cmd(args):
    ensure_connected()
    r = subprocess.run([ADB, "-s", DEV] + args, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout.strip()

def tap(x, y, delay=2.0):
    print(f"[*] Tap ({x}, {y})")
    cmd(["shell", "input", "tap", str(x), str(y)])
    time.sleep(delay)

def type_text(text, delay=1.0):
    print(f"[*] Type: {text}")
    cmd(["shell", "input", "text", text])
    time.sleep(delay)

def keyevent(code, delay=1.0):
    cmd(["shell", "input", "keyevent", str(code)])
    time.sleep(delay)

def cap(name):
    cmd(["shell", "screencap", "-p", f"/sdcard/{name}.png"])
    out = os.path.join("scratch", f"{name}.png")
    subprocess.run([ADB, "-s", DEV, "pull", f"/sdcard/{name}.png", out], capture_output=True)
    print(f"[*] Pulled: {out}")
    return out

def get_auth():
    raw = cmd(["shell", "su", "-c", "cat /data/user/0/jp.konami.pesam/files/SaveData/AUTH/online_user_id_data.dat"])
    try:
        return json.loads(raw)
    except:
        return None

if __name__ == "__main__":
    ensure_connected()
    # Step 1: Tap Done on Language screen
    tap(1150, 675, delay=3.0)
    cap("step1_after_lang")
