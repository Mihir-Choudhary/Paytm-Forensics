"""FR-9 Searches & app state: recent searches, storefront feature flags, reminders."""
from __future__ import annotations

import json
from typing import Iterator

from .base import BaseParser, register
from ..core.models import SearchQuery, ConfigItem, Transaction, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps


@register
class RecentSearchParser(BaseParser):
    name = "search.recent"
    needs = ("search_db",)

    def parse(self) -> Iterator[Record]:
        art = self.get("search_db")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "recent_Search_Tbl" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "recent_Search_Tbl"):
                query = r.get("id")
                item = r.get("item")
                if item:
                    try:
                        query = json.loads(item).get("cta", {}).get("label", query) or query
                    except (ValueError, TypeError):
                        pass
                yield SearchQuery(
                    provenance=self.prov(art, "recent_Search_Tbl", rowid),
                    raw={"id": r.get("id"), "vertical_id": r.get("vertical_id")},
                    query=r.get("id"),
                    vertical_id=r.get("vertical_id"),
                    url=r.get("url"),
                    timestamp=timestamps.decode(r.get("timestamp")).to_dict(),
                )


@register
class StorefrontFlagParser(BaseParser):
    name = "state.storefront_flags"
    needs = ("storefront_db_try3",)

    def parse(self) -> Iterator[Record]:
        art = self.get("storefront_db_try3")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "sf_v_cache_table" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "sf_v_cache_table"):
                val = r.get("dataValue")
                try:
                    val = json.loads(val).get("value", val)
                except (ValueError, TypeError):
                    pass
                from ..enrich import config_explain
                kind, meaning = config_explain.explain(r.get("dataKey"), str(val))
                yield ConfigItem(
                    provenance=self.prov(art, "sf_v_cache_table", rowid),
                    raw=r,
                    key=r.get("dataKey"),
                    value=str(val),
                    kind=kind, meaning=meaning,
                )
