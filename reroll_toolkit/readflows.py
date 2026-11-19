import sys,re,json
sys.path.insert(0,r"C:/Users/jaras/OneDrive/Desktop/Efootball/pes_api")
import gate_crypto as gc
try: import msgpack
except: msgpack=None
from mitmproxy.io import FlowReader
f=r"C:/Users/jaras/AppData/Local/Temp/claude/b2d87a41fc65cc84/258001c8-85a9-4227-b759-79022ccca673/scratchpad/flows.mitm"
def dec(body):
    for fn in ('decrypt_body','decrypt'):
        if hasattr(gc,fn):
            try:
                r=getattr(gc,fn)(body)
                if isinstance(r,(bytes,bytearray)) and msgpack:
                    try: return msgpack.unpackb(bytes(r),raw=False,strict_map_key=False)
                    except: return r
                return r
            except Exception as e: pass
    return None
n=0
for fl in FlowReader(open(f,'rb')).stream():
    try:
        req=fl.request
        if 'konami' not in req.host: continue
        cmd=(re.search(r'gate_(CMD_[A-Z_]+)\.php',req.path) or [None,req.path])[1]
        o=dec(req.raw_content)
        mid=o.get('msgid') if isinstance(o,dict) else None
        n+=1
        print('#%d %s'%(n,cmd))
        if isinstance(o,dict):
            print('   ', json.dumps(o,ensure_ascii=False,default=str)[:900])
    except Exception as e:
        print('err',e)
print('total konami reqs:',n)
