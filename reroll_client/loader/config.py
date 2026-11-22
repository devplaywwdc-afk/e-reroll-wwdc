"""Embedded client constants — the only trust anchors baked into the exe.

In a production build these live inside the native `.pyd` (L5), not as loose
Python strings. Kept here as plain module constants for phase-1 bring-up.

If the server TLS cert or grant key is ever regenerated, update these and
rebuild every exe (TLS_SPKI_PIN / ED25519_PUB change).
"""
from __future__ import annotations

# Direct TLS to the raw IP — bypasses Cloudflare/nginx (self-signed, SPKI-pinned).
SERVER_HOST = "140.99.254.10"
SERVER_PORT = 8787

# sha256(SubjectPublicKeyInfo DER) base64 of the server's self-signed cert.
TLS_SPKI_PIN = "d7exisGbEqYPmymZWu8yyjf9Pt8xWPevd80+05McBnY="

# Ed25519 grant verification key (raw 32-byte public key, hex). Server holds the
# private half; a fake server cannot forge a grant.
ED25519_PUB = "f0b8e3e3b1328f588c3aa8f024556343b2013edf290975c0fd23a3d1f3175641"

# X25519 server public (server also returns it in /session; kept for reference).
X25519_PUB = "a7cc3b2dd07ee0abc5023f62df2cbf616757a3add9ed2c6bb4e8a759f6015573"

# brain magic (packing format tag).
BRAIN_MAGIC = b"RRB1"

# Discord OAuth2 (phase 2). PUBLIC client id only — the client secret lives on
# the server. Empty = this build runs key+HWID only (phase 1). Fill per build.
DISCORD_CLIENT_ID = "1570347324885180516"

# App version + the public GitHub repo used for the (soft) auto-update check.
# Bump APP_VERSION each release and publish a GitHub Release tagged v<version>
# with the exe attached. This is a NOTICE-only check (not forced).
APP_VERSION = "1.0.21"
GITHUB_REPO = "devplaywwdc-afk/e-reroll-wwdc"

# Where a user with no active rental is sent to buy/rent (Discord-first bounce).
SHOP_URL = "https://crgwwdc.shop/efootball"
