import urllib.request
import json

url = 'http://127.0.0.1:13337/output/1d9bca31-edc6-41ff-ba9e-9d17ca2bf5fe.json'
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read().decode())

lines = data.get('asm', {}).get('lines', [])
print(f'Total lines: {len(lines)}')

for i, l in enumerate(lines[:120]):
    refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
    names = ' '.join([ref.get('name','') for ref in l.get('refs', []) if ref.get('name')])
    ins = l.get('instruction', '')
    if refs or names or any(k in ins for k in ["BL ", "ADRL", "LDR ", "STR "]):
        print(f"{l['addr']}: {ins}  // {refs} {names}")
