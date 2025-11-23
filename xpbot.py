from __future__ import annotations
import time
import asyncio
from maubot import Plugin, MessageEvent
from maubot.handlers import command, event
from mautrix.types import EventType

# ----- CONFIG -----
XP_PER_MESSAGE = 5       # XP awarded per accepted message
XP_PER_LEVEL = 100       # XP needed for +1 level (100 XP = level 1)
COOLDOWN = 5             # seconds between XP gains

# Hard fallback admin set
ADMINS = {"@admin:j5.chat"}

# Badge milestones (levels)
BADGES = {
    1: "🥉 Bronze",
    5: "🥈 Silver",
    10: "🥇 Gold",
    20: "🏆 Platinum",
}

# Accounts to ignore
IGNORE_USERS = {
    "@ticketbot:j5.chat",
    "@karma:j5.chat",
    "@mee6:j5.chat",
    "@mee6bot:j5.chat",
    "@antithread:j5.chat",
    "@poll:j5.chat",
    "@helpdesk:j5.chat",
    "@mee6xp:j5.chat",
}


class XpBot(Plugin):
    async def start(self) -> None:
        await super().start()

        # ensure DB table exists
        def _create_table():
            with self.database.begin() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS xp_users (
                        user_id TEXT PRIMARY KEY,
                        xp INTEGER NOT NULL,
                        last_msg REAL DEFAULT 0
                    )
                    """
                )

        await asyncio.get_event_loop().run_in_executor(None, _create_table)

    # ---------------- helpers ----------------
    def normalize_user(self, user: str) -> str:
        """Normalize to @user:j5.chat"""
        if not user:
            return ""
        u = user.strip()
        if u.startswith("@") and ":" in u:
            return u
        if u.startswith("@"):
            local = u[1:]
            return f"@{local}:j5.chat"
        if ":" not in u:
            return f"@{u}:j5.chat"
        return u

    async def get_user_row(self, mxid: str):
        def _get():
            with self.database.begin() as conn:
                cur = conn.execute(
                    "SELECT user_id, xp, last_msg FROM xp_users WHERE user_id = ?",
                    (mxid,),
                )
                return cur.fetchone()
        return await asyncio.get_event_loop().run_in_executor(None, _get)

    async def upsert_user(self, mxid: str, xp: int, last_msg: float = None):
        if last_msg is None:
            last_msg = 0.0

        def _upsert():
            with self.database.begin() as conn:
                conn.execute(
                    """
                    INSERT INTO xp_users (user_id, xp, last_msg)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        xp = excluded.xp,
                        last_msg = excluded.last_msg
                    """,
                    (mxid, xp, last_msg),
                )
        await asyncio.get_event_loop().run_in_executor(None, _upsert)

    def get_badge(self, level: int) -> str:
        for milestone, badge in sorted(BADGES.items(), reverse=True):
            if level >= milestone:
                return badge
        return ""

    def calc_level(self, xp: int) -> int:
        return xp // XP_PER_LEVEL

    async def is_admin_or_mod(self, evt: MessageEvent) -> bool:
        return evt.sender in ADMINS

    # ---------------- Event: give XP ----------------
    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent) -> None:
        if not evt.content or not getattr(evt.content, "body", None):
            return
        sender = evt.sender
        if sender == self.client.mxid or sender in IGNORE_USERS:
            return

        mxid = self.normalize_user(sender)
        row = await self.get_user_row(mxid)
        xp_now = row[1] if row else 0
        last_msg = row[2] if row else 0.0

        now = time.time()
        if now - (last_msg or 0) < COOLDOWN:
            return

        new_xp = xp_now + XP_PER_MESSAGE
        await self.upsert_user(mxid, new_xp, now)

        old_level = self.calc_level(xp_now)
        new_level = self.calc_level(new_xp)

        if new_level > old_level:
            badge = self.get_badge(new_level)
            await evt.reply(f"🎉 {mxid} leveled up to **Level {new_level}**! {badge}")

    # ---------------- Commands ----------------
    @command.new("level", help="Check your level and XP")
    async def cmd_level(self, evt: MessageEvent) -> None:
        mxid = self.normalize_user(evt.sender)
        row = await self.get_user_row(mxid)
        xp = row[1] if row else 0
        lvl = self.calc_level(xp)
        badge = self.get_badge(lvl)
        await evt.reply(f"⭐ {evt.sender}: Level {lvl} | XP: {xp} {badge}")

    @command.new("profile", help="Check your or another user's profile")
    @command.argument("user", required=False)
    async def cmd_profile(self, evt: MessageEvent, user: str = None) -> None:
        target = self.normalize_user(user) if user else self.normalize_user(evt.sender)
        row = await self.get_user_row(target)
        xp = row[1] if row else 0
        lvl = self.calc_level(xp)
        badge = self.get_badge(lvl)
        await evt.reply(f"👤 {target}: Level {lvl} | XP: {xp} {badge}")

    @command.new("setxp", help="(Admin only) Set XP for a user")
    @command.argument("user")
    @command.argument("xp")
    async def cmd_setxp(self, evt: MessageEvent, user: str, xp: str) -> None:
        if not await self.is_admin_or_mod(evt):
            await evt.reply("❌ You are not allowed to use this command.")
            return

        target = self.normalize_user(user)
        try:
            xp_val = int(xp)
        except ValueError:
            await evt.reply("❌ XP must be a number.")
            return

        row = await self.get_user_row(target)
        old_xp = row[1] if row else 0
        old_level = self.calc_level(old_xp)
        new_level = self.calc_level(xp_val)
        last_msg = row[2] if row else 0
        await self.upsert_user(target, xp_val, last_msg)

        badge = self.get_badge(new_level)
        await evt.reply(f"✅ XP set for {target}: {xp_val} (Level {new_level} {badge})")
        if new_level > old_level:
            await evt.reply(f"🎉 {target} leveled up to **Level {new_level}**! {badge}")

    @command.new("leaderboard", help="(Admin only) Show top XP users")
    async def cmd_leaderboard(self, evt: MessageEvent) -> None:
        if not await self.is_admin_or_mod(evt):
            await evt.reply("❌ You are not allowed to use this command.")
            return

        def _get_top():
            with self.database.begin() as conn:
                cur = conn.execute(
                    "SELECT user_id, xp FROM xp_users ORDER BY xp DESC LIMIT 10"
                )
                return cur.fetchall()

        rows = await asyncio.get_event_loop().run_in_executor(None, _get_top)

        if not rows:
            await evt.reply("📊 No data yet.")
            return

        msg_lines = ["🏆 **Leaderboard**"]
        for i, r in enumerate(rows, start=1):
            user_id = r[0]
            xp = r[1]
            lvl = self.calc_level(int(xp))
            badge = self.get_badge(lvl)
            msg_lines.append(f"{i}. {user_id} — Level {lvl} | XP {xp} {badge}")

        await evt.reply("\n".join(msg_lines))

    @command.new("xp", help="Show available XP commands")
    async def cmd_xp_help(self, evt: MessageEvent) -> None:
        if await self.is_admin_or_mod(evt):
            msg = (
                "📘 **XP Commands (Admin)**\n"
                "- `!level`\n"
                "- `!leaderboard`\n"
                "- `!profile [user]`\n"
                "- `!setxp <user> <xp>`\n"
                "- `!xp`\n"
            )
        else:
            msg = (
                "📘 **XP Commands (User)**\n"
                "- `!level`\n"
                "- `!profile [user]`\n"
                "- `!xp`\n"
            )
        await evt.reply(msg)
