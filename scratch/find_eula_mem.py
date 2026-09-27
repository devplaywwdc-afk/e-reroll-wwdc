import subprocess
import re

subprocess.run(['adb', 'connect', '127.0.0.1:5557'])
pid = 4920

r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', f'cat /proc/{pid}/maps'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
maps = r.stdout.splitlines()

ranges = []
for line in maps:
    parts = line.split()
    if len(parts) < 2: continue
    perms = parts[1]
    if 'r' in perms and 'w' in perms and ('[anon:' in line or '[heap]' in line):
        start_s, end_s = parts[0].split('-')
        start = int(start_s, 16)
        end = int(end_s, 16)
        size = end - start
        if 4096 <= size <= 8 * 1024 * 1024:
            ranges.append((start, size))

print(f"Scanning {len(ranges)} writable memory ranges for EULA and season...")

keywords = [b'eula_version', b'user_eula_season', b'CMD_CREATE_USER', b'eula_select_country_a3']

for start, size in ranges:
    cmd = f"dd if=/proc/{pid}/mem bs=4096 skip={start//4096} count={size//4096} 2>/dev/null"
    proc = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'exec-out', cmd], capture_output=True)
    chunk = proc.stdout
    for kw in keywords:
        if kw in chunk:
            pos = 0
            while True:
                pos = chunk.find(kw, pos)
                if pos == -1: break
                ctx = chunk[max(0, pos-20):min(len(chunk), pos+200)]
                print(f"FOUND [{kw.decode()}]: {ctx}")
                pos += len(kw)
