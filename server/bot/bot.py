"""Discord bot for the reroll license system.

Thin front-end over the license server's admin API (localhost, X-Admin-Token).
Customer commands: /redeem, /mykeys. Admin commands (gated by ADMIN_ROLE_ID or
guild owner): /genkey, /keyinfo, /ban, /unban, /bankey.

Config (env, loaded from the bot's .env by the systemd unit):
  DISCORD_BOT_TOKEN   bot token
  REROLL_API          e.g. https://127.0.0.1:8787  (self-signed -> TLS not verified)
  ADMIN_TOKEN         same X-Admin-Token as the license server
  GUILD_ID            guild to sync slash commands to (instant); 0 = global (~1h)
  ADMIN_ROLE_ID       role allowed to run admin commands; 0 = owner only
  DEFAULT_DAYS        default rental length for /genkey (default 30)
  DEFAULT_ENTITLEMENTS default entitlements (default "reroll,gacha,daily")
"""
from __future__ import annotations

import os
import ssl
import secrets
import time
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands

API = os.environ.get("REROLL_API", "https://127.0.0.1:8787").rstrip("/")
ADMIN_TOKEN = os.environ["ADMIN_TOKEN"]
GUILD_ID = int(os.environ.get("GUILD_ID", "0") or 0)
ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", "0") or 0)
DEFAULT_DAYS = int(os.environ.get("DEFAULT_DAYS", "30") or 30)
DEFAULT_ENT = os.environ.get("DEFAULT_ENTITLEMENTS", "reroll,gacha,daily")

# localhost + self-signed cert -> skip verification (traffic never leaves the box)
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def _gen_key() -> str:
    return "RRL-" + secrets.token_hex(8).upper()


def _fmt_exp(ts) -> str:
    if not ts:
        return "ไม่มีวันหมดอายุ"
    left = ts - int(time.time())
    d = max(0, left // 86400)
    when = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    return f"{when} (เหลือ ~{d} วัน)" if left > 0 else f"{when} (หมดอายุแล้ว)"


def _mask(key: str) -> str:
    return key[:8] + "…" + key[-4:] if len(key) > 14 else key


def _is_admin(inter: discord.Interaction) -> bool:
    if inter.guild is None:
        return False
    if inter.guild.owner_id == inter.user.id:
        return True
    if ADMIN_ROLE_ID and isinstance(inter.user, discord.Member):
        return any(r.id == ADMIN_ROLE_ID for r in inter.user.roles)
    return False


class API_Error(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


async def _api(method: str, path: str, json=None) -> dict:
    url = API + path
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.request(method, url, json=json, headers=headers, ssl=_SSL) as r:
            body = await r.json(content_type=None)
            if r.status >= 400:
                detail = body.get("detail") if isinstance(body, dict) else str(body)
                raise API_Error(r.status, detail or f"HTTP {r.status}")
            return body


# ------------------------------- commands -------------------------------
@tree.command(name="redeem", description="ผูก License Key เข้ากับ Discord ของคุณ")
@app_commands.describe(key="License Key ที่ซื้อมา")
async def redeem(inter: discord.Interaction, key: str):
    await inter.response.defer(ephemeral=True)
    key = key.strip()
    try:
        res = await _api("POST", "/admin/bind",
                         json={"key": key, "discord_id": str(inter.user.id)})
    except API_Error as e:
        msg = {404: "ไม่พบ Key นี้ — เช็กตัวอักษรอีกที",
               409: "Key นี้ถูกผูกกับ Discord คนอื่นไปแล้ว"}.get(
                   e.status, f"ผิดพลาด: {e.detail}")
        await inter.followup.send(f"❌ {msg}", ephemeral=True)
        return
    if res.get("discord_banned"):
        await inter.followup.send("⚠️ บัญชี Discord ของคุณถูกแบน ติดต่อแอดมิน", ephemeral=True)
        return
    head = "✅ ผูก Key เรียบร้อย" if not res.get("already") else "ℹ️ Key นี้ผูกกับคุณอยู่แล้ว"
    await inter.followup.send(
        f"{head}\n**Key:** `{_mask(key)}`\n**สิทธิ์:** {res.get('entitlements')}\n"
        f"**หมดอายุ:** {_fmt_exp(res.get('expires_at'))}\n"
        f"เปิดโปรแกรมแล้ว Login with Discord ด้วยบัญชีนี้ได้เลย", ephemeral=True)


@tree.command(name="mykeys", description="ดู Key ทั้งหมดที่ผูกกับ Discord ของคุณ")
async def mykeys(inter: discord.Interaction):
    await inter.response.defer(ephemeral=True)
    res = await _api("GET", f"/admin/keysbydiscord/{inter.user.id}")
    keys = res.get("keys") or []
    if not keys:
        await inter.followup.send("ยังไม่มี Key ผูกกับบัญชีนี้ — ใช้ /redeem <key>", ephemeral=True)
        return
    banned = " ⛔ (บัญชีถูกแบน)" if res.get("banned") else ""
    lines = []
    for k in keys:
        st = "⛔ แบน" if k.get("ban_at") else "✅"
        lines.append(f"{st} `{_mask(k['key'])}` — {k['entitlements']} — "
                     f"{_fmt_exp(k.get('expires_at'))} — seat {k['seats_used']}/{k['seats']}")
    await inter.followup.send(f"**Key ของคุณ{banned}:**\n" + "\n".join(lines), ephemeral=True)


@tree.command(name="genkey", description="[แอดมิน] สร้าง Key ใหม่ผูกให้ลูกค้า")
@app_commands.describe(user="ลูกค้า (Discord)", days="จำนวนวัน (0=ไม่หมดอายุ)",
                       seats="จำนวนเครื่อง", entitlements="สิทธิ์ เช่น reroll,gacha,daily")
async def genkey(inter: discord.Interaction, user: discord.Member,
                 days: int = 0, seats: int = 1, entitlements: str = ""):
    await inter.response.defer(ephemeral=True)
    if not _is_admin(inter):
        await inter.followup.send("❌ เฉพาะแอดมิน", ephemeral=True)
        return
    days = days or DEFAULT_DAYS
    ent = (entitlements or DEFAULT_ENT).strip()
    key = _gen_key()
    expires_at = None if days <= 0 else int(time.time()) + days * 86400
    await _api("POST", "/admin/key", json={
        "key": key, "seats": max(1, seats), "entitlements": ent,
        "tier": "rental", "expires_at": expires_at, "discord_id": str(user.id)})
    dm_ok = True
    try:
        await user.send(
            f"🎟️ License Key ของคุณ\n**Key:** `{key}`\n**สิทธิ์:** {ent}\n"
            f"**หมดอายุ:** {_fmt_exp(expires_at)}\n"
            f"เปิดโปรแกรม → Login with Discord ด้วยบัญชีนี้ → วาง Key นี้")
    except discord.Forbidden:
        dm_ok = False
    note = "ส่ง DM ให้ลูกค้าแล้ว" if dm_ok else "⚠️ DM ลูกค้าไม่ได้ (ปิด DM) — ส่ง Key ให้เอง"
    await inter.followup.send(
        f"✅ สร้าง Key ให้ {user.mention}\n**Key:** `{key}`\n**สิทธิ์:** {ent}\n"
        f"**หมดอายุ:** {_fmt_exp(expires_at)} — seats {seats}\n{note}", ephemeral=True)


@tree.command(name="keyinfo", description="[แอดมิน] ดูข้อมูล Key")
@app_commands.describe(key="License Key")
async def keyinfo(inter: discord.Interaction, key: str):
    await inter.response.defer(ephemeral=True)
    if not _is_admin(inter):
        await inter.followup.send("❌ เฉพาะแอดมิน", ephemeral=True)
        return
    try:
        k = await _api("GET", f"/admin/keyinfo/{key.strip()}")
    except API_Error as e:
        await inter.followup.send(
            "❌ ไม่พบ Key" if e.status == 404 else f"❌ {e.detail}", ephemeral=True)
        return
    owner = f"<@{k['discord_id']}>" if k.get("discord_id") else "ยังไม่ผูก"
    st = "⛔ แบน" if k.get("ban_at") else "✅ ปกติ"
    await inter.followup.send(
        f"**`{k['key']}`**\nเจ้าของ: {owner}\nสถานะ: {st}\nสิทธิ์: {k['entitlements']}\n"
        f"หมดอายุ: {_fmt_exp(k.get('expires_at'))}\nseat: {k['seats_used']}/{k['seats']} — tier {k['tier']}",
        ephemeral=True)


@tree.command(name="ban", description="[แอดมิน] แบนบัญชี Discord (ลามทุก Key ของเขา)")
@app_commands.describe(user="บัญชีที่จะแบน")
async def ban(inter: discord.Interaction, user: discord.Member):
    await inter.response.defer(ephemeral=True)
    if not _is_admin(inter):
        await inter.followup.send("❌ เฉพาะแอดมิน", ephemeral=True)
        return
    res = await _api("POST", "/admin/ban", json={"discord_id": str(user.id)})
    await inter.followup.send(
        f"⛔ แบน {user.mention} แล้ว — Key ที่ผูกโดนด้วย {res.get('affected_keys', 0)} ตัว",
        ephemeral=True)


@tree.command(name="unban", description="[แอดมิน] ปลดแบนบัญชี Discord")
@app_commands.describe(user="บัญชีที่จะปลดแบน")
async def unban(inter: discord.Interaction, user: discord.Member):
    await inter.response.defer(ephemeral=True)
    if not _is_admin(inter):
        await inter.followup.send("❌ เฉพาะแอดมิน", ephemeral=True)
        return
    res = await _api("POST", "/admin/ban", json={"discord_id": str(user.id), "unban": True})
    await inter.followup.send(
        f"✅ ปลดแบน {user.mention} — คืนสิทธิ์ {res.get('affected_keys', 0)} Key", ephemeral=True)


@tree.command(name="bankey", description="[แอดมิน] แบนเฉพาะ Key (ไม่แตะบัญชี)")
@app_commands.describe(key="License Key")
async def bankey(inter: discord.Interaction, key: str):
    await inter.response.defer(ephemeral=True)
    if not _is_admin(inter):
        await inter.followup.send("❌ เฉพาะแอดมิน", ephemeral=True)
        return
    res = await _api("POST", "/admin/ban", json={"key": key.strip()})
    await inter.followup.send(
        f"⛔ แบน Key `{_mask(key.strip())}` — {res.get('affected_keys', 0)} ตัว", ephemeral=True)


@client.event
async def on_ready():
    if GUILD_ID:
        g = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=g)
        await tree.sync(guild=g)
    else:
        await tree.sync()
    print(f"[bot] logged in as {client.user} (guild={GUILD_ID or 'global'})", flush=True)


if __name__ == "__main__":
    client.run(os.environ["DISCORD_BOT_TOKEN"])
