# eFootball Reroll — PC Client (thin loader)

Phase-1 client for the live license server at `https://140.99.254.10:8787`
(direct TLS, SPKI-pinned, bypasses Cloudflare). Auth = **license key + HWID**.
No Discord, mode A (brain-in-RAM). Mirrors the server crypto byte-exact.

## Layout
```
reroll_client/
  loader/
    config.py       embedded constants (server URL, SPKI pin, Ed25519/X25519 pub)
    canonical.py    grant payload serializer — byte-exact mirror of server
    crypto_core.py  verify_grant / derive_kwrap / ECDH / unwrap_k_brain
    brain.py        pack + AES-GCM encrypt/decrypt of the brain
    integrity.py    M = SHA256("RR1" || exe bytes || runtime probe)
    hwid.py         stable per-machine id (MachineGuid + node)
    tls_pin.py      SPKI-pinned HTTPS
    session.py      /session handshake + heartbeat (lease)
    loader.py       thin loader: session -> fetch engine module -> exec in RAM
  run.py            entry; module_id from exe name / --module
  release.py        build brain.enc + build.blob + exe; register on server
  selftest.py       local crypto round-trip (no server needed)
```

## Quick checks
```bash
pip install -r requirements.txt
python selftest.py          # proves client crypto == server formulas
```

## Live handshake (dry-run, no exe build)
Needs a build + key registered on the server first (server side holds ADMIN_TOKEN):
```bash
python release.py --module reroll --build reroll-dev-1    # writes dist/brain.enc, prints k_brain
# (server registers build with m_expected = dev M + that k_brain, issues a key)
python run.py --dry-run --module reroll --build reroll-dev-1 --key RRL-XXXX --brain dist/brain.enc
```

## Shipping (real build)
```bash
python release.py --module reroll --build reroll-2026.11.1 --brain-src brain_src.json --exe --register
# -> dist/eFootballReroll.exe (+ bundled brain.enc, build_id.txt)
# -> registers m_expected (measured on the FROZEN exe) + k_brain on the server
```
Repeat per module (`gacha`, `daily`). Deploy order: upload + sha-verify, then flip `allowed=1`.

## Hard rules (from the hard-won pitfalls)
- `M` must be **identical** between the exe `release.py` measures and what the
  running exe computes — measure the **frozen exe**, never `run.py`. A frozen-only
  probe false-positive = mass lockout. Phase-1 keeps the runtime probe neutral.
- One canonical serializer (`canonical.py`) on both sides — byte-exact or signatures fail.
- Never embed `ADMIN_TOKEN` in an exe. Never change `WM_SECRET` (server side).
- Rotating the grant key ⇒ rebuild every exe with the new `ED25519_PUB`.
- Ban key/hwid, never the account.

## Server contract
See `C:\Users\jaras\Downloads\REROLL_PC_CLIENT_HANDOFF.md` and
`docs/REROLL_LICENSE_ARCHITECTURE.md`. The grant payload, HKDF info, and wrapped
format are mirrored from the server's `crypto/{canonical,grants,brainwrap}.py`.
