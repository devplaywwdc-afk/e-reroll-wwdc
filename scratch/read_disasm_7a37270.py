import urllib.request, json
url = 'http://127.0.0.1:13337/output/0b427241-880f-4539-b506-b2aef972895e.json'
with urllib.request.urlopen(url) as r:
    data = json.loads(r.read().decode())
lines = data.get('asm', {}).get('lines', [])
for l in lines:
    addr = l.get('addr', '')
    if any(addr.startswith(prefix) for prefix in ['7a3725', '7a3726', '7a3727', '7a3728', '7a3729', '7a372a', '7a372b']):
        refs = ' '.join([ref.get('string','') for ref in l.get('refs', []) if ref.get('string')])
        names = ' '.join([ref.get('name','') for ref in l.get('refs', []) if ref.get('name')])
        print(f"{addr}: {l.get('instruction','')} // {refs} {names}")
