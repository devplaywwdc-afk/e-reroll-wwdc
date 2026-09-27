import subprocess

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

c = subprocess.run([ADB, "connect", DEV], capture_output=True, text=True, encoding="utf-8", errors="ignore")
print("Connect:", c.stdout.strip())

r = subprocess.run([ADB, "-s", DEV, "shell", "dumpsys window"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
lines = [l for l in r.stdout.splitlines() if "mCurrentFocus" in l or "mFocusedApp" in l or "mFocusedWindow" in l]
print("Found lines:", len(lines))
for l in lines[:10]:
    print(" ", l)
