import subprocess
import time

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

def adb_cmd(cmd_list):
    return subprocess.run([ADB, "-s", DEV] + cmd_list, capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout

subprocess.run([ADB, "connect", DEV], capture_output=True)

# 1. Tap text box
print("[*] Tapping text box at (400, 525)...")
adb_cmd(["shell", "input", "tap", "400", "525"])
time.sleep(1)

# 2. Select all and delete (or send 25 backspaces)
print("[*] Clearing text...")
# Move cursor to end
adb_cmd(["shell", "input", "keyevent", "123"]) # KEYCODE_MOVE_END
for _ in range(20):
    adb_cmd(["shell", "input", "keyevent", "67"]) # KEYCODE_DEL
time.sleep(0.5)

# 3. Enter custom name
custom_name = "DJ_REROLL_01"
print(f"[*] Entering name: {custom_name}...")
adb_cmd(["shell", "input", "text", custom_name])
time.sleep(1)

# 4. Hide keyboard
adb_cmd(["shell", "input", "keyevent", "4"]) # KEYCODE_BACK
time.sleep(1)

# 5. Cap screen to verify name entered
with open("scratch/screen_name_entered.png", "wb") as f:
    subprocess.run([ADB, "-s", DEV, "exec-out", "screencap -p"], stdout=f)
print("[*] Saved scratch/screen_name_entered.png")
