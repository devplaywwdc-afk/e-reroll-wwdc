import ssl, socket, struct, base64, json, time, msgpack
import h2.connection, h2.events

HOST = 'pes22-game.cs.konami.net'
PORT = 443

def encode_varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value & 0x7f)
    return bytes(out)

def build_grpc_payload(cmd_name, seq, msgpack_dict, field4='/'):
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
    return b'\x00' + struct.pack('>I', len(pb)) + bytes(pb)

def decode_grpc_response(data):
    if len(data) < 5: return None
    msg_len = struct.unpack('>I', data[1:5])[0]
    payload = data[5:5+msg_len]
    idx = 0; cmd_name = ''; seq = 0; b64_data = b''
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
    result = {'cmd': cmd_name, 'seq': seq}
    if b64_data:
        try:
            mp_bytes = base64.b64decode(b64_data)
            result['data'] = msgpack.unpackb(mp_bytes, raw=False)
        except Exception as e:
            result['error'] = str(e)
    return result

def try_login(uid, auth_code, dev_id='3e04704dd8c1e044'):
    ctx = ssl.create_default_context()
    ctx.set_alpn_protocols(['h2'])
    sock = socket.create_connection((HOST, PORT), timeout=10)
    tls = ctx.wrap_socket(sock, server_hostname=HOST)
    conn = h2.connection.H2Connection()
    conn.initiate_connection()
    tls.sendall(conn.data_to_send())
    conn.receive_data(tls.recv(65535))
    tls.sendall(conn.data_to_send())
    
    sid = conn.get_next_available_stream_id()
    conn.send_headers(sid, [
        (':scheme', 'https'), (':method', 'POST'),
        (':authority', f'{HOST}:{PORT}'),
        (':path', '/command_service.CommandService/CommandStream'),
        ('te', 'trailers'), ('content-type', 'application/grpc'),
        ('user-agent', 'grpc-c++/1.26.0 grpc-c/9.0.0 (android; chttp2; gon)'),
        ('grpc-accept-encoding', 'identity,deflate,gzip'),
        ('accept-encoding', 'identity,gzip'),
    ], end_stream=False)
    tls.sendall(conn.data_to_send())
    
    payload = {
        'msgid': 'CMD_LOGIN', 'rqid': 1, 'user_id': uid, 'session_id': '',
        'my_platform': 'ANDROID', 's_keyword': '', 'auth_code': auth_code,
        'hash': 'e8bbc34f90e46bbbbb6ef7a7a74df3ba', 'client_version': '6.0.1',
        'platform': 'ANDROID', 'device': {'device_identifier': dev_id},
        'lang': 'TH', 'country_code': ['THA'], 'region': 'asia', 'pcspec': '',
        'cross_platform_option': 'ON', 'is_disable_sharing': 'NO',
        'os_version': 'Android OS 12 / API-32', 'model_name': 'SM-F741N',
        'gpu_name': 'Adreno (TM) 640', 'soc_name': 'Qualcomm Snapdragon 888',
        'is_rooting': 'NO', 'phy_mem_used_mib': 1024, 'phy_mem_available_mib': 2048,
        'app_storage_used_mib': 500, 'app_storage_available_mib': 10000,
        'data_storage_used_mib': 2000, 'data_storage_available_mib': 20000,
        'payment_store_link_send_info': {'country_code': 'THA'}
    }
    frame = build_grpc_payload('CMD_LOGIN', 1, payload)
    conn.send_data(sid, frame, end_stream=False)
    tls.sendall(conn.data_to_send())
    
    resp_body = bytearray()
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            d = tls.recv(65535)
            if not d: break
            for ev in conn.receive_data(d):
                if isinstance(ev, h2.events.DataReceived):
                    resp_body.extend(ev.data)
                    conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    if len(resp_body) >= 5:
                        mlen = struct.unpack('>I', resp_body[1:5])[0]
                        if len(resp_body) >= 5 + mlen:
                            res = decode_grpc_response(bytes(resp_body[:5+mlen]))
                            tls.close()
                            return res
        except socket.timeout: break
    tls.close()
    return None

# credentials redacted for git — load your own (user_id, auth_code) pairs locally,
# e.g. from a gitignored accounts.json:  accounts = json.load(open("accounts.json"))
accounts = [
    # (2000000000, "<auth_code_hex>"),
]
for uid, ac in accounts:
    r = try_login(uid, ac)
    data = r.get('data', {}) if r else {}
    print(f"UID {uid}: {data.get('result')} | {data.get('error_msg')} | {data.get('error_code')}")
