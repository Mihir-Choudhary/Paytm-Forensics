"""FR-4 Communications: chat messages reconstructed from chatDb."""
from __future__ import annotations

import json
from typing import Iterator

from .base import BaseParser, register
from ..core.models import Channel, Message, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps


@register
class ChannelParser(BaseParser):
    """FR-4: TBL_CHANNELS — the conversation list itself.

    Conversations were previously inferred from messages alone, so a channel whose messages
    had been deleted was invisible: 41 channels existed but only 11 were shown.
    """
    name = "chats.channels"
    needs = ("chatDb.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("chatDb.db")
        if not art:
            return
        with self.open(art) as con:
            tables = sql.list_tables(con)
            if "TBL_CHANNELS" not in tables:
                return
            counts: dict = {}
            if "ChatMessageEntity" in tables:
                for _rid, m in sql.rows(con, "ChatMessageEntity"):
                    cu = m.get("channelUrl")
                    if cu:
                        counts[cu] = counts.get(cu, 0) + 1
            members: dict = {}
            if "DBChannelUserEntryCrossRef" in tables:
                for _rid, x in sql.rows(con, "DBChannelUserEntryCrossRef"):
                    cu = x.get("channelUrl")
                    if cu:
                        members[cu] = members.get(cu, 0) + 1
            for rowid, r in sql.rows(con, "TBL_CHANNELS"):
                url = r.get("channelUrl") or r.get("url")
                n = counts.get(url, 0)
                yield Channel(
                    provenance=self.prov(art, "TBL_CHANNELS", rowid),
                    raw=r,
                    channel_url=url,
                    name=(r.get("name") or r.get("channelName") or None),
                    member_count=members.get(url),
                    message_count=n,
                    has_messages=bool(n),
                    timestamp=timestamps.decode(
                        r.get("createdAt") or r.get("lastMessageAt")).to_dict(),
                )


@register
class ChatParser(BaseParser):
    name = "chats"
    needs = ("chatDb.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("chatDb.db")
        if not art:
            return
        with self.open(art) as con:
            tables = sql.list_tables(con)
            if "ChatMessageEntity" not in tables:
                return
            chan_to_party = self._channel_counterparties(con, tables)
            for rowid, r in sql.rows(con, "ChatMessageEntity"):
                amount = None
                rrn = None
                data = r.get("data")
                if data:
                    try:
                        d = json.loads(data)
                        amount = _to_float(d.get("amount") or d.get("displayAmount"))
                        rrn = d.get("rrn")
                    except (ValueError, TypeError):
                        pass
                yield Message(
                    provenance=self.prov(art, "ChatMessageEntity", rowid),
                    raw={k: r.get(k) for k in ("messageContent", "customType",
                                                "channelUrl", "senderName")},
                    channel_url=r.get("channelUrl"),
                    chat_with=chan_to_party.get(r.get("channelUrl")),
                    sender_id=r.get("senderId"),
                    sender_name=r.get("senderName"),
                    msg_type=r.get("customType") or r.get("messageType"),
                    content=r.get("messageContent"),
                    amount=amount,
                    rrn=rrn,
                    status=str(r.get("messageState")) if r.get("messageState") is not None else None,
                    encrypted_blob_present=bool(r.get("rawMessage")),
                    timestamp=timestamps.decode(r.get("createdAt")).to_dict(),
                )


    @staticmethod
    def _channel_counterparties(con, tables) -> dict:
        """Map channelUrl -> counterparty display name using the channel membership
        cross-reference and the users table (the non-subject member of each channel)."""
        if not ({"DBChannelUserEntryCrossRef", "TBL_USERS"} <= set(tables)):
            return {}
        # userPrimaryKey -> (name, isMe)
        users = {}
        for _rid, u in sql.rows(con, "TBL_USERS"):
            users[u.get("userPrimaryKey")] = (
                u.get("sendbirdUserName") or u.get("name"), str(u.get("isMe")) == "1")
        out: dict = {}
        for _rid, x in sql.rows(con, "DBChannelUserEntryCrossRef"):
            chan = x.get("channelUrl")
            name, is_me = users.get(x.get("userPrimaryKey"), (None, False))
            if not chan or not name or is_me:
                continue
            out.setdefault(chan, name)   # first non-subject member wins
        return out


def _to_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
