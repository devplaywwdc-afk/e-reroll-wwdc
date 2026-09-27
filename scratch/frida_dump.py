#!/usr/bin/env python3
"""Connect to baked gadget (127.0.0.1:27042), load hook_createuser.js, dump CMD_CREATE_USER."""
import os, json, time, frida

ADDR="127.0.0.1:27042"
SCRIPT=os.path.join(os.path.dirname(__file__),"hook_createuser.js")
OUT=os.path.join(os.path.dirname(__file__),"create_dump.json")
LOG=os.path.join(os.path.dirname(__file__),"dump_live.log")
logf=open(LOG,"w",encoding="utf-8",buffering=1)
def log(*a):
    line=" ".join(str(x) for x in a); print(line,flush=True); logf.write(line+"\n")

dumps=[]
def on_message(msg,data):
    if msg.get("type")=="send":
        p=msg.get("payload",{})
        if isinstance(p,dict) and p.get("tag")=="CREATE_USER_DUMP":
            d=p["data"]; dumps.append(d)
            with open(OUT,"w",encoding="utf-8") as f: json.dump(dumps,f,indent=2,ensure_ascii=False)
            log("[CAPTURED] hash=%s ver=%s eula=%s"%(d["fields"].get("hash"),
                d["fields"].get("client_version"), json.dumps(d.get("eula_version"),ensure_ascii=False)))
            log("  full fields:", json.dumps(d["fields"],ensure_ascii=False))
        elif isinstance(p,dict) and p.get("tag")=="LOG": log("[js]",p.get("msg"))
        else: log("[send]",json.dumps(p,ensure_ascii=False))
    elif msg.get("type")=="error": log("[error]",msg.get("stack") or msg.get("description"))

def main():
    mgr=frida.get_device_manager(); dev=None
    for _ in range(40):
        try:
            dev=mgr.add_remote_device(ADDR); ps=dev.enumerate_processes()
            if ps: break
        except Exception: time.sleep(0.5)
    if not dev: log("[!] cannot connect gadget",ADDR); return
    ps=dev.enumerate_processes(); log("[*] procs:",[(p.pid,p.name) for p in ps])
    tgt=ps[0]; log("[*] attaching",tgt.pid,tgt.name)
    sess=dev.attach(tgt.pid)
    scr=sess.create_script(open(SCRIPT,encoding="utf-8").read(),runtime="v8")
    scr.on("message",on_message); scr.load()
    log("[*] READY — tap through guest creation in the game.")
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt: pass

if __name__=="__main__": main()
