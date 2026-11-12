"""Client-side crypto — mirrors the server's grants.py / brainwrap.py exactly.

- verify_grant     : Ed25519 verify over the canonical grant string (L2)
- derive_kwrap     : HKDF-SHA256(shared, info="brain-wrap"||hwid||M)  (L3)
- unwrap_k_brain   : X25519-ECDH + AES-256-GCM open of the wrapped key
- (brain enc/dec lives in brain.py)

The decrypt path has NO branch to patch: a tampered exe measures a different M,
derives the wrong K_wrap, and AES-GCM open() raises -> k_brain never recovered.
"""
from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_INFO_PREFIX = b"brain-wrap"


class GrantError(Exception):
    """Grant signature invalid, expired, or bound to a different device/build."""


def verify_grant(ed25519_pub_hex: str, grant: str, sig_b64: str) -> dict:
    """Verify the server's grant string and return its parsed fields.

    `grant` is the exact string the server returned (payload.decode()); verifying
    over it is canonical by construction. Raises GrantError on any failure.
    """
    pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(ed25519_pub_hex))
    try:
        pub.verify(base64.b64decode(sig_b64), grant.encode("utf-8"))
    except (InvalidSignature, ValueError) as e:
        raise GrantError(f"grant signature invalid: {e}") from e

    parts = grant.split("|")
    if len(parts) != 8 or parts[0] != "v1":
        raise GrantError("malformed grant payload")
    return {
        "key": parts[1], "hwid": parts[2], "sid": parts[3], "build_id": parts[4],
        "iat": int(parts[5]), "exp": int(parts[6]), "nonce": parts[7],
    }


def derive_kwrap(shared: bytes, hwid: str, m: bytes) -> bytes:
    """info = b"brain-wrap" + hwid.utf8 + M(raw 32 bytes), no separator."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None,
        info=_INFO_PREFIX + hwid.encode("utf-8") + m,
    ).derive(shared)


def ecdh_shared(eph_sk: X25519PrivateKey, server_x25519_pub_hex: str) -> bytes:
    return eph_sk.exchange(
        X25519PublicKey.from_public_bytes(bytes.fromhex(server_x25519_pub_hex)))


def unwrap_k_brain(k_wrap: bytes, wrapped_b64: str) -> bytes:
    """blob = base64(nonce[12] || ct); AAD = None. Raises on tamper/wrong-M."""
    blob = base64.b64decode(wrapped_b64)
    nonce, ct = blob[:12], blob[12:]
    return AESGCM(k_wrap).decrypt(nonce, ct, None)
