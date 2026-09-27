"""collector.py — รับ raw bytes จาก frida agent → reassemble → decode → สร้าง flow → store.

ต่อ 1 connection (แยกด้วย ssl ptr หรือ fd) จะเดาว่าเป็น HTTP/2 (gRPC) หรือ HTTP/1 (.ds/REST)
แล้วแตกเป็น request/response, decode body (protobuf / .ds decrypt / json / form) เป็น flow dict
ตาม schema ที่ web UI ใช้ แล้ว push เข้า server.store.emit().
"""
import re
import gzip
import json
import time
import itertools
import subprocess
import urllib.parse

import frida

from http2 import H2Direction, peek_kind
from decode import grpc_messages, pb_decode_pretty, pb_decode_obj, try_msgpack
from dsdec import decode_ds_body
from server import store

_ids = itertools.count(1)
# EpisodeAfterPlay request ~26KB+; old 16KB cap truncated capture templates.
# Keep full hex in the store/export (UI can still shorten when rendering).
MAXHEX = 2 * 1024 * 1024


# ---------- helpers ----------
def hexs(b: bytes) -> str:
    if not b:
        return ""
    if len(b) <= MAXHEX:
        return b.hex(" ")
    h = b[:MAXHEX].hex(" ")
    return h + (" …(+%dB)" % (len(b) - MAXHEX))


def _hdrget(headers, key, default=""):
    key = key.lower()
    for k, v in headers:
        if k.lower() == key:
            return v
    return default


def _printable(bs: bytes) -> bool:
    if not bs:
        return True
    p = sum(1 for c in bs if 32 <= c < 127 or c in (9, 10, 13))
    return p / len(bs) > 0.85


def classify_body(headers, body: bytes, is_request: bool):
    """คืน (kind, text, json) สำหรับ body ที่ไม่ใช่ gRPC (H1/.ds/REST). gunzip ให้อัตโนมัติ."""
    if not body:
        return "empty", "", ""
    # คลาย gzip ก่อน (content-encoding: gzip หรือ magic 1f 8b) — เช่น logging.devsisters.cloud
    ce = _hdrget(headers, "content-encoding").lower()
    if "gzip" in ce or body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)
        except Exception:
            pass
    k, t = decode_ds_body(body, is_request)      # .ds ?
    if k:
        return k, t, (t if t.strip()[:1] in ("{", "[") else "")
    mp = try_msgpack(body)
    if mp is not None:
        j = json.dumps(mp, indent=2, ensure_ascii=False)
        return "msgpack", j, j
    ct = _hdrget(headers, "content-type").lower()
    if "json" in ct or body[:1] in (b"{", b"["):
        try:
            j = json.dumps(json.loads(body.decode("utf-8", "replace")), indent=2, ensure_ascii=False)
            return "json", j, j
        except Exception:
            pass
    if "x-www-form-urlencoded" in ct or (b"&" in body[:400] and b"=" in body[:400] and _printable(body)):
        return "form", body.decode("latin1").replace("&", "\n"), ""
    if _printable(body):
        return "raw", body.decode("utf-8", "replace"), ""
    # binary ที่ไม่ใช่ text → ลอง protobuf (เช่น client log ที่ gunzip แล้ว)
    tree = pb_decode_pretty(body)
    if tree:
        obj = pb_decode_obj(body)
        jtxt = json.dumps(obj, indent=2, ensure_ascii=False) if obj is not None else ""
        return "protobuf", tree, jtxt
    return "raw", hexs(body), ""


def grpc_body(buf: bytes):
    """decode gRPC message stream → (kind, tree_text, json_text)."""
    msgs = list(grpc_messages(bytes(buf)))
    if not msgs:
        if not buf:
            return "empty", "", ""
        return "raw", hexs(bytes(buf)), ""
    parts, objs = [], []
    for i, (cf, payload) in enumerate(msgs):
        if cf == 1:                       # gRPC compressed-flag → คลาย gzip ก่อน decode
            try:
                payload = gzip.decompress(payload)
            except Exception:
                pass
        tree = pb_decode_pretty(payload)
        head = "-- message #%d (%dB, compressed=%d) --" % (i, len(payload), cf)
        parts.append(head + "\n" + (tree if tree else hexs(payload)))
        obj = pb_decode_obj(payload)
        objs.append(obj if obj is not None else {"_raw_hex": hexs(payload)})
    jtxt = json.dumps(objs[0] if len(objs) == 1 else objs, indent=2, ensure_ascii=False)
    return "protobuf", "\n\n".join(parts), jtxt


def _looks_like_afterplay_req(req_buf: bytes) -> bool:
    """protobuf คำขอ AfterPlay: มี combo 26.x_crg หรือบอดี้ใหญ่ + score/ep."""
    msgs = list(grpc_messages(bytes(req_buf)))
    if not msgs:
        return False
    cf, payload = msgs[0]
    if cf == 1:
        try:
            payload = gzip.decompress(payload)
        except Exception:
            return False
    obj = pb_decode_obj(payload) or {}
    combo = str(obj.get("19") or "")
    if "crg" in combo.lower() or combo.startswith("26.7") or combo.startswith("26.8"):
        return True
    return bool(len(req_buf) > 8000 and obj.get("8") and obj.get("9") and obj.get("10"))


def _looks_like_afterplay_res(res_buf: bytes, res_json: str) -> bool:
    blob = res_json or ""
    if "hasReplayData" in blob and "treasureBoxResultList" in blob:
        return True
    raw = bytes(res_buf or b"")
    if b"hasReplayData" in raw and b"treasureBoxResultList" in raw:
        return True
    return False


def _as_int(v):
    if v is None or isinstance(v, dict):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def afterplay_summary(rpc, req_json):
    """สรุป field ที่อ่านบ่อยจาก EpisodeAfterPlay request (protobuf field number).

    15 play_time · 9 earned_coin · 10 earned_exp · 8 score · 43 statistics
    """
    obj = None
    if req_json:
        try:
            obj = json.loads(req_json) if isinstance(req_json, str) else req_json
            if isinstance(obj, list):
                obj = obj[0] if obj else {}
        except Exception:
            obj = None
    if not isinstance(obj, dict):
        return None
    combo = str(obj.get("19") or "")
    looks = (
        rpc == "EpisodeAfterPlay"
        or "crg" in combo.lower()
        or (obj.get("15") is not None and obj.get("9") is not None and obj.get("8") is not None)
    )
    if not looks:
        return None
    st = {}
    raw_st = obj.get("43")
    if isinstance(raw_st, str):
        try:
            st = json.loads(raw_st)
        except Exception:
            st = {}
    elif isinstance(raw_st, dict):
        st = raw_st
    cj = _as_int(st.get("CoinJellyTotal"))
    play_time = _as_int(obj.get("15"))
    coin = _as_int(obj.get("9"))
    exp = _as_int(obj.get("10"))
    if play_time is None and coin is None and exp is None:
        return None
    return {
        "play_time": play_time,
        "earned_coin": coin,
        "earned_exp": exp,
        "score": _as_int(obj.get("8")),
        "pureCoin": _as_int(st.get("pureCoin")),
        "CoinJellyTotal": cj,
        "coin_cap": (cj * 280) if cj else None,
        "life_key": obj.get("11") if isinstance(obj.get("11"), str) else None,
    }


def json_of(kind, text):
    """คืน text ถ้าเป็น JSON/.ds ที่ขึ้นต้นด้วย { หรือ [ (สำหรับ JSON view); else ''."""
    if kind in ("json", "ds-decrypted") and text and text.strip()[:1] in ("{", "["):
        return text
    return ""


def now_time(ts_ms):
    import time
    lt = time.localtime(ts_ms / 1000.0)
    return time.strftime("%H:%M:%S", lt) + ".%03d" % (int(ts_ms) % 1000)


# ---------- HTTP/2 (gRPC) ----------
class H2Conn:
    def __init__(self, key):
        self.key = key
        self.dw = H2Direction()
        self.dr = H2Direction()
        self.streams = {}         # sid -> state

    def _st(self, sid):
        return self.streams.setdefault(sid, {
            "fid": next(_ids), "t0": None, "t1": None,
            "req_hdr": [], "req_buf": bytearray(), "req_done": False,
            "res_hdr": [], "res_buf": bytearray(), "res_done": False,
        })

    def feed(self, direction, data, ts):
        d = self.dw if direction == "w" else self.dr
        for ev in d.feed(bytes(data)):
            kind, sid = ev[0], ev[1]
            if sid == 0:
                continue
            st = self._st(sid)
            if direction == "w":
                if kind == "headers":
                    st["req_hdr"] = ev[2]
                    st["t0"] = st["t0"] or ts
                    if ev[3]:
                        st["req_done"] = True
                elif kind == "data":
                    st["req_buf"] += ev[2]
                    if ev[3]:
                        st["req_done"] = True
                    self._check_stream_messages(sid, st, "w", ts)
            else:
                if kind == "headers":
                    st["res_hdr"] = (st["res_hdr"] or []) + ev[2]     # response + trailer
                    st["t1"] = ts
                    if ev[3]:
                        st["res_done"] = True
                elif kind == "data":
                    st["res_buf"] += ev[2]
                    st["t1"] = ts
                    if ev[3]:
                        st["res_done"] = True
                    self._check_stream_messages(sid, st, "r", ts)
            self._flush(sid, st)

    def _check_stream_messages(self, sid, st, direction, ts):
        rh = st["req_hdr"]
        path = _hdrget(rh, ":path")
        authority = _hdrget(rh, ":authority") or "pes22-game.cs.konami.net"
        if not path or "CommandStream" not in path:
            return

        req_msgs = list(grpc_messages(bytes(st["req_buf"])))
        res_msgs = list(grpc_messages(bytes(st["res_buf"])))

        seen_req = st.setdefault("emitted_req_count", 0)
        seen_res = st.setdefault("emitted_res_count", 0)

        # Emit newly arrived request messages as distinct flows
        while seen_req < len(req_msgs):
            cf, payload = req_msgs[seen_req]
            obj = pb_decode_obj(payload) or {}
            cmd_name = str(obj.get("1") or f"MSG_{seen_req+1}")
            sub_fid = next(_ids)
            now_t = ts or (time.time() * 1000)

            sub_flow = {
                "id": sub_fid,
                "ts": now_t / 1000.0,
                "time": now_time(now_t),
                "proto": "grpc",
                "host": authority,
                "path": f"{path} [{cmd_name}]",
                "service": "CommandStream",
                "rpc": cmd_name,
                "method": "POST",
                "status": "...",
                "dur_ms": None,
                "req_size": len(payload),
                "res_size": 0,
                "request": {
                    "headers": rh,
                    "body_text": pb_decode_pretty(payload),
                    "body_json": json.dumps(obj, indent=2, ensure_ascii=False),
                    "body_hex": hexs(payload),
                    "body_kind": "protobuf",
                },
                "response": {
                    "headers": [],
                    "body_text": "",
                    "body_json": "",
                    "body_hex": "",
                    "body_kind": "empty",
                },
                "_t0": now_t,
                "_cmd": cmd_name,
            }
            st.setdefault("sub_flows", []).append(sub_flow)
            store.emit(sub_flow)
            seen_req += 1
            st["emitted_req_count"] = seen_req

        # Match newly arrived response messages
        while seen_res < len(res_msgs):
            cf, payload = res_msgs[seen_res]
            obj = pb_decode_obj(payload) or {}
            cmd_name = str(obj.get("1") or "")

            matched = None
            for sf in st.get("sub_flows", []):
                if sf["status"] == "..." and (sf["_cmd"] == cmd_name or not cmd_name):
                    matched = sf
                    break
            if not matched:
                for sf in st.get("sub_flows", []):
                    if sf["status"] == "...":
                        matched = sf
                        break

            now_t = ts or (time.time() * 1000)
            if matched:
                dur = (now_t - matched["_t0"]) if matched.get("_t0") else 0
                matched["status"] = "200"
                matched["dur_ms"] = round(dur, 1)
                matched["res_size"] = len(payload)
                matched["response"] = {
                    "headers": st["res_hdr"],
                    "body_text": pb_decode_pretty(payload),
                    "body_json": json.dumps(obj, indent=2, ensure_ascii=False),
                    "body_hex": hexs(payload),
                    "body_kind": "protobuf",
                }
                store.emit(matched)
            else:
                push_fid = next(_ids)
                push_flow = {
                    "id": push_fid,
                    "ts": now_t / 1000.0,
                    "time": now_time(now_t),
                    "proto": "grpc",
                    "host": authority,
                    "path": f"{path} [PUSH: {cmd_name or 'SERVER'}]",
                    "service": "CommandStream",
                    "rpc": cmd_name or "SERVER_PUSH",
                    "method": "PUSH",
                    "status": "200",
                    "dur_ms": 0,
                    "req_size": 0,
                    "res_size": len(payload),
                    "request": {"headers": [], "body_text": "", "body_json": "", "body_hex": "", "body_kind": "empty"},
                    "response": {
                        "headers": st["res_hdr"],
                        "body_text": pb_decode_pretty(payload),
                        "body_json": json.dumps(obj, indent=2, ensure_ascii=False),
                        "body_hex": hexs(payload),
                        "body_kind": "protobuf",
                    },
                }
                store.emit(push_flow)
            seen_res += 1
            st["emitted_res_count"] = seen_res

    def _flush(self, sid, st):
        rh = st["req_hdr"]
        hpack_broken = (any(k == "__hpack_error__" for k, v in rh)
                        or any(k == "__hpack_error__" for k, v in st["res_hdr"]))
        path = _hdrget(rh, ":path")
        authority = _hdrget(rh, ":authority")
        ctype = _hdrget(rh, "content-type").lower()
        res_ctype = _hdrget(st["res_hdr"], "content-type").lower()
        gstatus_hdr = _hdrget(st["res_hdr"], "grpc-status")
        # body เป็น gRPC message ไหม — ใช้ยืนยัน gRPC แม้ header HPACK เพี้ยน (attach กลางคัน)
        looks_grpc = bool(list(grpc_messages(bytes(st["req_buf"])))
                          or list(grpc_messages(bytes(st["res_buf"]))))
        is_grpc = ("grpc" in ctype
                   or "grpc" in res_ctype
                   or gstatus_hdr != ""
                   or bool(re.match(r"^/[A-Za-z0-9_.]+/[A-Za-z0-9_]+$", path or ""))
                   or (hpack_broken and looks_grpc))
        service = rpc = ""
        if path and path.count("/") >= 2 and is_grpc:
            _, service, rpc = path.split("/", 2)
        if not rpc and is_grpc:
            if _looks_like_afterplay_req(st["req_buf"]) or _looks_like_afterplay_res(
                    st["res_buf"], ""):
                service = service or "service.api.EpisodeAPI"
                rpc = "EpisodeAfterPlay"
        if hpack_broken and not path:
            path = "⚠ HPACK desync — รัน run.py --restart เพื่อ sync (ดู path/service เต็ม)"
        elif is_grpc and not path and not st["req_hdr"]:
            path = "⚠ ไม่มี request header (H2 ฝั่งเขียนหลุด — รีสตาร์ต Monitor แล้วเล่นอีกรอบ)"

        if is_grpc:
            rk, rt, rj = grpc_body(st["req_buf"])
            sk, sstext, sj = grpc_body(st["res_buf"])
        else:
            rk, rt, rj = classify_body(rh, bytes(st["req_buf"]), True)
            sk, sstext, sj = classify_body(st["res_hdr"], bytes(st["res_buf"]), False)

        status = _hdrget(st["res_hdr"], ":status")
        gstatus = _hdrget(st["res_hdr"], "grpc-status")
        status_disp = status + (" / grpc=%s" % gstatus if gstatus != "" else "")
        dur = (st["t1"] - st["t0"]) if (st["t0"] and st["t1"]) else None

        flow = {
            "id": st["fid"], "ts": (st["t0"] or 0) / 1000.0, "time": now_time(st["t0"] or 0),
            "proto": "grpc" if is_grpc else "http",
            "host": authority, "path": path or "",
            "service": service, "rpc": rpc,
            "method": _hdrget(rh, ":method", "POST"),
            "status": status_disp.strip(" /"),
            "dur_ms": round(dur, 1) if dur is not None else None,
            "req_size": len(st["req_buf"]), "res_size": len(st["res_buf"]),
            "request": {"headers": rh, "body_text": rt, "body_json": rj, "body_hex": hexs(bytes(st["req_buf"])), "body_kind": rk},
            "response": {"headers": st["res_hdr"], "body_text": sstext, "body_json": sj,
                         "body_hex": hexs(bytes(st["res_buf"])), "body_kind": sk},
        }
        ap = afterplay_summary(rpc, rj)
        if ap:
            flow["afterplay"] = ap
        store.emit(flow)


# ---------- HTTP/1 (.ds / REST) ----------
_H1_REQ = re.compile(rb"^(GET|POST|PUT|HEAD|DELETE|OPTIONS|PATCH) ([^ ]+) HTTP/1\.[01]\r\n")
_H1_RES = re.compile(rb"^HTTP/1\.[01] (\d{3})")


class H1Conn:
    def __init__(self, key):
        self.key = key
        self.wbuf = bytearray()
        self.rbuf = bytearray()
        self.pending = []          # req ที่ยังรอ response (FIFO)

    def feed(self, direction, data, ts):
        if direction == "w":
            self.wbuf += data
            self._parse(self.wbuf, True, ts)
        else:
            self.rbuf += data
            self._parse(self.rbuf, False, ts)

    def _parse(self, buf, is_req, ts):
        while True:
            hdr_end = buf.find(b"\r\n\r\n")
            if hdr_end < 0:
                return
            head = bytes(buf[:hdr_end])
            lines = head.split(b"\r\n")
            start = lines[0]
            if is_req and not _H1_REQ.match(start + b"\r\n"):
                del buf[:hdr_end + 4]
                continue
            if not is_req and not _H1_RES.match(start):
                del buf[:hdr_end + 4]
                continue
            headers = []
            for ln in lines[1:]:
                if b":" in ln:
                    k, v = ln.split(b":", 1)
                    headers.append((k.decode("latin1").strip(), v.decode("latin1").strip()))
            clen = int(_hdrget(headers, "content-length", "0") or 0)
            chunked = "chunked" in _hdrget(headers, "transfer-encoding").lower()
            body_start = hdr_end + 4
            if chunked:
                body, consumed = self._dechunk(buf[body_start:])
                if body is None:
                    return
                total = body_start + consumed
            else:
                if len(buf) < body_start + clen:
                    return
                body = bytes(buf[body_start:body_start + clen])
                total = body_start + clen
            del buf[:total]
            if is_req:
                self._on_req(start, headers, body, ts)
            else:
                self._on_res(start, headers, body, ts)

    def _dechunk(self, data):
        out = bytearray()
        i = 0
        while True:
            nl = data.find(b"\r\n", i)
            if nl < 0:
                return None, 0
            try:
                size = int(bytes(data[i:nl]).split(b";")[0], 16)
            except ValueError:
                return None, 0
            if size == 0:
                end = data.find(b"\r\n", nl + 2)
                return bytes(out), (end + 2 if end >= 0 else nl + 4)
            if len(data) < nl + 2 + size + 2:
                return None, 0
            out += data[nl + 2:nl + 2 + size]
            i = nl + 2 + size + 2

    def _on_req(self, start, headers, body, ts):
        m = _H1_REQ.match(start + b"\r\n")
        method = m.group(1).decode()
        path = m.group(2).decode("latin1")
        fid = next(_ids)
        kind, text, bjson = classify_body(headers, body, True)
        proto = "ds" if ".ds" in path or kind.startswith("ds") else "http"
        req = {
            "id": fid, "ts": ts / 1000.0, "time": now_time(ts), "proto": proto,
            "host": _hdrget(headers, "host"), "path": path, "service": "", "rpc": "",
            "method": method, "status": "", "dur_ms": None,
            "req_size": len(body), "res_size": 0,
            "request": {"headers": headers, "body_text": text, "body_json": bjson, "body_hex": hexs(body), "body_kind": kind},
            "response": {"headers": [], "body_text": "", "body_json": "", "body_hex": "", "body_kind": "empty"},
            "_t0": ts,
        }
        self.pending.append(req)
        store.emit({k: v for k, v in req.items() if not k.startswith("_")})

    def _on_res(self, start, headers, body, ts):
        status = _H1_RES.match(start).group(1).decode()
        if not self.pending:
            return
        req = self.pending.pop(0)
        kind, text, bjson = classify_body(headers, body, False)
        if req["proto"] == "ds" and kind.startswith("ds"):
            pass
        req["status"] = status
        req["res_size"] = len(body)
        req["dur_ms"] = round(ts - req["_t0"], 1)
        req["response"] = {"headers": headers, "body_text": text, "body_json": bjson, "body_hex": hexs(body), "body_kind": kind}
        store.emit({k: v for k, v in req.items() if not k.startswith("_")})


# ---------- connection manager ----------
class ConnManager:
    def __init__(self):
        self.conns = {}
        self.pending_r = {}        # key -> [(data,ts)] เผื่อ response byte มาก่อน request
        self.pending_w = {}        # รอจนเดา http2/http1 ได้ — ห้ามล็อก H1 จากก้อนแรกที่ยังไม่ครบ
        self._seen = 0

    def _open(self, key, kind):
        # gRPC เป็น HTTP/2 — ถ้ายังไม่ชัวร์ว่าเป็น HTTP/1 ให้ลอง H2 ก่อน
        conn = H1Conn(key) if kind == "http1" else H2Conn(key)
        self.conns[key] = conn
        for rdata, rts in self.pending_r.pop(key, []):
            try:
                conn.feed("r", rdata, rts)
            except Exception:
                pass
        for wdata, wts in self.pending_w.pop(key, []):
            try:
                conn.feed("w", wdata, wts)
            except Exception:
                pass
        return conn

    def feed(self, t, key, data, ts):
        if data is None:
            return
        data = bytes(data)
        direction = "w" if t in ("w", "sw") else "r"
        store.note_bytes(direction, len(data))
        self._seen += 1
        if self._seen <= 12:
            print("[collector] %s %dB key=%s" % (direction, len(data), key[-18:]), flush=True)
        conn = self.conns.get(key)
        if conn is None:
            if direction == "r":
                self.pending_r.setdefault(key, []).append((data, ts))
                return
            self.pending_w.setdefault(key, []).append((data, ts))
            sample = b"".join(x[0] for x in self.pending_w[key][:12])
            kind = peek_kind(sample)
            if kind == "other" and len(sample) < 24:
                return
            if self._seen <= 12:
                print("[collector] open %s as %s" % (key[-18:], kind), flush=True)
            self._open(key, kind)
            return
        try:
            conn.feed(direction, data, ts)
        except Exception as e:
            print("[collector] feed error on %s: %s" % (key, e))


# ---------- frida ----------
# eFootball by Konami. Gadget mode: the frida Gadget is baked into the APK, so the
# only process we ever see on the remote device is the Gadget (== the game process).
PKG = "jp.konami.pesam"
GAME_NAMES = ("eFootball", "Gadget", "jp.konami.pesam", "PES")


_cri_http_reqs = {}

def _make_on_message(cm):
    def on_message(message, data):
        if message.get("type") == "send":
            p = message.get("payload") or {}
            t = p.get("t")
            if t in ("w", "r"):
                cm.feed(t, "ssl:" + p["ssl"], data, p["ts"])
            elif t in ("sw", "sr"):
                cm.feed(t, "fd:" + str(p["fd"]), data, p["ts"])
            elif t == "http_open":
                tag = str(p.get("tag"))
                url = p.get("url") or ""
                parsed = urllib.parse.urlparse(url)
                fid = next(_ids)
                now_t = p.get("ts") or (time.time() * 1000)
                
                path_part = parsed.path or "/"
                rpc_name = path_part.split("/")[-1] if "/" in path_part else ""
                if rpc_name.endswith(".php"):
                    rpc_name = rpc_name[:-4]

                flow = {
                    "id": fid,
                    "ts": now_t / 1000.0,
                    "time": now_time(now_t),
                    "proto": "http",
                    "host": parsed.netloc or "service.konami.net",
                    "path": path_part + (("?" + parsed.query) if parsed.query else ""),
                    "service": "CriHttp",
                    "rpc": rpc_name,
                    "method": "POST" if ("Cmd" in path_part or "api" in path_part) else "GET",
                    "status": "...",
                    "dur_ms": None,
                    "req_size": 0,
                    "res_size": 0,
                    "request": {
                        "headers": [["url", url]],
                        "body_text": "",
                        "body_json": "",
                        "body_hex": "",
                        "body_kind": "empty",
                    },
                    "response": {
                        "headers": [],
                        "body_text": "",
                        "body_json": "",
                        "body_hex": "",
                        "body_kind": "empty",
                    },
                    "_t0": now_t,
                    "_req_buf": bytearray(),
                    "_res_buf": bytearray(),
                }
                _cri_http_reqs[tag] = flow
                store.emit({k: v for k, v in flow.items() if not k.startswith("_")})

            elif t == "http_hdr":
                tag = str(p.get("tag"))
                k = p.get("key")
                v = p.get("val")
                if tag in _cri_http_reqs and k and v:
                    _cri_http_reqs[tag]["request"]["headers"].append([k, v])

            elif t == "http_body_w":
                tag = str(p.get("tag"))
                if tag in _cri_http_reqs and data:
                    flow = _cri_http_reqs[tag]
                    flow["_req_buf"].extend(data)
                    flow["req_size"] = len(flow["_req_buf"])
                    kind, text, bjson = classify_body(flow["request"]["headers"], bytes(flow["_req_buf"]), True)
                    flow["request"]["body_kind"] = kind
                    flow["request"]["body_text"] = text
                    flow["request"]["body_json"] = bjson
                    flow["request"]["body_hex"] = hexs(bytes(flow["_req_buf"]))
                    store.emit({k: v for k, v in flow.items() if not k.startswith("_")})

            elif t == "http_status":
                status_code = str(p.get("status") or "")
                # find most recent pending cri http request
                for flow in reversed(list(_cri_http_reqs.values())):
                    if flow["status"] == "...":
                        flow["status"] = status_code
                        now_t = p.get("ts") or (time.time() * 1000)
                        if flow.get("_t0"):
                            flow["dur_ms"] = round(now_t - flow["_t0"], 1)
                        store.emit({k: v for k, v in flow.items() if not k.startswith("_")})
                        break

            elif t == "http_body_r":
                tag = str(p.get("tag"))
                if tag in _cri_http_reqs and data:
                    flow = _cri_http_reqs[tag]
                    flow["_res_buf"].extend(data)
                    flow["res_size"] = len(flow["_res_buf"])
                    kind, text, bjson = classify_body(flow["response"]["headers"], bytes(flow["_res_buf"]), False)
                    flow["response"]["body_kind"] = kind
                    flow["response"]["body_text"] = text
                    flow["response"]["body_json"] = bjson
                    flow["response"]["body_hex"] = hexs(bytes(flow["_res_buf"]))
                    store.emit({k: v for k, v in flow.items() if not k.startswith("_")})

            elif t == "http_close":
                tag = str(p.get("tag"))
                if tag in _cri_http_reqs:
                    flow = _cri_http_reqs.pop(tag)
                    if flow["status"] == "...":
                        flow["status"] = "200"
                    now_t = p.get("ts") or (time.time() * 1000)
                    if flow.get("_t0") and not flow.get("dur_ms"):
                        flow["dur_ms"] = round(now_t - flow["_t0"], 1)
                    store.emit({k: v for k, v in flow.items() if not k.startswith("_")})

        elif message.get("type") == "log":
            print("[agent]", message.get("payload"), flush=True)
        elif message.get("type") == "error":
            print("[agent error]", message.get("stack") or message.get("description"), flush=True)
    return on_message


def _find_game(dev):
    """หา (pid, name) ของเกม. **ปล่อย exception** ถ้า enumerate ล้มเหลว (= remote device
    stale / gadget หาย / forward หลุด) เพื่อให้ loop สั่ง reconnect.
    Gadget mode: process เดียวที่เห็นคือ Gadget (= ตัวเกมเอง)."""
    procs = dev.enumerate_processes()
    for p in procs:
        if any(nm.lower() in p.name.lower() for nm in GAME_NAMES):
            return p.pid, p.name
    # gadget lists itself as 'Gadget' — but if renamed, just take the first process
    if procs:
        return procs[0].pid, procs[0].name
    return None, None


# ---------- adb / frida-server setup (ใช้ตอน start และตอน reconnect) ----------
def _sh(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def _get_device_serial():
    r = _sh(["adb", "devices"])
    if r and r.returncode == 0:
        for ln in (r.stdout or "").splitlines()[1:]:
            parts = ln.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    return None


def adb_sh(args, timeout=20):
    dev = _get_device_serial()
    if dev:
        return _sh(["adb", "-s", dev] + args, timeout=timeout)
    return _sh(["adb"] + args, timeout=timeout)


def adb_online():
    """มี adb device พร้อมใช้ (state=device) อย่างน้อย 1 เครื่องไหม."""
    dev = _get_device_serial()
    if dev:
        return True
    # ลอง connect MuMu (5557 / 16384) เผื่อเพิ่งเปิด emulator
    for p in (5557, 16384, 7555, 5555):
        _sh(["adb", "connect", "127.0.0.1:%d" % p], timeout=3)
    return _get_device_serial() is not None


def ensure_frida_server(fport):
    """Gadget mode — ไม่มี frida-server. Gadget ถูกฝังใน APK แล้ว listen บน fport
    เมื่อเกมรัน. ที่ต้องทำ: adb forward + ทำให้แน่ใจว่าเกมรันอยู่."""
    if not _get_device_serial():
        _sh(["adb", "connect", "127.0.0.1:5557"], timeout=3)
    r = adb_sh(["shell", "pidof %s" % PKG])
    if not (r and (r.stdout or "").strip()):
        adb_sh(["shell", "monkey -p %s -c android.intent.category.LAUNCHER 1" % PKG])
        time.sleep(6)
    adb_sh(["forward", "tcp:%d" % fport, "tcp:%d" % fport])


def _make_device(host):
    """Gadget mode: remote device = the Gadget socket (host = 127.0.0.1:PORT)."""
    dm = frida.get_device_manager()
    try:
        return dm.add_remote_device(host)
    except Exception:
        try:
            dm.remove_remote_device(host)
        except Exception:
            pass
        return dm.add_remote_device(host)


def auto_attach_loop(host, agent_path, frida_port=27053, do_setup=True, poll=2.0):
    """เปิดค้างไว้แบบ Charles — เจอเกมเมื่อไหร่ attach ทันที.
    เกาะต่อได้ทั้ง 3 กรณี: เกมปิด-เปิด, เกม restart, และ **adb หลุด/เครื่อง reconnect**
    (พอเครื่องกลับมา → ตั้ง frida-server + adb forward + สร้าง remote device ใหม่อัตโนมัติ).
    traffic ทุก session ไหลเข้า ConnManager ตัวเดียว (flow ต่อเนื่องบนเว็บ)."""
    cm = ConnManager()
    on_message = _make_on_message(cm)
    state = {"pid": None}
    agent_src = open(agent_path, "r", encoding="utf-8").read()
    # main() ตั้ง frida-server + forward ให้แล้วตอน start → เริ่มด้วยสมมติ online + device พร้อม
    online = True
    try:
        dev = _make_device(host)
    except Exception as e:
        print("[auto] สร้าง remote device ครั้งแรกไม่ผ่าน: %s (จะลองใหม่)" % e, flush=True)
        dev, online = None, False

    def detached(reason, *a):
        print("[auto] เกมหลุด (%s) — รอเปิดใหม่..." % reason, flush=True)
        state["pid"] = None

    def reconnect():
        """เรียกตอน adb เพิ่งกลับมา (หรือ device stale): ตั้ง frida-server + forward + device ใหม่."""
        print("[auto] adb กลับมา/เชื่อมใหม่ — ตั้ง frida-server + forward + remote device ใหม่ ...", flush=True)
        if do_setup:
            ensure_frida_server(frida_port)
        d = _make_device(host)
        print("[auto] ✓ reconnect สำเร็จ — เกาะต่อ รอเกม", flush=True)
        return d

    print("[auto] watcher เริ่มทำงาน — รอเกมเปิด (poll ทุก %.0fs, auto-reconnect adb ON)" % poll, flush=True)
    while True:
        try:
            # 1) เครื่อง adb ยังต่ออยู่ไหม
            if not adb_online():
                if online:
                    print("[auto] ⚠ adb หลุด — รอเครื่องกลับมา (เกาะไว้ให้เอง) ...", flush=True)
                online = False
                state["pid"] = None
                time.sleep(poll)
                continue
            # 2) adb online — ถ้าเพิ่งกลับมา (หรือยังไม่มี device) → reconnect
            if not online or dev is None:
                dev = reconnect()
                online = True

            # 3) หาเกม + attach
            pid, name = _find_game(dev)
            if pid and pid != state["pid"]:
                try:
                    try:                          # hot-reload agent.js ทุกครั้ง (แก้ agent ไม่ต้อง restart)
                        agent_src = open(agent_path, "r", encoding="utf-8").read()
                    except Exception:
                        pass
                    session = dev.attach(pid)
                    script = session.create_script(agent_src)
                    script.on("message", on_message)
                    script.load()
                    session.on("detached", detached)
                    state["pid"] = pid
                    store.note_attach(pid, name)
                    print("[auto] ✓ attached %s (pid=%d) — traffic เริ่มไหลแล้ว" % (name, pid), flush=True)
                except Exception as e:
                    print("[auto] attach pid=%d ไม่ผ่าน: %s (ลองใหม่รอบหน้า)" % (pid, e), flush=True)
                    state["pid"] = None
            elif not pid:
                state["pid"] = None
        except Exception as e:
            # enumerate/attach ล้มด้วย transport error = remote device stale (frida-server/forward หาย)
            # → บังคับ reconnect รอบหน้า
            print("[auto] loop error: %s — จะ reconnect รอบหน้า" % e, flush=True)
            state["pid"] = None
            online = False
            try:
                frida.get_device_manager().remove_remote_device(host)
            except Exception:
                pass
            dev = None
        time.sleep(poll)
