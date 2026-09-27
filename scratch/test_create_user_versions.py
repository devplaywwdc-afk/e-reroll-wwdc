import ssl
import os
import socket
import struct
import base64
import json
import time
import msgpack
import h2.connection
import h2.events

def encode_varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value & 0x7f)
    return bytes(out)

def build_grpc_payload(cmd_name: str, seq: int, msgpack_dict: dict, field4: str = "/"):
    packed_mp = msgpack.packb(msgpack_dict)
    b64_str = base64.b64encode(packed_mp)
    pb = bytearray()
    cmd_bytes = cmd_name.encode('utf-8')
    pb.append(0x0a)
    pb.extend(encode_varint(len(cmd_bytes)))
    pb.extend(cmd_bytes)
    pb.append(0x10)
    pb.extend(encode_varint(seq))
    pb.append(0x1a)
    pb.extend(encode_varint(len(b64_str)))
    pb.extend(b64_str)
    if field4:
        f4_bytes = field4.encode('utf-8')
        pb.append(0x22)
        pb.extend(encode_varint(len(f4_bytes)))
        pb.extend(f4_bytes)
    return b"\x00" + struct.pack(">I", len(pb)) + bytes(pb)

def decode_grpc_response(data: bytes):
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
            if fn == 1: cmd_name = sub.decode('utf-8', 'replace')
            elif fn == 3: b64_data = sub
    result = {"cmd": cmd_name, "seq": seq}
    if b64_data:
        try:
            mp_bytes = base64.b64decode(b64_data)
            result["data"] = msgpack.unpackb(mp_bytes, raw=False)
        except Exception as e:
            result["error"] = str(e)
    return result

HOST = "pes22-game.cs.konami.net"
PORT = 443

def test_eula_versions(versions_dict, season=""):
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(["h2"])
    sock = socket.create_connection((HOST, PORT), timeout=10)
    tls_sock = ctx.wrap_socket(sock, server_hostname=HOST)
    conn = h2.connection.H2Connection()
    conn.initiate_connection()
    tls_sock.sendall(conn.data_to_send())
    data = tls_sock.recv(65535)
    conn.receive_data(data)
    tls_sock.sendall(conn.data_to_send())

    headers = [
        (':scheme', 'https'),
        (':method', 'POST'),
        (':authority', f'{HOST}:{PORT}'),
        (':path', '/command_service.CommandService/CommandStream'),
        ('te', 'trailers'),
        ('content-type', 'application/grpc'),
        ('user-agent', 'grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)'),
        ('grpc-accept-encoding', 'identity,deflate,gzip'),
        ('accept-encoding', 'identity,gzip'),
    ]
    stream_id = conn.get_next_available_stream_id()
    conn.send_headers(stream_id, headers, end_stream=False)
    tls_sock.sendall(conn.data_to_send())

    create_user_dict = {
        "msgid": "CMD_CREATE_USER",
        "rqid": 1,
        "user_id": 0,
        "session_id": "",
        "my_platform": "ANDROID",
        "s_keyword": "",
        "hash": "e8bbc34f90e46bbbbb6ef7a7a74df3ba",
        "client_version": "6.0.1",
        "platform": "ANDROID",
        "device": {"device_identifier": "3e04704dd8c1e044"},
        "lang": "en",
        "region": "asia",
        "pcspec": "",
        "user_eula_season": season,
        "user_eula_info": {
            "eula_ip_country_a3": "THA",
            "eula_select_country_a3": "THA",
            "eula_ip_state": "",
            "eula_select_state": "",
            "eula_version": versions_dict,
            "eula_country_age_kind": "ADULT",
            "eula_privacy_action": "AGREE"
        },
        "pes_user_info": {
            "user_id": 0,
            "user_name": "dj_reroll",
            "is_deleted": "NO",
            "platform": "ANDROID",
            "psn_user_id": {"account_id": "", "online_id": "", "is_valid_cache": "NO"},
            "xuid": "",
            "steamid": "",
            "nsw_user_id": {"nsa_id": "", "issuer_id": 0},
            "avatar": {
                "is_already_set": "NO", "player_id": 0, "special_id": 0,
                "background_id": 0, "special_id_background": 0, "is_enable_player": "NO",
                "is_enable_background": "NO", "material_key_player": "", "material_key_background": ""
            },
            "fameIdList": [],
            "user_banner": {"banner_id": {"unique_id": 0}, "is_valid": "NO"}
        },
        "cross_platform_option": "ON",
        "age": "25",
        "os_version": "Android OS 12 / API-32",
        "model_name": "SM-F741N",
        "gpu_name": "Adreno (TM) 640",
        "soc_name": "Qualcomm Snapdragon",
        "survey_results": [],
        "user_age_kind": "ADULT",
        "is_rooting": "NO",
        "phy_mem_used_mib": 1024,
        "phy_mem_available_mib": 3072,
        "app_storage_used_mib": 512,
        "app_storage_available_mib": 10240,
        "data_storage_used_mib": 512,
        "data_storage_available_mib": 10240,
        "payment_store_link_send_info": {"country_code": "THA"},
        "kid_challenge_id": ""
    }

    frame = build_grpc_payload("CMD_CREATE_USER", 1, create_user_dict)
    conn.send_data(stream_id, frame, end_stream=False)
    tls_sock.sendall(conn.data_to_send())

    resp_body = bytearray()
    st = time.time()
    res_obj = None
    while time.time() - st < 5:
        try:
            d = tls_sock.recv(65535)
            if not d: break
            events = conn.receive_data(d)
            for ev in events:
                if isinstance(ev, h2.events.DataReceived):
                    resp_body.extend(ev.data)
                    conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    if len(resp_body) >= 5:
                        mlen = struct.unpack(">I", resp_body[1:5])[0]
                        if len(resp_body) >= 5 + mlen:
                            res_obj = decode_grpc_response(bytes(resp_body[:5+mlen]))
                            break
            if res_obj: break
        except Exception:
            break
    tls_sock.close()
    return res_obj

v_0 = {"eula": "0.00", "eula_privacy_notice": "0.00", "eula_myclub": "0.00", "eula_japan_version": "0.00", "eula_myclub_japan": "0.00"}
print("Testing version 0.00 with season '':")
res = test_eula_versions(v_0, season="")
print(json.dumps(res, indent=2))

v_empty = {"eula": "", "eula_privacy_notice": "", "eula_myclub": "", "eula_japan_version": "", "eula_myclub_japan": ""}
print("\nTesting version empty with season '':")
res = test_eula_versions(v_empty, season="")
print(json.dumps(res, indent=2))
