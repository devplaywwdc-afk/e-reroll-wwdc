import urllib.request
import json

url = 'http://127.0.0.1:13337/output/1b6f83b9-f232-4635-81d4-50d6199b7d1b.json'
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read().decode())

lines = data.get('asm', {}).get('lines', [])

# Print lines between 761cd80 and 761ce90
start_print = False
for l in lines:
    addr = l.get('addr', '')
    if '761cd80' in addr:
        start_print = True
    if start_print:
        refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
        names = ' '.join([ref.get('name','') for ref in l.get('refs', []) if ref.get('name')])
        ins = l.get('instruction', '')
        print(f"{addr}: {ins}  // {refs} {names}")
        if '761ceb4' in addr:
            break
