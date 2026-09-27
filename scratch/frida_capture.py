#!/usr/bin/env python3
"""Spawn eFootball, load hook_serializer.js, write CMD_CREATE_USER dumps to host file."""
import sys, os, json, time, frida

PKG = "jp.konami.pesam"
SCRIPT = os.path.join(os.path.dirname(__file__), "hook_serializer.js")
OUT = os.path.join(os.path.dirname(__file__), "create_dump.json")
LOG = os.path.join(os.path.dirname(__file__), "capture_live.log")

logf = open(LOG, "w", encoding="utf-8", buffering=1)  # line-buffered
def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    logf.write(line + "\n")

dumps = []

def on_message(msg, data):
    if msg.get("type") == "send":
        p = msg.get("payload", {})
        if isinstance(p, dict) and p.get("tag") == "CREATE_USER_DUMP":
            d = p["data"]
            dumps.append(d)
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(dumps, f, indent=2, ensure_ascii=False)
            log("[CAPTURED] hash=%s ver=%s eula=%s" % (
                d["fields"].get("hash"), d["fields"].get("client_version"),
                json.dumps(d.get("eula_version"), ensure_ascii=False)))
        else:
            log("[send]", json.dumps(p, ensure_ascii=False))
    elif msg.get("type") == "error":
        log("[error]", msg.get("stack") or msg.get("description"))

def main():
    dev = frida.get_usb_device(timeout=10)
    log("[*] device:", dev.name)
    pid = dev.spawn([PKG])
    log("[*] spawned pid", pid)
    sess = dev.attach(pid)
    with open(SCRIPT, "r", encoding="utf-8") as f:
        code = f.read()
    scr = sess.create_script(code, runtime="v8")
    scr.on("message", on_message)
    scr.load()
    log("[*] script loaded, resuming")
    dev.resume(pid)
    log("[*] READY — create a fresh guest ID in the game now. capturing...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
