#!/usr/bin/env python3
"""
Deep test of CMD_CREATE_USER — try different hash values, different device models,
and different field combinations to find what exactly triggers "db error tb_user_base:"
"""
import ssl, socket, struct, base64, json, time, uuid, sys, hashlib
import msgpack
import h2.connection
import h2.events

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except: pass

HOST = "pes22-game.cs.konami.net"
PORT = 443

def encode_varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value & 0x7f)
    return bytes(out)

def build_grpc_payload(cmd_name, seq, d, field4="/"):
    packed = msgpack.packb(d)
    b64 = base64.b64encode(packed)
    pb = bytearray()
    c = cmd_name.encode()
    pb += b"\x0a" + encode_varint(len(c)) + c
    pb += b"\x10" + encode_varint(seq)
    pb += b"\x1a" + encode_varint(len(b64)) + b64
    if field4:
        f = field4.encode()
        pb += b"\x22" + encode_varint(len(f)) + f
    return b"\x00" + struct.pack(">I", len(pb)) + bytes(pb)

def decode_resp(data):
    if len(data) < 5: return None
    ml = struct.unpack(">I", data[1:5])[0]
    p = data[5:5+ml]
    i = 0; cmd = ""; b64d = b""
    while i < len(p):
        t = p[i]; i += 1; fn = t >> 3; wt = t & 7
        if wt == 0:
            v = 0; s = 0
            while True:
                b = p[i]; i += 1; v |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
        elif wt == 2:
            l = 0; s = 0
            while True:
                b = p[i]; i += 1; l |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
            sub = p[i:i+l]; i += l
            if fn == 1: cmd = sub.decode('utf-8','replace')
            elif fn == 3: b64d = sub
    r = {"cmd": cmd}
    if b64d:
        try: r["data"] = msgpack.unpackb(base64.b64decode(b64d), raw=False)
        except: pass
    return r

def send_once(payload, timeout=10):
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2"])
    sock = socket.create_connection((HOST, PORT), timeout=15)
    tls = ctx.wrap_socket(sock, server_hostname=HOST)
    conn = h2.connection.H2Connection()
    conn.initiate_connection()
    tls.sendall(conn.data_to_send())
    conn.receive_data(tls.recv(65535))
    tls.sendall(conn.data_to_send())
    
    sid = conn.get_next_available_stream_id()
    conn.send_headers(sid, [
        (':scheme','https'),(':method','POST'),
        (':authority',f'{HOST}:{PORT}'),
        (':path','/command_service.CommandService/CommandStream'),
        ('te','trailers'),('content-type','application/grpc'),
        ('user-agent','grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)'),
        ('grpc-accept-encoding','identity,deflate,gzip'),
        ('accept-encoding','identity,gzip'),
    ], end_stream=False)
    tls.sendall(conn.data_to_send())
    
    frame = build_grpc_payload("CMD_CREATE_USER", 1, payload)
    conn.send_data(sid, frame, end_stream=False)
    tls.sendall(conn.data_to_send())
    
    resp = bytearray()
    st = time.time()
    while time.time() - st < timeout:
        try:
            d = tls.recv(65535)
            if not d: break
            for ev in conn.receive_data(d):
                if isinstance(ev, h2.events.DataReceived):
                    resp.extend(ev.data)
                    conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    if len(resp) >= 5:
                        ml = struct.unpack(">I", resp[1:5])[0]
                        if len(resp) >= 5 + ml:
                            tls.close()
                            return decode_resp(bytes(resp[:5+ml]))
            tls.sendall(conn.data_to_send())
        except: break
    tls.close()
    return None

def make_payload(overrides=None):
    device_id = uuid.uuid4().hex[:16]
    p = {
        "msgid": "CMD_CREATE_USER",
        "rqid": 1,
        "user_id": 0,
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba",
        "client_version": "6.0.1",
        "platform": "ANDROID",
        "device": {"device_identifier": device_id},
        "lang": "TH",
        "region": "asia",
        "pcspec": "",
        "user_eula_season": "2025",
        "user_eula_info": {
            "eula_ip_country_a3": "THA",
            "eula_select_country_a3": "THA",
            "eula_ip_state": "",
            "eula_select_state": "",
            "eula_version": {
                "eula": "8.00", "eula_privacy_notice": "4.00",
                "eula_myclub": "6.00", "eula_japan_version": "8.00",
                "eula_myclub_japan": "7.00"
            },
            "eula_country_age_kind": "BIRTH_YEAR_MONTH",
            "eula_privacy_action": "CONFIRM"
        },
        "pes_user_info": {
            "user_id": 0, "user_name": "DjTest",
            "is_deleted": "NO", "platform": "ANDROID",
            "psn_user_id": {"account_id":"","online_id":"","is_valid_cache":"NO"},
            "xuid": "", "steamid": "",
            "nsw_user_id": {"nsa_id":"","issuer_id":0},
            "avatar": {
                "is_already_set":"NO","player_id":0,"special_id":0,
                "background_id":0,"special_id_background":0,
                "is_enable_player":"NO","is_enable_background":"NO",
                "material_key_player":"","material_key_background":""
            },
            "fameIdList": [],
            "user_banner": {"banner_id":{"unique_id":0},"is_valid":"NO"}
        },
        "cross_platform_option": "ON",
        "age": "1998-05",
        "os_version": "Android OS 12 / API-32",
        "model_name": "SM-F741N",
        "gpu_name": "Adreno (TM) 640",
        "soc_name": "Qualcomm Snapdragon 888",
        "survey_results": [],
        "user_age_kind": "ADULT",
        "is_rooting": "NO",
        "phy_mem_used_mib": 1024,
        "phy_mem_available_mib": 2048,
        "app_storage_used_mib": 500,
        "app_storage_available_mib": 10000,
        "data_storage_used_mib": 500,
        "data_storage_available_mib": 10000,
        "payment_store_link_send_info": {"country_code": "THA"},
        "kid_challenge_id": ""
    }
    if overrides:
        for k, v in overrides.items():
            if "." in k:
                parts = k.split(".")
                obj = p
                for part in parts[:-1]:
                    obj = obj[part]
                obj[parts[-1]] = v
            else:
                p[k] = v
    return p

# Test battery
tests = [
    # Test hash variations
    ("hash=empty", {"hash": ""}),
    ("hash=zeros", {"hash": "00000000000000000000000000000000"}),
    ("hash=md5(6.0.1)", {"hash": hashlib.md5(b"6.0.1").hexdigest()}),
    ("hash=md5(PES2022)", {"hash": hashlib.md5(b"PES2022").hexdigest()}),
    ("hash=md5(ANDROID)", {"hash": hashlib.md5(b"ANDROID").hexdigest()}),
    ("hash=md5(CMD_CREATE_USER)", {"hash": hashlib.md5(b"CMD_CREATE_USER").hexdigest()}),
    ("hash=md5(pesam)", {"hash": hashlib.md5(b"jp.konami.pesam").hexdigest()}),
    ("hash=md5(client_version+6.0.1)", {"hash": hashlib.md5(b"client_version6.0.1").hexdigest()}),
    
    # Test client version
    ("version=6.0.0", {"client_version": "6.0.0", "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba"}),
    ("version=5.7.1", {"client_version": "5.7.1", "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba"}),
    ("version=6.1.0", {"client_version": "6.1.0", "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba"}),
    
    # Test region
    ("region=ASIA (upper)", {"region": "ASIA"}),
    ("region=jp", {"region": "jp"}),
    
    # Test lang
    ("lang=th (lower)", {"lang": "th"}),
    ("lang=EN", {"lang": "EN"}),
    ("lang=JA", {"lang": "JA"}),
    
    # Test different device models
    ("model=Pixel6", {"model_name": "Pixel 6", "gpu_name": "Mali-G78", "soc_name": "Google Tensor"}),
    
    # Test age format
    ("age=25", {"age": "25"}),
    ("age=1998-05-15", {"age": "1998-05-15"}),
    
    # Test eula_country_age_kind
    ("age_kind=ADULT", {"user_eula_info.eula_country_age_kind": "ADULT"}),
    ("age_kind=NONE", {"user_eula_info.eula_country_age_kind": "NONE"}),
    
    # Test eula_privacy_action
    ("privacy=AGREE", {"user_eula_info.eula_privacy_action": "AGREE"}),
    ("privacy=NONE", {"user_eula_info.eula_privacy_action": "NONE"}),
    
    # Test with no hash field at all
    ("no_hash", None),  # special case
]

print("="*70)
print("  Deep CMD_CREATE_USER Field Testing")
print("="*70)

for name, overrides in tests:
    if name == "no_hash":
        p = make_payload()
        del p["hash"]
    else:
        p = make_payload(overrides)
    
    r = send_once(p)
    data = r.get("data", {}) if r else {}
    result = data.get("result", "NO_RESP")
    err = data.get("error_msg", "")
    
    status = "OK" if result == "NOERR" else result
    print(f"  [{name:40s}] {status}: {err}")
    
    if result == "NOERR":
        print(f"\n  *** SUCCESS with {name}! ***")
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000])
        with open(f"scratch/success_{name}.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        break
    
    time.sleep(0.3)

print("\nDone.")
