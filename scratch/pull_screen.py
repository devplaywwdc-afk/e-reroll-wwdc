import subprocess
import os

def run_adb(cmd_list):
    full_cmd = "adb connect 127.0.0.1:7555 >nul 2>&1 && " + " ".join(cmd_list)
    res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    return res

run_adb(["adb", "-s", "127.0.0.1:7555", "shell", "screencap", "-p", "/sdcard/screen_now.png"])
run_adb(["adb", "-s", "127.0.0.1:7555", "pull", "/sdcard/screen_now.png", "screen_live.png"])

if os.path.exists("screen_live.png"):
    print(f"Success! screen_live.png size: {os.path.getsize('screen_live.png')}")
else:
    print("Failed to pull screen_live.png")
