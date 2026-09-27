import json, re

with open("all_flows_clean.json", "r", encoding="utf-8") as f:
    flows = json.load(f)

print(f"Total flows: {len(flows)}")
cmds = set()
flow_info = []

for i, flow in enumerate(flows):
    req_text = flow.get("request", {}).get("body_text", "")
    res_text = flow.get("response", {}).get("body_text", "")
    
    matches_req = re.findall(r'#1 str: "(CMD_[A-Z0-9_]+)"', req_text)
    matches_res = re.findall(r'#1 str: "(CMD_[A-Z0-9_]+)"', res_text)
    
    found = set(matches_req + matches_res)
    for c in found:
        cmds.add(c)
    if found:
        flow_info.append((i, flow.get("path"), list(found)))

print("Unique CMDs:", sorted(list(cmds)))
print("\nFlows matching commands:")
for i, path, c_list in flow_info:
    if any(c != "CMD_HEARTBEAT_GRPC" for c in c_list):
        print(f"Flow {i}: {c_list}")
