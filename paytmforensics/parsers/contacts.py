"""FR-3 Contacts & counterparties: TBL_USERS, contacts db, VPA cache."""
from __future__ import annotations

import json
from typing import Iterator

from .base import BaseParser, register
from ..core.models import Person, Record
from ..ingest import sqlite_ro as sql


def _account_age(get_info: str | None) -> str | None:
    if not get_info:
        return None
    try:
        outer = json.loads(get_info)
        inner = json.loads(outer.get("jsonString", "{}"))
        return inner.get("customerCreationText")
    except (ValueError, TypeError):
        return None


@register
class CounterpartyParser(BaseParser):
    name = "contacts.users"
    needs = ("chatDb.db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("chatDb.db")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "TBL_USERS" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "TBL_USERS"):
                if str(r.get("isMe")) == "1":
                    continue   # subject handled by identity parser
                vpas = [v for v in [r.get("vpa")] if v]
                yield Person(
                    provenance=self.prov(art, "TBL_USERS", rowid),
                    raw={k: r.get(k) for k in ("identifier", "type", "phoneNumber",
                                                "vpa", "mid", "bankName")},
                    customer_id=r.get("identifier"),
                    name=r.get("sendbirdUserName") or r.get("name"),
                    phone=r.get("phoneNumber"),
                    vpas=vpas,
                    person_type=r.get("type"),
                    account_age_text=_account_age(r.get("getInfoSync") or r.get("getPaymentInfoSync")),
                    sendbird_id=r.get("sendbirdUserId"),
                    bank_name=r.get("bankName"),
                    masked_account=r.get("maskedAccNo"),
                    verified_name=r.get("verifiedName"),
                )


@register
class VpaCacheParser(BaseParser):
    name = "contacts.vpa_cache"
    needs = ("cache_database",)

    def parse(self) -> Iterator[Record]:
        art = self.get("cache_database")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "cache_table" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "cache_table"):
                yield Person(
                    provenance=self.prov(art, "cache_table", rowid),
                    raw=r,
                    name=r.get("name"),
                    vpas=[r.get("vpa")] if r.get("vpa") else [],
                    person_type="MERCHANT" if str(r.get("is_verified_merchant")) == "1" else None,
                    verified_name=r.get("verified_name"),
                )
