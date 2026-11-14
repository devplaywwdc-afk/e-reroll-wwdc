
    def keys_for_discord(self, discord_id: str) -> list[sqlite3.Row]:
        with self._c() as c:
            return c.execute(
                "SELECT * FROM reroll_keys WHERE discord_id=? ORDER BY created_at DESC",
                (discord_id,)).fetchall()
