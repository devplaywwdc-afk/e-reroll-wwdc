#!/usr/bin/env python3
"""Call an IDA MCP tool (clean instance 13338) and print the text/code cleanly."""
import sys, json, ida_mcp
tool = sys.argv[1]
args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
r = ida_mcp.call(tool, args)
res = (r or {}).get("result", {})
# prefer structuredContent
sc = res.get("structuredContent")
if sc is not None:
    # decompile returns {code:...} or list
    if isinstance(sc, dict) and "code" in sc:
        print(sc["code"]); sys.exit()
    if isinstance(sc, dict) and isinstance(sc.get("result"), list):
        for it in sc["result"]:
            if isinstance(it, dict) and "code" in it:
                print("// ---", it.get("addr"), "---"); print(it["code"])
        sys.exit()
    print(json.dumps(sc, ensure_ascii=False, indent=1)[:12000]); sys.exit()
for c in res.get("content", []):
    if c.get("type") == "text":
        print(c["text"][:12000])
