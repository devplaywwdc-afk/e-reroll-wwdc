import json
import re

data = json.load(open('exported_flows.json', encoding='utf-8'))
f1588 = next((x for x in data['flows'] if x['id'] == 1588), None)
if f1588:
    req_t = f1588.get('request', {}).get('body_text', '')
    res_t = f1588.get('response', {}).get('body_text', '')
    
    print("=== FLOW 1588 ===")
    print("REQ cmds:", set(re.findall(r'#1 str: "([^"]+)"', req_t)))
    print("RES cmds:", set(re.findall(r'#1 str: "([^"]+)"', res_t)))

print("\n=== ALL FLOWS IN DATA ===")
for flow in data['flows']:
    path = flow.get('path', '')
    rpc = flow.get('rpc', '')
    cmd = flow.get('_cmd', '')
    if rpc or cmd:
        print(f"ID={flow['id']}: rpc={rpc} cmd={cmd} path={path}")
