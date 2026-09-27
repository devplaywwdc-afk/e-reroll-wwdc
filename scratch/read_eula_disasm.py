import urllib.request
import json

url = 'http://127.0.0.1:13337/output/366e69ec-4c38-4739-bb11-bf570539ad3f.json'
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read())

lines = data.get('asm', {}).get('lines', [])
print(f"Total lines: {len(lines)}")
for l in lines[:120]:
    refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
    print(f"{l['addr']}: {l['instruction']} {refs}")
