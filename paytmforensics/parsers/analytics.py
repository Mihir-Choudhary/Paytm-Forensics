"""Decode protobuf-encoded analytics/transport payloads (best-effort).

Sources:
  - com.google.android.datatransport.events: events.payload, event_payloads.bytes
  - google_app_measurement_local.db: messages.entry
These are usually flushed (empty) on a live device; when present, fields are decoded
generically and surfaced for the examiner.
"""
from __future__ import annotations

from typing import Iterator

from .base import BaseParser, register
from ..core.models import Record, DiagnosticEvent
from ..ingest import sqlite_ro as sql
from ..enrich import protobuf, timestamps


@register
class TransportEventsParser(BaseParser):
    name = "analytics.transport"
    needs = ("com.google.android.datatransport.events", "google_app_measurement_local.db")

    def parse(self) -> Iterator[Record]:
        yield from self._events()
        yield from self._measurement()

    def _events(self) -> Iterator[Record]:
        art = self.get("com.google.android.datatransport.events")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            tabs = sql.list_tables(con)
            if "events" not in tabs:
                return
            cols = sql.columns(con, "events")
            if "payload" not in cols:
                return
            for rowid, r in sql.rows(con, "events"):
                blob = r.get("payload")
                fields = protobuf.decode(blob) if isinstance(blob, (bytes, bytearray)) else None
                yield DiagnosticEvent(
                    provenance=self.prov(art, "events", rowid),
                    raw={"decoded": fields},
                    event_type="transport_event",
                    message=str(fields)[:300] if fields else "(opaque/undecodable payload)",
                    network_type=r.get("transport_name"),
                    timestamp=timestamps.decode(r.get("timestamp_ms")).to_dict(),
                )

    def _measurement(self) -> Iterator[Record]:
        art = self.get("google_app_measurement_local.db")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "messages" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "messages"):
                blob = r.get("entry")
                fields = protobuf.decode(blob) if isinstance(blob, (bytes, bytearray)) else None
                yield DiagnosticEvent(
                    provenance=self.prov(art, "messages", rowid),
                    raw={"decoded": fields},
                    event_type="ga_measurement",
                    app_version=r.get("app_version"),
                    message=str(fields)[:300] if fields else "(opaque/undecodable payload)",
                )
