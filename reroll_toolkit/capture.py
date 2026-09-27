#!/usr/bin/env python3
"""
capture.py — drive hook_capture.js against eFootball and decode create_user.

On NATIVE arm64 (Apple-silicon emulator / real device) frida hooks are reliable,
so prefer SPAWN mode to catch the boot-time account-creation flow:

    # 1. push + run frida-server (arm64) on the device as root, then:
    python capture.py --spawn         # spawn game, hook from boot, capture
    python capture.py                 # attach to a running game instead
    python capture.py --gadget        # connect to a baked frida-gadget on :27042

Outputs:
    wire/<ts>_<dir>_<cmd>.json   decoded gRPC (protobuf + base64 + msgpack)
    create_dump.json             serializer struct dump (hash/client_version/eula)
    capture.log
"""
import os, sys, json, time, base64, struct, argparse, frida
try:
    import msgpack
except ImportError:
    msgpack = None
    print("WARN: pip install msgpack  (payloads will be saved raw, not decoded)")

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = "jp.konami.pesam"
SCRIPT = os.path.join(HERE, "hook_capture.js")
WIRE = os.path.join(HERE, "wire"); os.makedirs(WIRE, exist_ok=True)
DUMP = os.path.join(HERE, "create_dump.json")
LOG = open(os.path.join(HERE, "capture.log"), "w", encoding="utf-8", buffering=1)

def log(*a):
    line = " ".join(str(x) for x in a); print(line, flush=True); LOG.write(line + "\n")

def _varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1; v |= (x & 0x7f) << s
        if not x & 0x80: return v, i
        s += 7

def decode_grpc(buf):
    """gRPC frame -> {cmd, seq, data(dict)}.  frame = 1B flag + 4B len + protobuf."""
    try:
        if len(buf) < 5: return {"_err": "short"}
        ln = struct.unpack(">I", buf[1:5])[0]; pb = buf[5:5 + ln]
        i = 0; cmd = None; seq = None; b64 = None
        while i < len(pb):
            tag = pb[i]; i += 1; fn = tag >> 3; wt = tag & 7
            if wt == 0:
                val, i = _varint(pb, i)
                if fn == 2: seq = val
            elif wt == 2:
                l, i = _varint(pb, i); sub = pb[i:i + l]; i += l
                if fn == 1: cmd = sub.decode("utf-8", "replace")
                elif fn == 3: b64 = sub
            else:
                break
        data = None
        if b64 and msgpack:
            try: data = msgpack.unpackb(base64.b64decode(b64), raw=False)
            except Exception as e: data = {"_decode_err": str(e), "b64": b64.decode("ascii", "replace")}
        return {"cmd": cmd, "seq": seq, "data": data}
    except Exception as e:
        return {"_err": str(e)}

def on_message(msg, data):
    if msg.get("type") == "send":
        p = msg.get("payload", {})
        tag = p.get("tag")
        if tag == "WIRE":
            dec = decode_grpc(data) if data else None
            fn = os.path.join(WIRE, f"{time.strftime('%H%M%S')}_{p['dir']}_{p['cmd']}.json")
            json.dump({"dir": p["dir"], "cmd": p["cmd"], "n": p["n"], "decoded": dec},
                      open(fn, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)
            if p["cmd"] in ("CMD_CREATE_USER", "CMD_LOGIN") and p["dir"] == "w" and dec and dec.get("data"):
                d = dec["data"]
                log(f"\n===== {p['cmd']} REQUEST (decoded) -> {os.path.basename(fn)} =====")
                for k in ("hash", "client_version", "user_eula_season", "lang", "region", "platform"):
                    log(f"    {k:20} = {d.get(k)}")
                ei = d.get("user_eula_info") or {}
                log(f"    eula_version         = {json.dumps(ei.get('eula_version'), ensure_ascii=False)}")
                log(f"    eula_country_age_kind= {ei.get('eula_country_age_kind')}")
                log("    (full payload in the wire/ json)\n")
            else:
                log(f"[wire] {p['dir']} {p['cmd']} ({p['n']}B) -> {os.path.basename(fn)}")
        elif tag == "DUMP":
            json.dump(p["fields"], open(DUMP, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
            log(f"[serializer DUMP] hash={p['fields'].get('hash')} ver={p['fields'].get('client_version')} -> create_dump.json")
        elif tag == "LOG":
            log("[js]", p.get("msg"))
    elif msg.get("type") == "error":
        log("[error]", msg.get("stack") or msg.get("description"))
    elif msg.get("type") == "log":
        log("[js]", msg.get("payload"))

def get_device():
    return frida.get_usb_device(timeout=15)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spawn", action="store_true", help="spawn the game (best: catches boot-time create_user)")
    ap.add_argument("--gadget", action="store_true", help="connect to a baked frida-gadget on 127.0.0.1:27042")
    ap.add_argument("--gadget-addr", default="127.0.0.1:27042")
    args = ap.parse_args()
    code = open(SCRIPT, encoding="utf-8").read()

    if args.gadget:
        dev = frida.get_device_manager().add_remote_device(args.gadget_addr)
        ps = dev.enumerate_processes(); log("[*] gadget procs:", [(p.pid, p.name) for p in ps])
        sess = dev.attach(ps[0].pid)
        scr = sess.create_script(code); scr.on("message", on_message); scr.load()
        try: dev.resume(ps[0].pid)
        except Exception: pass
    elif args.spawn:
        dev = get_device()
        pid = dev.spawn([PKG]); log("[*] spawned", pid)
        sess = dev.attach(pid)
        scr = sess.create_script(code); scr.on("message", on_message); scr.load()
        dev.resume(pid); log("[*] resumed — hooks live from boot")
    else:
        dev = get_device()
        # attach by pid (frida process name may be the app label, not the package)
        pid = None
        for p in dev.enumerate_processes():
            if p.name == PKG or PKG in p.name or p.name.lower().startswith("efootball"):
                pid = p.pid; break
        if not pid: log("[!] game not running; launch it or use --spawn"); return
        sess = dev.attach(pid); log("[*] attached", pid)
        scr = sess.create_script(code); scr.on("message", on_message); scr.load()

    log("[*] READY — trigger guest creation (Title -> EULA -> Nickname -> confirm).")
    log("[*] Watch capture.log; decoded create_user lands in wire/ and create_dump.json")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        log("bye")

if __name__ == "__main__":
    main()
