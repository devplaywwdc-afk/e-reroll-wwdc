import json

with open('all_flows_clean.json', 'r', encoding='utf-8') as f:
    flows = json.load(f)

for f in flows:
    if f.get('rpc') == 'CommandStream':
        bj = f['request']['body_json']
        if isinstance(bj, str): bj = json.loads(bj)
        print("=== NON-HEARTBEAT REQUESTS ===")
        for i, item in enumerate(bj):
            cmd = item.get("1")
            if cmd != "CMD_HEARTBEAT_GRPC":
                print(f"REQ [{i:2d}] {cmd}")
                data = item.get("3")
                if isinstance(data, dict):
                    print(f"       data: {json.dumps(data)[:200]}")
        
        rbj = f['response']['body_json']
        if isinstance(rbj, str): rbj = json.loads(rbj)
        print("\n=== NON-HEARTBEAT RESPONSES ===")
        for i, item in enumerate(rbj):
            cmd = item.get("1")
            if cmd != "CMD_HEARTBEAT_GRPC":
                print(f"RES [{i:2d}] {cmd}")
                data = item.get("3")
                if isinstance(data, dict):
                    print(f"       data: {json.dumps(data)[:200]}")
