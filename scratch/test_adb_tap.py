import subprocess
import time

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

def run(cmd):
    return subprocess.run([ADB, "-s", DEV] + cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout

subprocess.run([ADB, "connect", DEV], capture_output=True)

# Let's test tapping English or another language in the list to see if the checkmark moves!
# "Bahasa Indonesia" is above "ไทย"
# In fresh_boot_15s.png, Bahasa Indonesia is at Y = ~530
print("[*] Tapping Bahasa Indonesia at (500, 520)...")
run(["shell", "input", "tap", "500", "520"])
time.sleep(1)

# Cap screen
run(["shell", "screencap", "-p", "/sdcard/test_tap_result.png"])
subprocess.run([ADB, "-s", DEV, "pull", "/sdcard/test_tap_result.png", "scratch/test_tap_result.png"], capture_output=True)
print("[*] Pulled scratch/test_tap_result.png")
