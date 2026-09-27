import urllib.request
import json

url = "http://127.0.0.1:13337/output/7a436914-c902-496e-b4f3-caad2451ba18.json"
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read())

lines = data.get('asm', {}).get('lines', [])
for i, l in enumerate(lines):
    ins = l.get('instruction', '')
    if any(k in ins for k in ["aLang", "LANG", "nation", "THA", "country", "BL "]):
        refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
        print(f"{l['addr']}: {ins}  {refs}")
    if i > 250: break
