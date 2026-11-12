"""Local crypto round-trip — proves the client primitives match the server's
exact formulas WITHOUT needing the live server. Simulates the server side with
the same cryptography calls the VPS uses, then runs the client path.

Run:  python -I selftest.py
"""
from __future__ import annotations

import base64
import os
import secrets
import sys
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from loader import brain as brain_mod
from loader import crypto_core
from loader.canonical import grant_payload


def _raw_pub_hex(sk) -> str:
    return sk.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def main() -> int:
    hwid = "deadbeefdeadbeefdeadbeefdeadbeef"
    m = os.urandom(32)                       # client-measured M (raw 32 bytes)
    k_brain = os.urandom(32)
    build_id = "reroll-selftest"
    key = "RRL-TEST-0001"

    # --- server key material ---
    ed_sk = Ed25519PrivateKey.generate()
    ed_pub_hex = _raw_pub_hex(ed_sk)
    x_sk = X25519PrivateKey.generate()
    x_pub_hex = _raw_pub_hex(x_sk)

    # --- client generates ephemeral, measures M (here: m), sends eph_pub ---
    eph = X25519PrivateKey.generate()

    # === SERVER side (mirror of api.py /session) ===
    now = int(time.time())
    sid = secrets.token_hex(16)
    nonce = secrets.token_hex(16)
    gexp = now + 300
    payload = grant_payload(key=key, hwid=hwid, sid=sid, build_id=build_id,
                            iat=now, exp=gexp, nonce=nonce)
    sig = base64.b64encode(ed_sk.sign(payload)).decode("ascii")

    shared_s = x_sk.exchange(eph.public_key())
    k_wrap_s = crypto_core.derive_kwrap(shared_s, hwid, m)
    wnonce = os.urandom(12)
    wrapped = base64.b64encode(
        wnonce + AESGCM(k_wrap_s).encrypt(wnonce, k_brain, None)).decode("ascii")
    brain_enc = brain_mod.encrypt_brain(
        k_brain, {"aes_key": "00" * 16, "hmac_seed": "ab" * 16,
                  "client_version": "11.1.1", "endpoint_map": {}, "cmd_schema": {}})

    resp = {"sid": sid, "iat": now, "grant_exp": gexp, "lease_exp": now + 300,
            "nonce": nonce, "grant": payload.decode(), "sig": sig,
            "wrapped": wrapped, "server_x25519_pub": x_pub_hex, "heartbeat": 60}

    # === CLIENT side (mirror of session.open_session) ===
    grant = crypto_core.verify_grant(ed_pub_hex, resp["grant"], resp["sig"])
    assert grant["hwid"] == hwid and grant["build_id"] == build_id
    assert grant["exp"] > now

    shared_c = crypto_core.ecdh_shared(eph, resp["server_x25519_pub"])
    assert shared_c == shared_s, "ECDH mismatch"
    k_wrap_c = crypto_core.derive_kwrap(shared_c, hwid, m)
    assert k_wrap_c == k_wrap_s, "K_wrap mismatch"
    got_k_brain = crypto_core.unwrap_k_brain(k_wrap_c, resp["wrapped"])
    assert got_k_brain == k_brain, "k_brain unwrap mismatch"
    brain = brain_mod.decrypt_brain(got_k_brain, brain_enc)
    assert brain["client_version"] == "11.1.1"

    # === tamper: wrong M must FAIL to unwrap (the gate, no branch to patch) ===
    bad_m = os.urandom(32)
    k_wrap_bad = crypto_core.derive_kwrap(shared_c, hwid, bad_m)
    try:
        crypto_core.unwrap_k_brain(k_wrap_bad, resp["wrapped"])
    except Exception:
        pass
    else:
        print("FAIL: wrong-M unwrap unexpectedly succeeded", file=sys.stderr)
        return 1

    # === tamper: forged grant must FAIL verify ===
    forged = base64.b64encode(b"\x00" * 64).decode()
    try:
        crypto_core.verify_grant(ed_pub_hex, resp["grant"], forged)
    except crypto_core.GrantError:
        pass
    else:
        print("FAIL: forged grant unexpectedly verified", file=sys.stderr)
        return 1

    print("OK: grant verify, ECDH, HKDF, k_brain unwrap, brain decrypt, "
          "wrong-M reject, forged-grant reject — all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
