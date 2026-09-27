import json

with open("all_flows_clean.json", "r", encoding="utf-8") as f:
    flows = json.load(f)

print("Flow keys:", flows[0].keys())
print("Request keys:", flows[0].get("request", {}).keys())
for i in range(min(5, len(flows))):
    print(f"\n--- Flow {i} ---")
    print("URL/path:", flows[i].get("path"), flows[i].get("url"), flows[i].get("host"))
    print("Request summary:", str(flows[i].get("request"))[:300])
    print("Response summary:", str(flows[i].get("response"))[:300])
