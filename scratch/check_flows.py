import urllib.request, json
from collections import Counter

res = urllib.request.urlopen('http://127.0.0.1:8770/api/flows', timeout=5)
flows = json.loads(res.read().decode()).get('flows', [])

print(f"Total flows: {len(flows)}")
rpcs = [f.get('rpc') for f in flows if f.get('rpc')]
print("RPC counts:")
for rpc, cnt in Counter(rpcs).most_common():
    print(f"  {rpc}: {cnt}")

print("\nLast 25 flows:")
for f in flows[-25:]:
    t = f.get('time')
    rpc = f.get('rpc')
    rq = f.get('req_size')
    rs = f.get('res_size')
    print(f"  {t} | {rpc:<35} | req:{rq} res:{rs}")
