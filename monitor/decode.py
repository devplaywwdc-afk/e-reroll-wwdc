"""decode.py — แยก gRPC message ออกจาก DATA stream + decode protobuf แบบ generic (ไม่ต้องมี .proto).

- grpc_messages(): แตก gRPC length-prefixed messages (1B compressed flag + 4B len + payload)
- pb_decode_pretty(): decode protobuf wire-format เป็นข้อความ tree อ่านรู้เรื่อง
  แสดง field number + wire type + ค่า; length-delimited จะลอง decode เป็น nested message,
  ถ้าไม่เข้าเค้าก็แสดงเป็น string (ถ้า printable) หรือ hex
"""
import struct
import json
import base64


def grpc_messages(buf: bytes):
    """yield (compressed_flag, payload_bytes) จาก gRPC DATA stream."""
    i, n = 0, len(buf)
    while i + 5 <= n:
        cf = buf[i]
        mlen = struct.unpack(">I", buf[i + 1:i + 5])[0]
        if i + 5 + mlen > n:
            break
        yield cf, buf[i + 5:i + 5 + mlen]
        i += 5 + mlen


def _read_varint(b, i):
    shift = result = 0
    while i < len(b):
        byte = b[i]; i += 1
        result |= (byte & 0x7f) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("varint เกิน")


def _printable(bs: bytes) -> bool:
    if not bs:
        return False
    printable = sum(1 for c in bs if 32 <= c < 127 or c in (9, 10, 13))
    return printable / len(bs) > 0.85


def _hex(bs: bytes, limit=64) -> str:
    h = bs[:limit].hex(" ")
    return h + (f" …(+{len(bs)-limit}B)" if len(bs) > limit else "")


def pb_decode_pretty(data: bytes, indent=0, depth=0) -> str:
    """decode protobuf เป็นข้อความ tree. คืน '' ถ้า parse ไม่ผ่าน (ให้ผู้เรียก fallback)."""
    lines = []
    pad = "  " * indent
    i, n = 0, len(data)
    if n == 0:
        return ""
    while i < n:
        try:
            tag, i = _read_varint(data, i)
        except ValueError:
            return "" if depth == 0 else "\n".join(lines)
        field = tag >> 3
        wire = tag & 0x7
        if field == 0:
            return "" if depth == 0 else "\n".join(lines)
        if wire == 0:      # varint
            try:
                val, i = _read_varint(data, i)
            except ValueError:
                return "" if depth == 0 else "\n".join(lines)
            lines.append(f"{pad}#{field} varint: {val}")
        elif wire == 1:    # 64-bit
            if i + 8 > n:
                return "" if depth == 0 else "\n".join(lines)
            raw = data[i:i + 8]; i += 8
            val = struct.unpack("<q", raw)[0]
            dbl = struct.unpack("<d", raw)[0]
            lines.append(f"{pad}#{field} i64: {val} (double {dbl:g})")
        elif wire == 2:    # length-delimited
            try:
                ln, i = _read_varint(data, i)
            except ValueError:
                return "" if depth == 0 else "\n".join(lines)
            if i + ln > n:
                return "" if depth == 0 else "\n".join(lines)
            sub = data[i:i + ln]; i += ln
            mp = try_msgpack(sub)
            if mp is not None:
                lines.append(f"{pad}#{field} msgpack ({ln}B):")
                for mp_line in json.dumps(mp, indent=2, ensure_ascii=False).splitlines():
                    lines.append(f"{pad}  {mp_line}")
            else:
                nested = pb_decode_pretty(sub, indent + 1, depth + 1) if (sub and depth < 12) else ""
                if nested:
                    lines.append(f"{pad}#{field} msg ({ln}B):")
                    lines.append(nested)
                elif _printable(sub):
                    lines.append(f'{pad}#{field} str: "{sub.decode("utf-8", "replace")}"')
                else:
                    lines.append(f"{pad}#{field} bytes({ln}): {_hex(sub)}")
        elif wire == 5:    # 32-bit
            if i + 4 > n:
                return "" if depth == 0 else "\n".join(lines)
            raw = data[i:i + 4]; i += 4
            val = struct.unpack("<i", raw)[0]
            flt = struct.unpack("<f", raw)[0]
            lines.append(f"{pad}#{field} i32: {val} (float {flt:g})")
        else:
            return "" if depth == 0 else "\n".join(lines)
    return "\n".join(lines)


def try_msgpack(b: bytes):
    """ลองแตก msgpack dictionary/list คืน dict/list หรือ None ถ้าไม่ใช่.
    รองรับทั้ง raw binary msgpack และ base64-encoded msgpack (แบบที่ eFootball ใช้)."""
    if not b or len(b) < 2:
        return None
    try:
        import msgpack
        res = msgpack.unpackb(b, raw=False)
        if isinstance(res, (dict, list)):
            return res
    except Exception:
        pass
    try:
        import base64
        import msgpack
        raw = base64.b64decode(b)
        if len(raw) >= 2:
            res = msgpack.unpackb(raw, raw=False)
            if isinstance(res, (dict, list)):
                return res
    except Exception:
        pass
    return None


def pb_decode_obj(data, depth=0):
    """protobuf → dict (field number เป็น key) สำหรับ JSON view.
    คืน None ถ้า parse ไม่ผ่าน; field ซ้ำ (repeated) → list; nested msg → dict;
    length-delim ที่ไม่ใช่ msg → string (ถ้า printable) หรือ {"_hex": ...}."""
    out = {}
    i, n = 0, len(data)
    if n == 0:
        return {}
    while i < n:
        try:
            tag, i = _read_varint(data, i)
        except ValueError:
            return None
        field, wire = tag >> 3, tag & 0x7
        if field == 0:
            return None
        key = str(field)
        if wire == 0:
            try:
                val, i = _read_varint(data, i)
            except ValueError:
                return None
        elif wire == 1:
            if i + 8 > n:
                return None
            val = struct.unpack("<q", data[i:i + 8])[0]; i += 8
        elif wire == 2:
            try:
                ln, i = _read_varint(data, i)
            except ValueError:
                return None
            if i + ln > n:
                return None
            sub = data[i:i + ln]; i += ln
            mp = try_msgpack(sub)
            if mp is not None:
                val = mp
            else:
                nested = pb_decode_obj(sub, depth + 1) if (sub and depth < 12) else None
                if nested:
                    val = nested
                elif _printable(sub):
                    val = sub.decode("utf-8", "replace")
                else:
                    val = {"_hex": _hex(sub)}
        elif wire == 5:
            if i + 4 > n:
                return None
            val = struct.unpack("<i", data[i:i + 4])[0]; i += 4
        else:
            return None
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(val)
        else:
            out[key] = val
    return out
