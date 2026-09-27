#!/usr/bin/env python3
"""Connect to ZygiskFrida gadget (listen/wait) on 127.0.0.1:27052, load hook_full.js, resume."""
import os, json, time, frida

ADDR = "127.0.0.1:27052"
SCRIPT = os.path.join(os.path.dirname(__file__), "hook_full.js")
OUT = os.path.join(os.path.dirname(__file__), "create_dump.json")
LOG = os.path.join(os.path.dirname(__file__), "capture_live.log")

logf = open(LOG, "w", encoding="utf-8", buffering=1)
def log(*a):
    line = " ".join(str(x) for x in a); print(line, flush=True); logf.write(line+"\n")

dumps = []
def on_message(msg, data):
    if msg.get("type") == "send":
        p = msg.get("payload", {})
        if isinstance(p, dict) and p.get("tag") == "CREATE_USER_DUMP":
            d = p["data"]; dumps.append(d)
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(dumps, f, indent=2, ensure_ascii=False)
            log("[CAPTURED] hash=%s ver=%s eula=%s" % (
                d["fields"].get("hash"), d["fields"].get("client_version"),
                json.dumps(d.get("eula_version"), ensure_ascii=False)))
        elif isinstance(p, dict) and p.get("tag") == "LOG":
            log("[js]", p.get("msg"))
        else:
            log("[send]", json.dumps(p, ensure_ascii=False))
    elif msg.get("type") == "error":
        log("[error]", msg.get("stack") or msg.get("description"))

def main():
    mgr = frida.get_device_manager()
    dev = None
    for _ in range(40):
        try:
            dev = mgr.add_remote_device(ADDR)
            procs = dev.enumerate_processes()
            if procs:
                break
        except Exception as e:
            time.sleep(0.5)
    if not dev:
        log("[!] could not connect to gadget at", ADDR); return
    procs = dev.enumerate_processes()
    log("[*] gadget procs:", [(p.pid, p.name) for p in procs])
    target = procs[0]  # gadget exposes the single injected process
    log("[*] attaching to", target.pid, target.name)
    sess = dev.attach(target.pid)
    with open(SCRIPT, "r", encoding="utf-8") as f:
        code = f.read()
    scr = sess.create_script(code, runtime="v8")
    scr.on("message", on_message); scr.load()
    log("[*] script loaded — resuming app")
    try:
        dev.resume(target.pid)
    except Exception as e:
        log("[!] resume:", e)
    log("[*] READY — wait for game to load, then create a fresh guest ID.")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
