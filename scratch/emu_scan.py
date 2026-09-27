
import sys

pid = None
with open('/proc/mounts') as f: pass
import os
for p in os.listdir('/proc'):
    if p.isdigit():
        try:
            with open(f'/proc/{p}/cmdline', 'rb') as f:
                if b'jp.konami.pesam' in f.read():
                    pid = int(p)
                    break
        except: pass

print(f"Found eFootball PID: {pid}")
if not pid:
    sys.exit(1)

with open(f"/proc/{pid}/maps") as f:
    lines = f.readlines()

count = 0
for line in lines:
    parts = line.split()
    if len(parts) < 2: continue
    perms = parts[1]
    if not ('r' in perms and 'w' in perms): continue
    addr_range = parts[0]
    start_str, end_str = addr_range.split('-')
    start = int(start_str, 16)
    end = int(end_str, 16)
    size = end - start
    if size > 30 * 1024 * 1024: continue
    
    try:
        with open(f"/proc/{pid}/mem", "rb", 0) as mem:
            mem.seek(start)
            chunk = mem.read(size)
            idx = 0
            while True:
                found = chunk.find(b"CMD_LOGIN", idx)
                if found == -1: break
                idx = found + 9
                snip_start = max(0, found - 16)
                snip_end = min(len(chunk), found + 600)
                print(f"--- MATCH at 0x{start+found:x} ---")
                print(repr(chunk[snip_start:snip_end]))
                count += 1
                if count >= 10:
                    sys.exit(0)
    except Exception as e:
        pass
