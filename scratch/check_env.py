import subprocess
import psutil

print("=== CHECKING EMULATOR PROCESSES ===")
found = False
for p in psutil.process_iter(['pid', 'name']):
    try:
        n = p.info['name'].lower()
        if any(k in n for k in ['dnplayer', 'ld', 'nox', 'mumu', 'qemu', 'hd-player', 'bluestacks', 'nemu']):
            print(f"PID {p.info['pid']}: {p.info['name']}")
            found = True
    except Exception:
        pass

if not found:
    print("No emulator processes found running.")

print("\n=== ADB DEVICES ===")
r = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
print(r.stdout)

for port in [5555, 5556, 16384, 7555, 5554]:
    r = subprocess.run(['adb', 'connect', f'127.0.0.1:{port}'], capture_output=True, text=True)
    out = r.stdout.strip()
    if 'connected' in out.lower() and 'cannot' not in out.lower():
        print(f"Connected to port {port}: {out}")

r = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
print("Updated adb devices:")
print(r.stdout)
