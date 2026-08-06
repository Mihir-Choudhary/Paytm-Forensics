"""FR-6 Consents & permissions: ups_database.ConsentTable."""
from __future__ import annotations

from typing import Iterator

from .base import BaseParser, register
from ..core.models import Consent, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps


@register
class ConsentParser(BaseParser):
    name = "consents"
    needs = ("ups_database",)

    def parse(self) -> Iterator[Record]:
        art = self.get("ups_database")
        if not art:
            return
        with self.open(art) as con:
            if "ConsentTable" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "ConsentTable"):
                yield Consent(
                    provenance=self.prov(art, "ConsentTable", rowid),
                    raw=r,
                    consent_key=r.get("consentKey"),
                    consent_value=r.get("consentValue"),
                    synced_with_server=str(r.get("syncedWithServer")) == "1",
                    timestamp=timestamps.decode(r.get("syncTimestamp")).to_dict(),
                )
