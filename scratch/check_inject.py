import subprocess
import time

ADB = r"G:\MuMuPlayerGlobal\nx_device\12.0\shell\adb.exe"
DEV = "127.0.0.1:5557"

subprocess.run([ADB, "connect", DEV], capture_output=True)

# Check what input tap 100 200 generates in getevent
p = subprocess.Popen([ADB, "-s", DEV, "shell", "getevent -lt /dev/input/event4"], stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
time.sleep(1)
subprocess.run([ADB, "-s", DEV, "shell", "input tap 100 200"], capture_output=True)
time.sleep(1)
p.terminate()
out, _ = p.communicate()
print("getevent output:")
print(out)
