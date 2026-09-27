#!/usr/bin/env python3
"""
Focused CMD_CREATE_USER test:
1. Query CMD_GET_EULA_AREA_LIST for current EULA versions
2. Try CMD_CREATE_USER with exact field layout from IDA
"""
import ssl, socket, struct, base64, json, time, uuid, sys
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

def build_grpc_payload(cmd_name, seq, msgpack_dict, field4="/"):
    packed_mp = msgpack.packb(msgpack_dict)
    b64_str = base64.b64encode(packed_mp)
    pb = bytearray()
    cmd_bytes = cmd_name.encode('utf-8')
    pb.append(0x0a); pb.extend(encode_varint(len(cmd_bytes))); pb.extend(cmd_bytes)
    pb.append(0x10); pb.extend(encode_varint(seq))
    pb.append(0x1a); pb.extend(encode_varint(len(b64_str))); pb.extend(b64_str)
    if field4:
        f4 = field4.encode('utf-8')
        pb.append(0x22); pb.extend(encode_varint(len(f4))); pb.extend(f4)
    return b"\x00" + struct.pack(">I", len(pb)) + bytes(pb)

def decode_grpc_response(data):
    if len(data) < 5: return None
    msg_len = struct.unpack(">I", data[1:5])[0]
    payload = data[5:5+msg_len]
    idx = 0; cmd_name = ""; seq = 0; b64_data = b""
    while idx < len(payload):
        tag = payload[idx]; idx += 1
        fn = tag >> 3; wt = tag & 0x07
        if wt == 0:
            val = 0; s = 0
            while True:
                b = payload[idx]; idx += 1
                val |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
            if fn == 2: seq = val
        elif wt == 2:
            l = 0; s = 0
            while True:
                b = payload[idx]; idx += 1
                l |= (b & 0x7f) << s
                if not (b & 0x80): break
                s += 7
            sub = payload[idx:idx+l]; idx += l
            if fn == 1: cmd_name = sub.decode('utf-8','replace')
            elif fn == 3: b64_data = sub
    result = {"cmd": cmd_name, "seq": seq}
    if b64_data:
        try:
            mp_bytes = base64.b64decode(b64_data)
            result["data"] = msgpack.unpackb(mp_bytes, raw=False)
        except Exception as e:
            result["error"] = str(e)
    return result

class GRPCSession:
    def __init__(self):
        ctx = ssl.create_default_context()
        ctx.set_alpn_protocols(["h2"])
        sock = socket.create_connection((HOST, PORT), timeout=15)
        self.tls = ctx.wrap_socket(sock, server_hostname=HOST)
        self.conn = h2.connection.H2Connection()
        self.conn.initiate_connection()
        self.tls.sendall(self.conn.data_to_send())
        self.conn.receive_data(self.tls.recv(65535))
        self.tls.sendall(self.conn.data_to_send())
        self.seq = 0
        self.stream_id = self.conn.get_next_available_stream_id()
        headers = [
            (':scheme', 'https'), (':method', 'POST'),
            (':authority', f'{HOST}:{PORT}'),
            (':path', '/command_service.CommandService/CommandStream'),
            ('te', 'trailers'), ('content-type', 'application/grpc'),
            ('user-agent', 'grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)'),
            ('grpc-accept-encoding', 'identity,deflate,gzip'),
            ('accept-encoding', 'identity,gzip'),
        ]
        self.conn.send_headers(self.stream_id, headers, end_stream=False)
        self.tls.sendall(self.conn.data_to_send())

    def send_cmd(self, cmd_name, payload, timeout=10):
        self.seq += 1
        frame = build_grpc_payload(cmd_name, self.seq, payload)
        self.conn.send_data(self.stream_id, frame, end_stream=False)
        self.tls.sendall(self.conn.data_to_send())
        
        resp_body = bytearray()
        st = time.time()
        while time.time() - st < timeout:
            try:
                d = self.tls.recv(65535)
                if not d: break
                for ev in self.conn.receive_data(d):
                    if isinstance(ev, h2.events.DataReceived):
                        resp_body.extend(ev.data)
                        self.conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                        if len(resp_body) >= 5:
                            mlen = struct.unpack(">I", resp_body[1:5])[0]
                            if len(resp_body) >= 5 + mlen:
                                return decode_grpc_response(bytes(resp_body[:5+mlen]))
                self.tls.sendall(self.conn.data_to_send())
            except socket.timeout:
                break
        return None
    
    def close(self):
        try: self.tls.close()
        except: pass

def step1_get_eula_versions():
    """Get EULA area list with correct lang code."""
    print("=" * 60)
    print("  STEP 1: GET_EULA_AREA_LIST (TH)")
    print("=" * 60)
    
    sess = GRPCSession()
    payload = {
        "msgid": "CMD_GET_EULA_AREA_LIST",
        "rqid": 1,
        "user_id": 0,
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "nation_code": "THA",
        "lang": "TH"
    }
    r = sess.send_cmd("CMD_GET_EULA_AREA_LIST", payload)
    if r:
        data = r.get("data", {})
        print(f"  result: {data.get('result')}")
        if data.get("result") == "NOERR":
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:3000])
            return data
        else:
            print(f"  error: {data.get('error_msg')}")
            # Try other lang codes
            for lang in ["EN", "JA", "TH", "en", "ja", "th"]:
                sess2 = GRPCSession()
                payload["lang"] = lang
                r2 = sess2.send_cmd("CMD_GET_EULA_AREA_LIST", payload)
                if r2:
                    d2 = r2.get("data", {})
                    print(f"  lang={lang}: {d2.get('result')} {d2.get('error_msg','')}")
                    if d2.get("result") == "NOERR":
                        print(json.dumps(d2, indent=2, ensure_ascii=False, default=str)[:3000])
                        sess2.close()
                        return d2
                sess2.close()
    sess.close()
    return None

def step2_get_country_list():
    """Get country list."""
    print("\n" + "=" * 60)
    print("  STEP 2: GET_COUNTRY_LIST")
    print("=" * 60)
    
    sess = GRPCSession()
    payload = {
        "msgid": "CMD_GET_COUNTRY_LIST",
        "rqid": 1,
        "user_id": 0,
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "lang": "TH"
    }
    r = sess.send_cmd("CMD_GET_COUNTRY_LIST", payload)
    if r:
        data = r.get("data", {})
        print(f"  result: {data.get('result')}")
        if data.get("result") == "NOERR":
            # Find THA entry for eula info
            countries = data.get("country_list", [])
            for c in countries:
                if c.get("country_code_a3") == "THA":
                    print(f"  THA entry: {json.dumps(c, indent=2, ensure_ascii=False, default=str)}")
                    sess.close()
                    return c
            # Print first few
            print(f"  total countries: {len(countries)}")
            for c in countries[:3]:
                print(f"    {c.get('country_code_a3')}: age_kind={c.get('country_age_kind')} eula_country={c.get('is_eula_country')}")
    sess.close()
    return None

def step3_create_user(eula_data=None, country_data=None):
    """Create user with fresh device ID."""
    print("\n" + "=" * 60)
    print("  STEP 3: CMD_CREATE_USER")
    print("=" * 60)
    
    device_id = uuid.uuid4().hex[:16]
    print(f"  device_id: {device_id}")
    
    # Use eula versions from step1 if available
    eula_ver = {
        "eula": "8.00",
        "eula_privacy_notice": "4.00",
        "eula_myclub": "6.00",
        "eula_japan_version": "8.00",
        "eula_myclub_japan": "7.00"
    }
    if eula_data:
        ev = eula_data.get("eula_version", {})
        if ev:
            eula_ver = ev
            print(f"  Using live EULA versions: {eula_ver}")
    
    age_kind = "BIRTH_YEAR_MONTH"
    if country_data:
        ak = country_data.get("country_age_kind", "")
        if ak:
            age_kind = ak
            print(f"  Using age_kind from country: {age_kind}")
    
    payload = {
        "msgid": "CMD_CREATE_USER",
        "rqid": 1,
        "user_id": 0,
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba",
        "client_version": "6.0.1",
        "platform": "ANDROID",
        "device": {
            "device_identifier": device_id
        },
        "lang": "TH",
        "region": "asia",
        "pcspec": "",
        "user_eula_season": "2025",
        "user_eula_info": {
            "eula_ip_country_a3": "THA",
            "eula_select_country_a3": "THA",
            "eula_ip_state": "",
            "eula_select_state": "",
            "eula_version": eula_ver,
            "eula_country_age_kind": age_kind,
            "eula_privacy_action": "CONFIRM"
        },
        "pes_user_info": {
            "user_id": 0,
            "user_name": f"DjTest{int(time.time())%10000:04d}",
            "is_deleted": "NO",
            "platform": "ANDROID",
            "psn_user_id": {"account_id": "", "online_id": "", "is_valid_cache": "NO"},
            "xuid": "",
            "steamid": "",
            "nsw_user_id": {"nsa_id": "", "issuer_id": 0},
            "avatar": {
                "is_already_set": "NO",
                "player_id": 0,
                "special_id": 0,
                "background_id": 0,
                "special_id_background": 0,
                "is_enable_player": "NO",
                "is_enable_background": "NO",
                "material_key_player": "",
                "material_key_background": ""
            },
            "fameIdList": [],
            "user_banner": {"banner_id": {"unique_id": 0}, "is_valid": "NO"}
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
    
    print(f"  user_name: {payload['pes_user_info']['user_name']}")
    print("  Sending CMD_CREATE_USER...")
    
    sess = GRPCSession()
    r = sess.send_cmd("CMD_CREATE_USER", payload, timeout=15)
    if r:
        data = r.get("data", {})
        result = data.get("result", "")
        print(f"  result: {result}")
        print(f"  error_msg: {data.get('error_msg', '')}")
        
        if result == "NOERR":
            print("\n  *** SUCCESS! Account created! ***")
            print(f"  user_id: {data.get('user_id')}")
            print(f"  auth_code: {data.get('auth_code')}")
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:3000])
            with open("scratch/fresh_create_success.json", "w", encoding="utf-8") as f:
                json.dump(r, f, indent=2, ensure_ascii=False, default=str)
        else:
            print(f"  Full response: {json.dumps(data, indent=2, ensure_ascii=False, default=str)[:1000]}")
            with open("scratch/fresh_create_fail.json", "w", encoding="utf-8") as f:
                json.dump(r, f, indent=2, ensure_ascii=False, default=str)
    else:
        print("  No response received!")
    
    sess.close()
    return r

def step4_try_login_existing():
    """Try login with existing auth to verify connectivity."""
    print("\n" + "=" * 60)
    print("  STEP 4: CMD_LOGIN with existing account")
    print("=" * 60)
    
    try:
        auth = json.load(open("auth_new.dat", "r", encoding="utf-8"))
        print(f"  user_id: {auth['user_id']}, code: {auth.get('user_code','?')}")
    except:
        print("  No auth_new.dat found, skipping")
        return None
    
    sess = GRPCSession()
    payload = {
        "msgid": "CMD_LOGIN",
        "rqid": 1,
        "user_id": auth["user_id"],
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "auth_code": auth["auth_code"],
        "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba",
        "client_version": "6.0.1",
        "platform": "ANDROID",
        "device": {"device_identifier": "3e04704dd8c1e044"},
        "lang": "TH",
        "country_code": ["THA"],
        "region": "asia",
        "pcspec": "",
        "cross_platform_option": "ON",
        "is_disable_sharing": "NO",
        "os_version": "Android OS 12 / API-32",
        "model_name": "SM-F741N",
        "gpu_name": "Adreno (TM) 640",
        "soc_name": "Qualcomm Snapdragon 888",
        "is_rooting": "NO",
        "phy_mem_used_mib": 1024,
        "phy_mem_available_mib": 2048,
        "app_storage_used_mib": 500,
        "app_storage_available_mib": 10000,
        "data_storage_used_mib": 2000,
        "data_storage_available_mib": 20000,
        "payment_store_link_send_info": {"country_code": "THA"}
    }
    
    r = sess.send_cmd("CMD_LOGIN", payload, timeout=15)
    if r:
        data = r.get("data", {})
        print(f"  result: {data.get('result')}")
        print(f"  error_msg: {data.get('error_msg', '')}")
        if data.get("result") == "NOERR":
            print(f"  session_id: {data.get('session_id')}")
            print(f"  s_keyword: {repr(data.get('s_keyword',''))}")
    else:
        print("  No response!")
    sess.close()
    return r

if __name__ == "__main__":
    # Step 1: Get EULA versions
    eula_data = step1_get_eula_versions()
    
    # Step 2: Get country info for THA
    country_data = step2_get_country_list()
    
    # Step 3: Try to create user
    step3_create_user(eula_data, country_data)
    
    # Step 4: Try login with existing account
    step4_try_login_existing()
    
    print("\nDone.")
