"""
eFootball 11.1.1 pure-API gate client.
Builds, signs, sends a gate command and decrypts the response.
"""
import os
import sys
import random

import requests

sys.path.insert(0, os.path.dirname(__file__))
import gate_crypto as gc

DEFAULT_UA = "PES/1.0 (ANDROID 14; {user_code}; SM-A526B; {ts}; 6.1.1; ; 11.1.1; )"


class GateError(Exception):
    pass


class GateClient:
    def __init__(self, user_code="0", timeout=20, session=None, verbose=False):
        self.user_code = user_code
        self.timeout = timeout
        self.http = session or requests.Session()
        self.verbose = verbose

    # ---- low level ----
    def _headers(self, user_code, sign):
        ts = random.randint(1_700_000_000, 1_999_999_999)
        return {
            "Accept-Language": "jp",
            "User-Agent": DEFAULT_UA.format(user_code=user_code, ts=ts),
            "pes-custom-encrypt": "AES256",
            "Accept-Encoding": "gzip",
            "Content-Encoding": "gzip",
            "Cookie": f"sign={sign};",
            "Content-Type": "application/x-www-form-urlencoded",
            "Connection": "Keep-Alive",
        }

    def send(self, cmd: str, payload: dict, user_code: str | None = None) -> dict:
        """Send a gate command. `payload` is the full msgpack dict (must include msgid)."""
        body = gc.encrypt_body(payload)
        sign = gc.make_sign(body)
        url = gc.GATE_URL % (gc.GATE_HOST, cmd)
        headers = self._headers(user_code or self.user_code, sign)
        if self.verbose:
            print(f"--> {cmd} ({len(body)}B) user={user_code or self.user_code}")
        r = self.http.post(url, data=body, headers=headers, timeout=self.timeout)
        return self._parse(cmd, r)

    def send_raw(self, cmd: str, body: bytes, user_code: str | None = None) -> "requests.Response":
        """Replay a pre-built body verbatim (for validation)."""
        sign = gc.make_sign(body)
        url = gc.GATE_URL % (gc.GATE_HOST, cmd)
        headers = self._headers(user_code or self.user_code, sign)
        return self.http.post(url, data=body, headers=headers, timeout=self.timeout)

    def _parse(self, cmd, r: "requests.Response") -> dict:
        raw = r.content
        # response may be: our AES body, or plain JSON, or gzipped JSON
        # 1) try gate AES decrypt
        try:
            return gc.decrypt_body(raw)
        except Exception:
            pass
        # 2) plain / gzip json
        import gzip, json
        data = raw
        try:
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            return json.loads(data.decode("utf-8", "replace"))
        except Exception:
            return {"_status": r.status_code, "_raw_hex": raw[:64].hex(), "_len": len(raw)}


def new_rqid() -> int:
    return random.getrandbits(32)
