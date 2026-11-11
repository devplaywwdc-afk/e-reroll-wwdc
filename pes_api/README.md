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

## GUI (web dashboard, like the rental tools)
Double-click **`pes_api/gui/START_REROLL_GUI.bat`** → opens a local web dashboard (auto-picks a
free port, like the Cookie Run launcher). One grid pane = one worker's live log (green `[+]` =
created, red `[!]` = throttled/500), with success/fail/rate/per-day stats on top.

- **Mode switch:**
  - `Direct` — spray from this machine's IP, keep only the accounts that succeed, discard the rest
    (same idea as the rental tools flooding until the backend 500s).
  - `Proxy rotation` — paste a proxy list (one per line); each worker uses one and auto-rotates to
    the next when its IP gets throttled. Use **HTTP(S) or SOCKS5**, prefer **rotating residential /
    mobile** proxies (datacenter gets flagged); format `http://user:pass@host:port` or
    `socks5://host:port` (SOCKS needs `pip install requests[socks]`).
- **Workers** default 15 (matches the 15-window rental layout). **เป้า** 0 = unlimited.
- Click the .bat again to open another independent dashboard on the next port.

## Reroll — fast / parallel (for scale)
```bash
python pes_api/reroll_fast.py 500 --workers 8        # parallel, adaptive pacing
python pes_api/reroll_fast.py 500 --no-setup         # create-only (~2x faster, no MyClub)
python pes_api/reroll_fast.py 500 --proxies proxies.txt   # one proxy/IP per line
```
The server **soft-throttles CREATE_USER per source IP** (`ERR_TEMP_UNAVAILABLE`, retryable, not a
ban) and the throttle tightens the more you create from one IP. `reroll_fast` runs a worker pool
with an **AIMD governor** (backs off on throttle, speeds up on success) so one IP stays at its safe
max, and `client.py` retries soft throttles with jittered backoff.

**Throughput (measured):** ~1 s/acct create, ~2 s/acct full-ready on a single unthrottled IP
(≈40–90k/day). The server caps a single IP well below 1M/day. **1M/day ≈ 11.6 acct/s needs ~20–50
source IPs** — a VPS fleet (one process per box, no proxy needed per box) or a proxy list via
`--proxies`. The code is IP-agnostic; scale horizontally.

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
