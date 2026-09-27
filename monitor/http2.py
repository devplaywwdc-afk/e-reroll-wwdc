"""http2.py — HTTP/2 frame reassembly + HPACK decode ต่อ 1 ทิศทางของ connection.

feed() รับ byte ที่ decrypt แล้ว (จาก SSL_read/SSL_write) ทีละก้อน ต่อกันเป็น stream
แล้วแยกเป็น HTTP/2 frame. คืน event: ('headers', stream_id, [(k,v)...]) / ('data', stream_id, bytes, end_stream)
HPACK ใช้ library `hpack` (stateful ต่อทิศทาง).
"""
from hpack import Decoder as HpackDecoder

H2_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
FRAME_TYPES = {
    0x0: "DATA", 0x1: "HEADERS", 0x2: "PRIORITY", 0x3: "RST_STREAM",
    0x4: "SETTINGS", 0x5: "PUSH_PROMISE", 0x6: "PING", 0x7: "GOAWAY",
    0x8: "WINDOW_UPDATE", 0x9: "CONTINUATION",
}
FLAG_END_STREAM = 0x1
FLAG_END_HEADERS = 0x4
FLAG_PADDED = 0x8
FLAG_PRIORITY = 0x20


class H2Direction:
    """สะสม byte + แตก frame + decode HPACK สำหรับ 1 ทิศทาง (client→server หรือ ตรงข้าม)."""

    def __init__(self):
        self.buf = bytearray()
        self.hpack = HpackDecoder()
        self.hpack.max_header_list_size = 1 << 20
        self.saw_preface = False
        self.is_http2 = None            # None=ยังไม่รู้, True/False
        self._hdr_frag = {}             # stream_id -> bytearray (สะสม HEADERS+CONTINUATION)

    def feed(self, data: bytes):
        """ป้อน byte เพิ่ม คืน list ของ event."""
        self.buf += data
        events = []

        # ครั้งแรก: เดาว่าเป็น HTTP/2 ไหม
        # ห้ามล็อก is_http2=False ถาวร — attach กลางคันเจอไบต์กลางเฟรม
        # แล้วคำขอ EpisodeAfterPlay ทั้งก้อนจะถูกกลืน (เหลือแต่คำตอบฝั่งอ่าน)
        if self.is_http2 is None:
            if self.buf[:len(H2_PREFACE)] == H2_PREFACE:
                self.is_http2 = True
                self.saw_preface = True
                del self.buf[:len(H2_PREFACE)]
            elif self._peek_looks_like_frame() or self._try_resync():
                self.is_http2 = True
            else:
                if len(self.buf) > 65536:
                    del self.buf[:-32768]
                return events

        if not self.is_http2:
            return events

        # แตก frame ทีละอัน
        while len(self.buf) >= 9:
            flen = (self.buf[0] << 16) | (self.buf[1] << 8) | self.buf[2]
            ftype = self.buf[3]
            flags = self.buf[4]
            sid = ((self.buf[5] & 0x7f) << 24) | (self.buf[6] << 16) | (self.buf[7] << 8) | self.buf[8]
            if len(self.buf) < 9 + flen:
                break                    # ยังมาไม่ครบ รอ byte เพิ่ม
            payload = bytes(self.buf[9:9 + flen])
            del self.buf[:9 + flen]
            self._handle_frame(ftype, flags, sid, payload, events)
        return events

    def _peek_looks_like_frame(self):
        return self._frame_at(0) is not None

    def _frame_at(self, i):
        """คืน (flen, ftype, sid) ถ้า buf[i:] เป็น HTTP/2 frame header ที่สมเหตุสมผล."""
        if i < 0 or len(self.buf) < i + 9:
            return None
        ftype = self.buf[i + 3]
        if ftype not in FRAME_TYPES:
            return None
        if self.buf[i + 5] & 0x80:
            return None
        flen = (self.buf[i] << 16) | (self.buf[i + 1] << 8) | self.buf[i + 2]
        if flen > 1024 * 1024:
            return None
        sid = ((self.buf[i + 5] & 0x7f) << 24) | (self.buf[i + 6] << 16) | (self.buf[i + 7] << 8) | self.buf[i + 8]
        if ftype in (0x0, 0x1, 0x2, 0x3, 0x5, 0x9) and sid == 0:
            return None
        if ftype == 0x4:  # SETTINGS — stream 0, payload เป็นคู่ 6 ไบต์
            if sid != 0 or flen % 6 != 0:
                return None
        if ftype == 0x6:  # PING
            if sid != 0 or flen != 8:
                return None
        if ftype == 0x7 and sid != 0:  # GOAWAY
            return None
        if ftype == 0x8 and flen != 4:  # WINDOW_UPDATE
            return None
        return flen, ftype, sid

    def _try_resync(self):
        """หาเฟรม HTTP/2 ใน buf (attach กลางคัน). ตัดขยะหน้าจุด sync. คืน True ถ้าเจอ."""
        n = len(self.buf)
        if n < 9:
            return False
        for i in range(0, n - 8):
            if self._frame_at(i) is None:
                continue
            if i:
                del self.buf[:i]
            return True
        return False

    def _handle_frame(self, ftype, flags, sid, payload, events):
        name = FRAME_TYPES.get(ftype)
        if name in ("HEADERS", "CONTINUATION"):
            block = payload
            if name == "HEADERS":
                # ตัด padding / priority ออกก่อน HPACK block
                idx = 0
                pad = 0
                if flags & FLAG_PADDED:
                    pad = block[idx]; idx += 1
                if flags & FLAG_PRIORITY:
                    idx += 5
                block = block[idx:len(block) - pad] if pad else block[idx:]
            frag = self._hdr_frag.setdefault(sid, bytearray())
            frag += block
            if flags & FLAG_END_HEADERS:
                hdrs = None
                try:
                    hdrs = self.hpack.decode(bytes(frag))
                    hdrs = [(k.decode() if isinstance(k, bytes) else k,
                             v.decode() if isinstance(v, bytes) else v) for k, v in hdrs]
                except Exception:
                    # HPACK dynamic table ไม่ sync (attach กลางคัน / frame ตก) →
                    # ลอง fresh decoder (literal headers ยังได้) + flag error ไว้
                    try:
                        fresh = HpackDecoder()
                        fresh.max_header_list_size = 1 << 20
                        hdrs = fresh.decode(bytes(frag))
                        hdrs = [(k.decode() if isinstance(k, bytes) else k,
                                 v.decode() if isinstance(v, bytes) else v) for k, v in hdrs]
                    except Exception as e:
                        hdrs = [("__hpack_error__", str(e))]
                self._hdr_frag.pop(sid, None)
                events.append(("headers", sid, hdrs, bool(flags & FLAG_END_STREAM)))
        elif name == "DATA":
            block = payload
            if flags & FLAG_PADDED:
                pad = block[0]
                block = block[1:len(block) - pad]
            events.append(("data", sid, block, bool(flags & FLAG_END_STREAM)))
        elif name == "RST_STREAM":
            events.append(("rst", sid, payload, True))
        # SETTINGS/PING/WINDOW_UPDATE/GOAWAY — ข้าม (ไม่กระทบ decode payload)


def peek_kind(data: bytes) -> str:
    """เดาชนิด stream แรก: 'http2' / 'http1' / 'other'."""
    if data[:len(H2_PREFACE)] == H2_PREFACE:
        return "http2"
    head = data[:16]
    if head[:4] in (b"GET ", b"POST", b"PUT ", b"HEAD", b"DELE", b"OPTI") or head[:5] == b"HTTP/":
        return "http1"
    if len(data) >= 9 and data[3] in FRAME_TYPES and (data[5] & 0x80) == 0:
        return "http2"
    return "other"
