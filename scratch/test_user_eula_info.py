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
        
    grpc_frame = b"\x00" + struct.pack(">I", len(pb)) + bytes(pb)
    return grpc_frame

def decode_grpc_response(data: bytes):
    if len(data) < 5:
        return None
    compressed = data[0]
    msg_len = struct.unpack(">I", data[1:5])[0]
    payload = data[5:5+msg_len]
    
    idx = 0
    cmd_name = ""
    seq = 0
    b64_data = b""
    while idx < len(payload):
        tag = payload[idx]; idx += 1
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val = 0; shift = 0
            while True:
                b = payload[idx]; idx += 1
                val |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            if field_num == 2: seq = val
        elif wire_type == 2:
            l = 0; shift = 0
            while True:
                b = payload[idx]; idx += 1
                l |= (b & 0x7f) << shift
                if not (b & 0x80): break
                shift += 7
            sub = payload[idx:idx+l]; idx += l
            if field_num == 1: cmd_name = sub.decode('utf-8', 'replace')
            elif field_num == 3: b64_data = sub
                
    result = {"cmd": cmd_name, "seq": seq}
    if b64_data:
        try:
            mp_bytes = base64.b64decode(b64_data)
            result["data"] = msgpack.unpackb(mp_bytes, raw=False)
        except Exception as e:
            result["b64_raw"] = b64_data.decode('utf-8', 'replace')
            result["error"] = str(e)
    return result

HOST = "pes22-game.cs.konami.net"
PORT = 443

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

req_dict = {
    "msgid": "CMD_GET_USER_EULA_INFO",
    "rqid": 1,
    "user_id": 0,
    "session_id": "",
    "my_platform": "ANDROID",
    "s_keyword": "",
    "steam_id": "",
    "xuid": "",
    "account_id": ""
}

grpc_frame = build_grpc_payload("CMD_GET_USER_EULA_INFO", 1, req_dict)
conn.send_data(stream_id, grpc_frame, end_stream=False)
tls_sock.sendall(conn.data_to_send())

print("Sent CMD_GET_USER_EULA_INFO! Waiting for response...")
resp_body = bytearray()
start_t = time.time()
while time.time() - start_t < 10:
    try:
        data = tls_sock.recv(65535)
    except socket.timeout:
        print("Socket timeout")
        break
    if not data:
        print("Connection closed")
        break
    events = conn.receive_data(data)
    for ev in events:
        if isinstance(ev, h2.events.ResponseReceived):
            print("Response Headers:", ev.headers)
        elif isinstance(ev, h2.events.DataReceived):
            resp_body.extend(ev.data)
            conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
            if len(resp_body) >= 5:
                msg_len = struct.unpack(">I", resp_body[1:5])[0]
                if len(resp_body) >= 5 + msg_len:
                    parsed = decode_grpc_response(bytes(resp_body[:5+msg_len]))
                    print("SERVER RESPONSE:")
                    print(json.dumps(parsed, indent=2, ensure_ascii=False))
                    with open("scratch/user_eula_info_resp.json", "w", encoding="utf-8") as f:
                        json.dump(parsed, f, indent=2, ensure_ascii=False)
                    resp_body = resp_body[5+msg_len:]
                    break
        elif isinstance(ev, h2.events.StreamEnded):
            break
    if os.path.exists("scratch/user_eula_info_resp.json"):
        break

tls_sock.close()
