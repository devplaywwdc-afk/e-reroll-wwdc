

# ---------------- admin: discord bind / key info (bot support) ----------------
class AdminBindReq(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    discord_id: str = Field(min_length=1, max_length=64)


def _key_public(row) -> dict:
    return {
        "key": row["key"],
        "discord_id": row["discord_id"],
        "tier": row["tier"],
        "seats": row["seats"],
        "seats_used": store.device_count(row["key"]),
        "entitlements": row["entitlements"],
        "expires_at": row["expires_at"],
        "ban_at": row["ban_at"],
        "created_at": row["created_at"],
    }


@app.post("/admin/bind")
def admin_bind(req: AdminBindReq, x_admin_token: str | None = Header(default=None)):
    """Bind a key to a discord_id (customer /redeem). Idempotent; rejects a key
    already bound to a different account."""
    _require_admin(x_admin_token)
    row = store.get_key(req.key)
    if not row:
        raise HTTPException(404, "key not found")
    bound = row["discord_id"]
    if bound and bound != req.discord_id:
        raise HTTPException(409, "key already bound to another account")
    already = bound == req.discord_id
    if not already:
        store.set_key_discord(req.key, req.discord_id)
    return {
        "ok": True,
        "already": already,
        "discord_banned": store.is_discord_banned(req.discord_id),
        **_key_public(store.get_key(req.key)),
    }


@app.get("/admin/keyinfo/{key}")
def admin_keyinfo(key: str, x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    row = store.get_key(key)
    if not row:
        raise HTTPException(404, "key not found")
    return _key_public(row)


@app.get("/admin/keysbydiscord/{discord_id}")
def admin_keys_by_discord(discord_id: str, x_admin_token: str | None = Header(default=None)):
    _require_admin(x_admin_token)
    rows = store.keys_for_discord(discord_id)
    return {
        "discord_id": discord_id,
        "banned": store.is_discord_banned(discord_id),
        "keys": [_key_public(r) for r in rows],
    }
