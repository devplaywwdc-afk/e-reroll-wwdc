import urllib.request
import json

url = "http://127.0.0.1:13337/output/b549b988-5f3a-40e5-9a13-c1e4f24af59e.json"
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read())

lines = data.get('asm', {}).get('lines', [])
print(f"Total lines: {len(lines)}")
for l in lines[:100]:
    refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
    print(f"{l['addr']}: {l['instruction']} {refs}")
