"""The encrypted "brain" — gate crypto the program cannot function without.

brain = { aes_key, hmac_seed, cmd_schema, endpoint_map, client_version, ... }
  packed    = canonical JSON bytes (sorted keys, compact)
  brain.enc = nonce(12) || AES-256-GCM(K_brain).encrypt(nonce, packed, None)

Packing is a PC-side contract (release.py encrypts, loader decrypts) — both use
this module, so they stay in lock-step. K_brain lives only on the server and is
released wrapped per session; without it brain.enc stays ciphertext.
"""
from __future__ import annotations

import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def pack(brain: dict) -> bytes:
    return json.dumps(brain, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def unpack(packed: bytes) -> dict:
    return json.loads(packed.decode("utf-8"))


def encrypt_brain(k_brain: bytes, brain: dict) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(k_brain).encrypt(nonce, pack(brain), None)


def decrypt_brain(k_brain: bytes, blob: bytes) -> dict:
    nonce, ct = blob[:12], blob[12:]
    return unpack(AESGCM(k_brain).decrypt(nonce, ct, None))
