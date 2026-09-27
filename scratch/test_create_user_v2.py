#!/usr/bin/env python3
"""
Step 1: Login with existing user to get session_id + s_keyword
Step 2: Query CMD_GET_USER_EULA_INFO to get the current eula versions
Step 3: Attempt CMD_CREATE_USER with correct params
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
        self.tls.close()

def test_create_user_directly():
    """Try CMD_CREATE_USER with updated eula params — no prior login needed."""
    print("="*60)
    print("  TEST: Direct CMD_CREATE_USER")
    print("="*60)
    
    sess = GRPCSession()
    
    device_id = uuid.uuid4().hex[:16]
    print(f"  device_identifier: {device_id}")
    
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
            "eula_version": {
                "eula": "8.00",
                "eula_privacy_notice": "4.00",
                "eula_myclub": "6.00",
                "eula_japan_version": "8.00",
                "eula_myclub_japan": "7.00"
            },
            "eula_country_age_kind": "BIRTH_YEAR_MONTH",
            "eula_privacy_action": "CONFIRM"
        },
        "pes_user_info": {
            "user_id": 0,
            "user_name": "DjReroll01",
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
    
    print("  Sending CMD_CREATE_USER...")
    r = sess.send_cmd("CMD_CREATE_USER", payload)
    if r:
        print("  RESPONSE:")
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str)[:3000])
        with open("scratch/direct_create_user_resp.json", "w", encoding="utf-8") as f:
            json.dump(r, f, indent=2, ensure_ascii=False, default=str)
        
        data = r.get("data", {})
        if data.get("result") == "NOERR":
            print("\n  *** SUCCESS! Account created! ***")
            print(f"  user_id: {data.get('user_id')}")
            print(f"  auth_code: {data.get('auth_code')}")
            return data
        else:
            print(f"\n  FAILED: {data.get('result')} - {data.get('error_msg')}")
    else:
        print("  No response!")
    
    sess.close()
    return None

def test_eula_versions():
    """Try different eula_season values."""
    print("\n" + "="*60)
    print("  TEST: Eula Season Variants")
    print("="*60)
    
    # Current game version is 6.0.1, season likely 2025
    # But let's try different combinations
    eula_seasons = ["2025", "2024", "2026"]
    eula_combos = [
        # (eula, privacy_notice, myclub, japan, myclub_japan)
        ("8.00", "4.00", "6.00", "8.00", "7.00"),  # v6.0.1 expected
        ("9.00", "5.00", "7.00", "9.00", "8.00"),  # maybe newer
        ("7.00", "3.00", "5.00", "7.00", "6.00"),  # maybe older
        ("1.00", "1.00", "1.00", "1.00", "1.00"),  # minimum
        ("0.00", "0.00", "0.00", "0.00", "0.00"),  # zero (fresh)
    ]
    
    for season in eula_seasons:
        for combo in eula_combos:
            sess = GRPCSession()
            device_id = uuid.uuid4().hex[:16]
            
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
                "device": {"device_identifier": device_id},
                "lang": "TH",
                "region": "asia",
                "pcspec": "",
                "user_eula_season": season,
                "user_eula_info": {
                    "eula_ip_country_a3": "THA",
                    "eula_select_country_a3": "THA",
                    "eula_ip_state": "",
                    "eula_select_state": "",
                    "eula_version": {
                        "eula": combo[0],
                        "eula_privacy_notice": combo[1],
                        "eula_myclub": combo[2],
                        "eula_japan_version": combo[3],
                        "eula_myclub_japan": combo[4]
                    },
                    "eula_country_age_kind": "BIRTH_YEAR_MONTH",
                    "eula_privacy_action": "CONFIRM"
                },
                "pes_user_info": {
                    "user_id": 0,
                    "user_name": "DjTest",
                    "is_deleted": "NO",
                    "platform": "ANDROID",
                    "psn_user_id": {"account_id": "", "online_id": "", "is_valid_cache": "NO"},
                    "xuid": "", "steamid": "",
                    "nsw_user_id": {"nsa_id": "", "issuer_id": 0},
                    "avatar": {
                        "is_already_set": "NO", "player_id": 0, "special_id": 0,
                        "background_id": 0, "special_id_background": 0,
                        "is_enable_player": "NO", "is_enable_background": "NO",
                        "material_key_player": "", "material_key_background": ""
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
            
            r = sess.send_cmd("CMD_CREATE_USER", payload)
            data = r.get("data", {}) if r else {}
            result = data.get("result", "NO_RESP")
            err = data.get("error_msg", "")
            
            tag = f"season={season} eula={combo[0]}/{combo[1]}/{combo[2]}/{combo[3]}/{combo[4]}"
            print(f"  [{tag}] -> {result}: {err}")
            
            if result == "NOERR":
                print(f"\n  *** SUCCESS with {tag}! ***")
                print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:2000])
                sess.close()
                return data
            
            sess.close()
            time.sleep(0.5)
    
    return None

# First try the GET_USER_EULA_INFO approach using existing user
def get_eula_info_from_login():
    """Login with existing user, then query eula info."""
    print("="*60)
    print("  STEP 1: Login with existing user")
    print("="*60)
    
    auth = json.load(open("auth_new.dat", "r", encoding="utf-8"))
    print(f"  user_id: {auth['user_id']}")
    print(f"  auth_code: {auth['auth_code'][:20]}...")
    
    sess = GRPCSession()
    
    login_payload = {
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
    
    print("  Sending CMD_LOGIN...")
    r = sess.send_cmd("CMD_LOGIN", login_payload)
    if r:
        data = r.get("data", {})
        result = data.get("result", "")
        print(f"  Login result: {result}")
        
        if result == "NOERR":
            session_id = data.get("session_id", "")
            s_keyword = data.get("s_keyword", "")
            print(f"  session_id: {session_id}")
            print(f"  s_keyword: {repr(s_keyword)}")
            
            # Now query eula info
            print("\n  Querying CMD_GET_USER_EULA_INFO...")
            eula_payload = {
                "msgid": "CMD_GET_USER_EULA_INFO",
                "rqid": 2,
                "user_id": auth["user_id"],
                "session_id": session_id,
                "my_platform": "ANDROID",
                "s_keyword": s_keyword,
            }
            r2 = sess.send_cmd("CMD_GET_USER_EULA_INFO", eula_payload)
            if r2:
                print("  EULA Info Response:")
                print(json.dumps(r2, indent=2, ensure_ascii=False, default=str)[:3000])
                with open("scratch/live_eula_info.json", "w", encoding="utf-8") as f:
                    json.dump(r2, f, indent=2, ensure_ascii=False, default=str)
            
            return {"session_id": session_id, "s_keyword": s_keyword, "login_data": data}
        else:
            print(f"  Login failed: {data.get('error_msg','')}")
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str)[:1000])
    else:
        print("  No response!")
    
    sess.close()
    return None

if __name__ == "__main__":
    # First try direct create_user
    result = test_create_user_directly()
    if result:
        print("\nDone! Account created.")
        sys.exit(0)
    
    # Try eula version variants
    result = test_eula_versions()
    if result:
        print("\nDone! Account created.")
        sys.exit(0)
    
    # Try login + eula info approach
    print("\nDirect approach failed. Trying login + eula query...")
    get_eula_info_from_login()
