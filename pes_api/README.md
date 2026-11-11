# pes_api — eFootball 11.1.1 pure-API toolkit

Reroll guest accounts and farm daily-login rewards **without opening the game** — direct
gate API, fully headless, built to run forever on a VPS.

Protocol fully reversed & verified; see `../docs/GATE_PROTOCOL_11.1.1.md`.

## Install
```bash
pip install requests msgpack pycryptodome
```

## Reroll — create accounts
```bash
python pes_api/reroll.py 20        # create 20 guest accounts
```
Each account is saved to `pes_api/accounts/<user_code>.txt` as:
```json
{"auth_code":"<40hex>","user_code":"ASxx...","user_id":1234567890,"version":"2.0.0"}
```
(same format as the sample result IDs.)

Every created account also auto-enters MyClub (picks the starter squad via
`CMD_SET_MYCLUB_ENTRY_INFO`) so it is **lobby-ready immediately — no tutorial to play**.
eFootball 11.1.1 has no forced playable tutorial; MyClub entry is the only required step.
The starter squad template is `entry_barcelona.json` (FC Barcelona, team 108); per-account
player/coach serials are regenerated as `user_id*1e9 + offset`.

## Daily-login bot — claim rewards every day
```bash
python pes_api/daily.py run         # one pass now: login every saved account + claim mailbox
python pes_api/daily.py schedule    # run forever, fire at 09:00 Asia/Bangkok daily (for VPS)
```
Per account it does: `CMD_LOGIN` → `CMD_GET_MAILBOX_INFO` → `CMD_RECV_PRESENT` (claims all),
logging to `pes_api/daily.log`. The daily login bonus is registered by the login itself and
delivered to the mailbox, which the bot then claims.

Run on a VPS with e.g. `nohup python pes_api/daily.py schedule &` or a systemd service.

## Verify the crypto without touching the server
```bash
python pes_api/selftest.py          # decrypt/sign round-trip vs captured traffic
```

## Files
| file | role |
|------|------|
| `gate_crypto.py` | AES-256-CBC + zlib + msgpack + HMAC-SHA256 sign (the core) |
| `client.py` | `GateClient` — build/sign/POST a gate command, decrypt the response |
| `flows.py` | `create_user`, `login`, `get_mailbox`, `recv_present`, … |
| `accounts.py` | save/load the `.dat` account files |
| `reroll.py` | reroll loop |
| `daily.py` | daily-login bot + 09:00 scheduler |
| `templates.json` | real captured request templates (login, recv_present, …) |

## Notes
- **Each account gets a random device fingerprint** (random `device_identifier` + varied
  model/gpu/soc/os from `rand_device()`), so accounts spread across many "devices". The server
  does not bind the app `hash` to the device, so one reused valid hash works for all — no secret
  to reproduce. Each account's `device_identifier` is saved in its `.dat` and reused at login.
- No cert pinning; `client_version` field is `6.1.1`.
