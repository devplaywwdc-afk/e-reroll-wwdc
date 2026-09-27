import subprocess
import re

# Read /proc/9650/maps via ADB
r = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'shell', 'su', '-c', 'cat /proc/9650/maps'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
maps = r.stdout.splitlines()

ranges = []
for line in maps:
    parts = line.split()
    if len(parts) < 2: continue
    perms = parts[1]
    if 'r' in perms and 'w' in perms and '[anon:' in line:
        start_s, end_s = parts[0].split('-')
        start = int(start_s, 16)
        end = int(end_s, 16)
        size = end - start
        if 4096 <= size <= 4 * 1024 * 1024:
            ranges.append((start, size))

print(f"Found {len(ranges)} anonymous writable memory regions to inspect.")

# Read chunks and scan for session_id
found_sessions = []
for start, size in ranges[:40]:
    # dump via dd
    cmd = f"su -c 'dd if=/proc/9650/mem bs=4096 skip={start//4096} count={size//4096} 2>/dev/null'"
    proc = subprocess.run(['adb', '-s', '127.0.0.1:5557', 'exec-out', cmd], capture_output=True)
    chunk = proc.stdout
    if b'session_id' in chunk:
        idx = 0
        while True:
            pos = chunk.find(b'session_id', idx)
            if pos == -1: break
            idx = pos + 10
            ctx = chunk[pos:pos+80]
            print(f"FOUND session_id at 0x{start+pos:x}: {ctx}")
            found_sessions.append((start+pos, ctx))

print(f"Total session_id occurrences found: {len(found_sessions)}")
