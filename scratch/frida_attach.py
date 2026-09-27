#!/usr/bin/env python3
"""Attach to running eFootball, load hook_serializer.js, dump CMD_CREATE_USER to host file."""
import os, json, time, subprocess, frida

ADB_SERIAL = "127.0.0.1:5557"
def adb_pid():
    try:
        out = subprocess.check_output(
            ["adb", "-s", ADB_SERIAL, "shell", "pidof", "jp.konami.pesam"],
            timeout=8).decode().strip()
        return int(out.split()[0]) if out else None
    except Exception:
        return None

PKG = "jp.konami.pesam"
SCRIPT = os.path.join(os.path.dirname(__file__), "hook_serializer.js")
OUT = os.path.join(os.path.dirname(__file__), "create_dump.json")
LOG = os.path.join(os.path.dirname(__file__), "capture_live.log")

logf = open(LOG, "w", encoding="utf-8", buffering=1)
def log(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True); logf.write(line + "\n")

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
        else:
            log("[js]", json.dumps(p, ensure_ascii=False))
    elif msg.get("type") == "error":
        log("[error]", msg.get("stack") or msg.get("description"))

def main():
    # connect to our isolated fs64 (adb-forwarded 127.0.0.1:27043), NOT the
    # ZygiskFrida ghost server on the default USB channel
    dev = frida.get_device_manager().add_remote_device("127.0.0.1:27043")
    log("[*] device:", dev.name)
    # wait for the process to exist (match by real pid, not frida name)
    pid = None
    for _ in range(60):
        pid = adb_pid()
        if pid: break
        time.sleep(0.5)
    if not pid:
        log("[!] process not found"); return
    log("[*] attaching to pid", pid)
    sess = dev.attach(pid)
    with open(SCRIPT, "r", encoding="utf-8") as f:
        code = f.read()
    scr = sess.create_script(code, runtime="v8")
    scr.on("message", on_message); scr.load()
    log("[*] READY — tap 'create new / guest ID' in the game. capturing...")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
