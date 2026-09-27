import json

with open('all_flows_clean.json', 'r', encoding='utf-8') as f:
    flows = json.load(f)

cmd_counts = {}
for f in flows:
    req_j = f.get('request', {}).get('body_json', '')
    if req_j:
        try:
            obj = json.loads(req_j)
            f1 = obj.get("1")
            f3 = obj.get("3")
            msgid = None
            if isinstance(f3, dict):
                msgid = f3.get("msgid")
            cmd = msgid or f1 or "UNKNOWN"
            cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1
        except Exception:
            pass

print("Distinct commands in flows:")
for cmd, count in sorted(cmd_counts.items(), key=lambda x: x[1], reverse=True):
    print(f"  {cmd}: {count}")
