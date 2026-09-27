import urllib.request, json
url = 'http://127.0.0.1:13337/output/8f2a5967-37a6-4451-b7a4-2a4482782ba2.json'
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read().decode())
lines = data.get('asm', {}).get('lines', [])
for l in lines[:100]:
    refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
    names = ' '.join([ref.get('name','') for ref in l.get('refs', []) if ref.get('name')])
    ins = l.get('instruction', '')
    if refs or names or any(k in ins for k in ["BL ", "ADRL ", "LDR ", "STR "]):
        print(f"{l['addr']}: {ins} // {refs} {names}")
