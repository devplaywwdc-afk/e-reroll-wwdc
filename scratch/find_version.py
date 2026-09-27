import subprocess
import re

# Read maps of pid 13571
cmd = "adb -s emulator-5556 shell \"su -c 'grep rw-p /proc/$(pidof jp.konami.pesam)/maps | head -n 30'\""
r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
print("Heap/data mappings:")
for line in r.stdout.splitlines()[:15]:
    print(" ", line)

# Search for client_version pattern in heap
search_script = """
import re
pid = subprocess.check_output(['pidof', 'jp.konami.pesam']).decode().strip()
with open(f'/proc/{pid}/maps') as f:
    maps = f.readlines()

for line in maps:
    if '[heap]' in line or '[anon:.bss]' in line or 'rw-p' in line:
        parts = line.split()
        start, end = [int(x, 16) for x in parts[0].split('-')]
        size = end - start
        if size > 10 * 1024 * 1024: continue # skip giant maps
        try:
            with open(f'/proc/{pid}/mem', 'rb', 0) as mem:
                mem.seek(start)
                buf = mem.read(size)
                idx = 0
                while True:
                    pos = buf.find(b'client_version', idx)
                    if pos == -1: break
                    context = buf[max(0, pos-20):min(len(buf), pos+60)]
                    print('FOUND:', repr(context))
                    idx = pos + 14
        except Exception:
            pass
"""
cmd2 = f"adb -s emulator-5556 shell \"su -c \\\"python3 -c '{search_script}' 2>/dev/null || true\\\"\""
r2 = subprocess.run(cmd2, capture_output=True, text=True, shell=True)
print("Search results:")
print(r2.stdout or r2.stderr)
