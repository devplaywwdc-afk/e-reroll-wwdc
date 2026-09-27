import urllib.request
import json
import re

req = urllib.request.urlopen('http://127.0.0.1:13337/output/ae2e611c-1553-4770-8d77-c648f76f121d.json')
data = json.loads(req.read().decode())
code = data.get('code', '')

# Find all string literals
keys = re.findall(r'"([^"\\]+)"', code)
print(f"Total string literals in sub_76B1B28: {len(keys)}")
seen = set()
for k in keys:
    if k not in seen and len(k) >= 2:
        seen.add(k)
        print(f"  - {k}")

