import json

with open('all_flows_clean.json', 'r', encoding='utf-8') as f:
    flows = json.load(f)

for f in flows:
    if f.get('rpc') == 'CMD_HEARTBEAT_GRPC' and f.get('req_size', 0) > 0:
        req = f.get('request', {})
        res = f.get('response', {})
        print("Flow ID:", f.get('id'))
        print("REQ headers:", req.get('headers'))
        print("REQ hex:", req.get('body_hex')[:100])
        print("REQ json:", req.get('body_json'))
        print("RES headers:", res.get('headers'))
        print("RES hex:", res.get('body_hex')[:100])
        print("RES json:", res.get('body_json'))
        break
