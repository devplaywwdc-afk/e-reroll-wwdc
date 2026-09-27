
import sys, re

pid = 9650
with open(f'/proc/{pid}/maps') as f:
    maps = f.readlines()

# look for session_id (alphanumeric string ~32 chars) or CMD_ in writable memory
found_tokens = set()
for m in maps:
    parts = m.split()
    if len(parts) < 2: continue
    perms = parts[1]
    if 'r' not in perms or 'w' not in perms: continue
    start, end = [int(x, 16) for x in parts[0].split('-')]
    size = end - start
    if size > 20 * 1024 * 1024 or size < 4096: continue
    
    try:
        with open(f'/proc/{pid}/mem', 'rb', 0) as mem:
            mem.seek(start)
            data = mem.read(size)
            # search for session_id key
            for match in re.finditer(b'session_id', data):
                pos = match.start()
                ctx = data[pos:pos+100]
                found_tokens.add(repr(ctx))
    except:
        pass

for t in list(found_tokens)[:10]:
    print('TOKEN:', t)
