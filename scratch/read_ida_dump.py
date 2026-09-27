import urllib.request
import json

url = "http://127.0.0.1:13337/output/7a436914-c902-496e-b4f3-caad2451ba18.json"
try:
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    lines = data.get('asm', {}).get('lines', [])
    print(f"Total lines: {len(lines)}")
    for i, l in enumerate(lines[:120]):
        ins = l.get('instruction', '')
        refs = l.get('refs', [])
        r_str = ' | '.join([r.get('string','') for r in refs if r.get('string')])
        print(f"{l['addr']}: {ins}  {r_str}")
except Exception as e:
    print(f"Error: {e}")
