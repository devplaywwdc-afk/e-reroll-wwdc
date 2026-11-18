# genlink_web — KONAMI ID account-link generator

Self-contained Python module for generating the short-lived **my.konami.net**
account-link URL for an eFootball account, by calling the game's gate API
directly (no emulator, no game UI).

## Files

| File | Purpose |
|------|---------|
| `gate_crypto.py` | Gate protocol crypto: AES-256-CBC body + HMAC sign. Do not edit. |
| `client.py` | `GateClient` — builds/signs/sends a gate command, decrypts the reply. Retries soft throttles. |
| `genlink.py` | The service entrypoint: `generate_link()`. This is the only function the web backend calls. |
| `example.py` | Minimal usage / smoke test. |
| `requirements.txt` | `requests`, `msgpack`, `pycryptodome`. |

## Install

```bash
pip install -r requirements.txt
```

## Use

```python
from genlink import generate_link

r = generate_link(
    user_id=2055734095,
    auth_code="f807b34d81cfaf6f3485b955429b8421ed248e74",
    device_identifier="ae2efa4abb162b4f353a2cbb3b06cd84345726ebcd129c1b92000e79f638ef38",  # optional
)
```

### Input

- `user_id` (int) — the account's numeric id.
- `auth_code` (str) — the account's auth code.
- `device_identifier` (str, optional) — 64-hex device id the account was created with. Falls back to a known-good default if omitted.
- `lang` (str, default `"th"`) — locale path inserted into the URL.

These three values are exactly what the reroll pipeline saves per account (the
`.txt`/`.dat` account record: `user_id`, `auth_code`, `device_identifier`).

### Output (always a dict, never raises on gate errors)

```jsonc
{
  "ok": true,                 // true only on NOERR + url present
  "url": "https://my.konami.net/th/account-link/...",
  "expires": 1763400000,      // unix ts the URL dies at (~20 min out)
  "expires_in": 1200,         // seconds left
  "link_status": "...",
  "result": "NOERR",
  "error": "..."              // present only when ok == false
}
```

On failure `ok` is `false` and `error` explains why — the web layer should
**refund the held token** and show a clean message.

## Notes for the web backend

- The URL is valid ~20 minutes. `expires`/`expires_in` drive the countdown.
- A new URL at no extra token cost is allowed, but the gate only issues a fresh
  one **after the previous URL expires**. Enforce that 20-min cooldown in your
  own layer (store `expires` per link request).
- One `generate_link()` call = one login + one gate command, ~1–3s. Run it in a
  worker/async task, not inline on the request thread, for throughput.
- Gate host is `pes22-game.cs.konami.net` (hardcoded in `gate_crypto.py`). The
  server does **not** bind the app `hash` to a device, so the default device id
  works for any account; prefer the account's real `device_identifier` when you
  have it.
