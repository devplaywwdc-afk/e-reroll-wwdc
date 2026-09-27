import json, os

for fname in ["exported_flows.json", "all_flows.json", "all_flows_clean.json", "flows_summary.json"]:
    if os.path.exists(fname):
        sz = os.path.getsize(fname)
        print(f"File: {fname} ({sz} bytes)")
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                d = json.load(f)
                if isinstance(d, list):
                    print(f"  List of {len(d)} items")
                    for i, item in enumerate(d[:10]):
                        if isinstance(item, dict):
                            print(f"    [{i}] keys: {list(item.keys())[:5]}")
                            # check if command name is present
                            cmd = item.get('command') or item.get('cmd') or item.get('url') or item.get('path')
                            print(f"         cmd/url: {cmd}")
                elif isinstance(d, dict):
                    print(f"  Dict keys: {list(d.keys())}")
        except Exception as e:
            print(f"  Error loading: {e}")
