#!/usr/bin/env python3
"""Minimal MCP-over-HTTP client for the IDA Pro MCP server (streamable HTTP at /mcp).

Usage:
    python ida_mcp.py list
    python ida_mcp.py call <tool> '<json_args>'
    python ida_mcp.py health
"""
import sys, json, urllib.request

URL = "http://127.0.0.1:13337/mcp"
_sid = [None]
_id = [0]

def _post(method, params=None):
    _id[0] += 1
    body = json.dumps({"jsonrpc": "2.0", "id": _id[0], "method": method,
                       "params": params or {}}).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if _sid[0]:
        headers["mcp-session-id"] = _sid[0]
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.headers.get("mcp-session-id"):
            _sid[0] = r.headers.get("mcp-session-id")
        raw = r.read().decode("utf-8", "replace")
    # response may be JSON or SSE (event-stream). Extract the JSON-RPC payload.
    if raw.lstrip().startswith("{"):
        return json.loads(raw)
    out = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                try: out = json.loads(data)
                except Exception: pass
    return out

def _notify(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}).encode()
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if _sid[0]:
        headers["mcp-session-id"] = _sid[0]
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass

def init():
    r = _post("initialize", {"protocolVersion": "2024-11-05",
                             "capabilities": {},
                             "clientInfo": {"name": "efb", "version": "1"}})
    _notify("notifications/initialized")
    return r

def call(tool, args):
    init()
    return _post("tools/call", {"name": tool, "arguments": args})

def list_tools():
    init()
    return _post("tools/list")

def _print(o): print(json.dumps(o, indent=2, ensure_ascii=False)[:8000])

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        r = list_tools()
        tools = (r or {}).get("result", {}).get("tools", [])
        print("TOOLS (%d):" % len(tools))
        for t in tools:
            print(" -", t.get("name"), "—", (t.get("description") or "")[:70])
    elif cmd == "health":
        _print(call("server_health", {}))
    elif cmd == "call":
        tool = sys.argv[2]
        args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        _print(call(tool, args))
    else:
        print("usage: list | health | call <tool> '<json>'")
