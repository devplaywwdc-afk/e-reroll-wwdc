# reroll license — server mirror

The live license server runs on the VPS at `/opt/reroll-license` (not fully in this
repo). This folder mirrors the **Discord bot** and documents the **server API
additions** so they're recoverable / reviewable. Secrets live only in the VPS
`.env` files, never here.

## Discord bot (`bot/`)

Thin front-end over the license server admin API. Deployed on the VPS:

- `bot/bot.py` → `/opt/reroll-license/bot/bot.py` (runs in the shared venv, `discord.py>=2.3`)
- `bot/reroll-bot.service` → `/etc/systemd/system/reroll-bot.service`
- `bot/.env.example` → copy to `/opt/reroll-license/bot/.env` (chmod 600), fill in secrets

Bot = **eFootball Reroll**, admin commands gated **owner-only** (`ADMIN_ROLE_ID=0`).

| command | who | action |
|---|---|---|
| `/redeem <key>` | customer | bind key to own Discord |
| `/mykeys` | customer | list own keys + expiry |
| `/genkey @user [days] [seats] [entitlements]` | admin | create key pre-bound to user, DM it |
| `/keyinfo <key>` | admin | key details |
| `/ban @user` / `/unban @user` | admin | ban/unban account (cascades to all its keys) |
| `/bankey <key>` | admin | ban a single key |

Deploy / update:
```
scp bot/bot.py root@<vps>:/opt/reroll-license/bot/bot.py
systemctl restart reroll-bot
journalctl -u reroll-bot -n 20 --no-pager
```

## Server API additions (`patches/`)

Appended to the live server (for the bot). X-Admin-Token gated.

- `patches/api_additions.py` → appended to `security/server/api.py`
  - `POST /admin/bind` `{key, discord_id}` — bind (idempotent; 409 if bound elsewhere, 404 if unknown)
  - `GET /admin/keyinfo/{key}` — key details + seats used
  - `GET /admin/keysbydiscord/{discord_id}` — all keys for a Discord account
- `patches/store_additions.py` → appended to `security/db/store.py` (`keys_for_discord`)

`reroll_keys(key, discord_id, tier, seats, entitlements, ban_at, expires_at, created_at)`;
bans via `reroll_bans(discord_id)` + `ban_at` cascade. Binding also happens
automatically on `/session` first use.
