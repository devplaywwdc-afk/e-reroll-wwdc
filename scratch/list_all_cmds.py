import json

with open("all_flows_clean.json", "r", encoding="utf-8") as f:
    flows = json.load(f)

print(f"Total flows: {len(flows)}")
unique_cmds = set()
for i, flow in enumerate(flows):
    req_body = flow.get("request", {}).get("body_json") or flow.get("request", {}).get("body_raw")
    resp_body = flow.get("response", {}).get("body_json") or flow.get("response", {}).get("body_raw")
    
    # check command in request body
    cmd_name = None
    if isinstance(req_body, list):
        for msg in req_body:
            if isinstance(msg, dict):
                c = msg.get("1") or msg.get("cmd") or msg.get("command")
                if c:
                    unique_cmds.add(str(c))
                    print(f"[{i}] REQ CMD: {c}")
    elif isinstance(req_body, dict):
        c = req_body.get("1") or req_body.get("cmd") or req_body.get("command")
        if c:
            unique_cmds.add(str(c))
            print(f"[{i}] REQ CMD: {c}")

print("Unique commands:", sorted(list(unique_cmds)))
