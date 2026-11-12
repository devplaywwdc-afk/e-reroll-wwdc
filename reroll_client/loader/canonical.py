"""THE single canonical serializer for the Ed25519 grant payload.

BYTE-EXACT MIRROR of the server's `crypto/canonical.py`
(server SHA256 3db0241c6f7fa4ef1d5d707a5f956aea49c153e80cff7605fbc4b99ca169a535).

Grant payload v1:  v1|key|hwid|sid|build|iat|exp|nonce
Both server and client build the bytes here and nowhere else. Any divergence =
signature verify fails. Fields may not contain the '|' delimiter.
"""
from __future__ import annotations

_FIELDS = ("key", "hwid", "sid", "build_id", "nonce")


def _check(name: str, value: str) -> str:
    if "|" in value:
        raise ValueError(f"field {name!r} must not contain '|'")
    return value


def grant_payload(*, key: str, hwid: str, sid: str, build_id: str,
                  iat: int, exp: int, nonce: str) -> bytes:
    for n in _FIELDS:
        _check(n, locals()[n])
    return f"v1|{key}|{hwid}|{sid}|{build_id}|{int(iat)}|{int(exp)}|{nonce}".encode("utf-8")
