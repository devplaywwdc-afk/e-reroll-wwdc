"""mitmproxy addon: dump eFootball (konami) request/response bodies + decode.

The 11.0.1 client sends commands as HTTP POST to
  https://pes22-game.cs.konami.net/pes22/gate/gate_<CMD>
Body format is the same envelope as gRPC: protobuf(f1=cmd,f2=seq,f3=b64 msgpack).
This saves raw bodies to konami_wire/ and prints decoded create_user/login fields.

Run:  mitmdump --listen-host 0.0.0.0 --listen-port 8080 -s konami_dump.py
"""
import os, base64, struct, json
try:
    import msgpack
except Exception:
    msgpack = None

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "konami_wire")
os.makedirs(OUT, exist_ok=True)
_seq = [0]

def _varint(b, i):
    v = s = 0
    while True:
        x = b[i]; i += 1; v |= (x & 0x7f) << s
        if not x & 0x80: return v, i
        s += 7

def decode_envelope(buf):
    """Try protobuf(f1 str,f2 varint,f3 bytes b64->msgpack). Fallback: raw b64/msgpack."""
    try:
        pb = buf
        # some transports prepend a 5-byte gRPC frame header
        if len(pb) > 5 and pb[0] == 0 and struct.unpack(">I", pb[1:5])[0] <= len(pb):
            pb = pb[5:5 + struct.unpack(">I", buf[1:5])[0]]
        i = 0; cmd = None; b64 = None
        while i < len(pb):
            tag = pb[i]; i += 1; fn = tag >> 3; wt = tag & 7
            if wt == 0:
                _, i = _varint(pb, i)
            elif wt == 2:
                l, i = _varint(pb, i); sub = pb[i:i+l]; i += l
                if fn == 1: cmd = sub.decode("utf-8", "replace")
                elif fn == 3: b64 = sub
            else:
                break
        data = None
        if b64 and msgpack:
            try: data = msgpack.unpackb(base64.b64decode(b64), raw=False)
            except Exception as e: data = {"_b64_decode_err": str(e)}
        if cmd or data:
            return {"cmd": cmd, "data": data}
    except Exception:
        pass
    # fallback: whole body is base64(msgpack)?
    if msgpack:
        try: return {"cmd": None, "data": msgpack.unpackb(base64.b64decode(buf), raw=False)}
        except Exception: pass
        try: return {"cmd": None, "data": msgpack.unpackb(buf, raw=False)}
        except Exception: pass
    return None

def _dump(flow, which, content):
    if not content:
        return
    url = flow.request.pretty_url
    cmd = url.rsplit("/", 1)[-1].split("?")[0]  # gate_CMD_xxx
    _seq[0] += 1
    base = os.path.join(OUT, f"{_seq[0]:03d}_{which}_{cmd}")
    with open(base + ".bin", "wb") as f:
        f.write(content)
    dec = decode_envelope(content)
    if dec is not None:
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(dec, f, indent=2, ensure_ascii=False, default=str)
    d = (dec or {}).get("data")
    if isinstance(d, dict) and ("hash" in d or "client_version" in d or "user_eula_info" in d):
        print(f"\n===== {which.upper()} {cmd} (DECODED) -> {os.path.basename(base)}.json =====", flush=True)
        for k in ("hash", "client_version", "user_eula_season", "lang", "region", "platform"):
            if k in d: print(f"    {k:18} = {d.get(k)}", flush=True)
        ei = d.get("user_eula_info")
        if isinstance(ei, dict):
            print(f"    eula_version       = {json.dumps(ei.get('eula_version'), ensure_ascii=False)}", flush=True)
        print("", flush=True)

def request(flow):
    if "konami" in flow.request.host:
        print(f"[req] {flow.request.method} {flow.request.pretty_url}", flush=True)
        _dump(flow, "req", flow.request.raw_content)

def response(flow):
    if "konami" in flow.request.host:
        _dump(flow, "res", flow.response.raw_content)
