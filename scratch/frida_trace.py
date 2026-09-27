#!/usr/bin/env python3
import os, json, time, frida
ADDR="127.0.0.1:27042"
SCRIPT=os.path.join(os.path.dirname(__file__),"hook_trace.js")
LOG=os.path.join(os.path.dirname(__file__),"trace_live.log")
logf=open(LOG,"w",encoding="utf-8",buffering=1)
def log(*a):
    line=" ".join(str(x) for x in a); print(line,flush=True); logf.write(line+"\n")
def on_message(msg,data):
    if msg.get("type")=="send":
        p=msg.get("payload",{})
        if p.get("tag")=="TRACE": log("[TRACE]"," ".join(p.get("items",[])))
        elif p.get("tag")=="CREATE_FIRED": log("[!!!] CREATE_USER serializer fired a1="+p.get("a1"))
        elif p.get("tag")=="LOG": log("[js]",p.get("msg"))
        else: log("[send]",json.dumps(p,ensure_ascii=False))
    elif msg.get("type")=="error": log("[error]",msg.get("stack") or msg.get("description"))
def main():
    mgr=frida.get_device_manager(); dev=None
    for _ in range(40):
        try:
            dev=mgr.add_remote_device(ADDR)
            if dev.enumerate_processes(): break
        except Exception: time.sleep(0.5)
    ps=dev.enumerate_processes(); log("[*] procs:",[(p.pid,p.name) for p in ps])
    tgt=ps[0]; sess=dev.attach(tgt.pid); log("[*] attached",tgt.pid)
    scr=sess.create_script(open(SCRIPT,encoding="utf-8").read(),runtime="v8")
    scr.on("message",on_message); scr.load()
    log("[*] READY — create guest now")
    while True: time.sleep(1)
main()
