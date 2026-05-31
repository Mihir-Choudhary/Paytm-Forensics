"""FR-8 Notifications: PaytmMessageDatabase (NotificationData + PushData)."""
from __future__ import annotations

from typing import Iterator

from .base import BaseParser, register
from ..core.models import Notification, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps


@register
class NotificationParser(BaseParser):
    name = "notifications"
    needs = ("PaytmMessageDatabase",)

    def parse(self) -> Iterator[Record]:
        art = self.get("PaytmMessageDatabase")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            tables = sql.list_tables(con)
            if "NotificationData" in tables:
                for rowid, r in sql.rows(con, "NotificationData"):
                    yield Notification(
                        provenance=self.prov(art, "NotificationData", rowid),
                        raw=r,
                        title=r.get("title"),
                        message=r.get("message"),
                        deep_link=r.get("deep_link"),
                        campaign_id=r.get("campaignId"),
                        push_id=r.get("pushId"),
                        timestamp=timestamps.decode(
                            r.get("receiveTime") or r.get("date")).to_dict(),
                    )
            # PushData = lightweight dedup records (presence/expiry only)
            if "PushData" in tables:
                for rowid, r in sql.rows(con, "PushData"):
                    yield Notification(
                        provenance=self.prov(art, "PushData", rowid),
                        raw=r,
                        push_id=r.get("pushIdentifier"),
                        timestamp=timestamps.decode(r.get("expiry")).to_dict(),
                        message="(push dedup record)",
                    )
