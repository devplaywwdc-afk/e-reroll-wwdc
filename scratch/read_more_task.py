import urllib.request
import json

url = "http://127.0.0.1:13337/output/b549b988-5f3a-40e5-9a13-c1e4f24af59e.json"
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read())

lines = data.get('asm', {}).get('lines', [])
for l in lines[100:300]:
    refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
    ins = l.get('instruction', '')
    if any(k in ins for k in ["BL ", "ADRL", "LDR ", "STR "]):
        print(f"{l['addr']}: {ins} {refs}")
