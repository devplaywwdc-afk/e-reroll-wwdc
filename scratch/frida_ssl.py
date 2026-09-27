#!/usr/bin/env python3
import os, json, time, base64, struct, frida
try: import msgpack
except Exception: msgpack=None

ADDR="127.0.0.1:27042"
SCRIPT=os.path.join(os.path.dirname(__file__),"hook_ssl2.js")
OUTDIR=os.path.join(os.path.dirname(__file__),"wire")
LOG=os.path.join(os.path.dirname(__file__),"ssl_live.log")
os.makedirs(OUTDIR,exist_ok=True)
logf=open(LOG,"w",encoding="utf-8",buffering=1)
def log(*a):
    line=" ".join(str(x) for x in a); print(line,flush=True); logf.write(line+"\n")

def rdvarint(b,i):
    v=0;s=0
    while True:
        x=b[i]; i+=1; v|=(x&0x7f)<<s
        if not x&0x80: break
        s+=7
    return v,i

def decode_grpc(buf):
    # buf: gRPC frame(s): 1-byte flag + 4-byte len + protobuf
    try:
        if len(buf)<5: return None
        ln=struct.unpack(">I",buf[1:5])[0]; pb=buf[5:5+ln]
        i=0; cmd=None; b64=None
        while i<len(pb):
            tag=pb[i]; i+=1; fn=tag>>3; wt=tag&7
            if wt==0:
                _,i=rdvarint(pb,i)
            elif wt==2:
                l,i=rdvarint(pb,i); sub=pb[i:i+l]; i+=l
                if fn==1: cmd=sub.decode('utf-8','replace')
                elif fn==3: b64=sub
        d=None
        if b64 and msgpack:
            try: d=msgpack.unpackb(base64.b64decode(b64),raw=False)
            except Exception as e: d={"_decode_err":str(e)}
        return {"cmd":cmd,"data":d}
    except Exception as e:
        return {"_err":str(e)}

def on_message(msg,data):
    if msg.get("type")=="send":
        p=msg.get("payload",{})
        if p.get("tag")=="WIRE":
            cmd=p.get("cmd"); dr=p.get("dir")
            dec=decode_grpc(data) if data else None
            ts=time.strftime("%H%M%S")
            fn=os.path.join(OUTDIR,f"{ts}_{dr}_{cmd}.json")
            rec={"dir":dr,"cmd":cmd,"n":p.get("n"),"decoded":dec}
            with open(fn,"w",encoding="utf-8") as f: json.dump(rec,f,indent=2,ensure_ascii=False,default=str)
            if cmd in ("CMD_CREATE_USER","CMD_LOGIN") and dr=="w":
                log(f"[*** {cmd} REQUEST ***] -> {fn}")
                if dec and dec.get("data"):
                    d=dec["data"]
                    for k in ("hash","client_version","user_eula_season","lang","region"):
                        log(f"    {k} = {d.get(k)}")
                    if isinstance(d.get("user_eula_info"),dict):
                        log(f"    eula_version = {json.dumps(d['user_eula_info'].get('eula_version'),ensure_ascii=False)}")
            else:
                log(f"[wire] {dr} {cmd} ({p.get('n')}B) -> {os.path.basename(fn)}")
        elif p.get("tag")=="LOG": log("[js]",p.get("msg"))
        else: log("[send]",json.dumps(p,ensure_ascii=False))
    elif msg.get("type")=="error": log("[error]",msg.get("stack") or msg.get("description"))
    elif msg.get("type")=="log": log("[js]",msg.get("payload"))

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
    try: dev.resume(tgt.pid); log("[*] resumed app (gadget wait)")
    except Exception as e: log("[!] resume:",e)
    log("[*] READY — hook active from boot; guest auto-create will be captured")
    while True: time.sleep(1)
main()
