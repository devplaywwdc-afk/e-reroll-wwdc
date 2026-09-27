import json

data = json.load(open("exported_flows.json", "r", encoding="utf-8"))
print("Summary:", data.get("summary"))
flows = data.get("flows", [])
print(f"Total flows: {len(flows)}")
for i, f in enumerate(flows):
    req = f.get("request", {})
    res = f.get("response", {})
    print(f"[{i}] {req.get('method')} {req.get('url')} status={res.get('status_code')}")
    body = req.get("body_json") or req.get("body_text") or ""
    if body:
        print("    REQ:", str(body)[:200])
