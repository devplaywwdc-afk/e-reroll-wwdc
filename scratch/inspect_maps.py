import subprocess

subprocess.run(['adb', 'connect', '127.0.0.1:5557'])
r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', 'cat /proc/4920/maps'], capture_output=True, text=True, errors='ignore')
lines = [l for l in r.stdout.splitlines() if 'heap' in l or 'libc_malloc' in l or 'scudo' in l or 'anon:' in l]
print(f"Total relevant mapping lines: {len(lines)}")
for l in lines[:15]:
    print(" ", l)
