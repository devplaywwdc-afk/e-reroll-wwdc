import json

with open('all_flows_clean.json', 'r', encoding='utf-8') as f:
    flows = json.load(f)

print(f"Total flows: {len(flows)}")
for i in range(130, len(flows)):
    f = flows[i]
    fid = f.get('id')
    proto = f.get('proto')
    rpc = f.get('rpc')
    path = f.get('path')
    req_j = f.get('request', {}).get('body_json', '')
    res_j = f.get('response', {}).get('body_json', '')
    print(f"Flow #{i} (id={fid}, proto={proto}, rpc={rpc}):")
    if req_j:
        print("  REQ:", req_j.replace("\n", " ")[:200])
    if res_j:
        print("  RES:", res_j.replace("\n", " ")[:200])
