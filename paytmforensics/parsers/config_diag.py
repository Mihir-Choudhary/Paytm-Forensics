"""FR-10 Configuration & diagnostics.

- appManagerDB / bank_app_manager_database  -> feature flags & endpoints (config),
  diffed against bundled defaults to flag changed values.
- paytm_error_analytics / paytmbank_error_analytics -> diagnostic/session events.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Iterator

from .base import BaseParser, register
from ..core.models import ConfigItem, DiagnosticEvent, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps
from ..enrich import config_explain
from ..enrich.secrets import redact_value
from ..resources_util import resource_path

_DEFAULTS = resource_path("bankappmanager_defaults.json")


@lru_cache(maxsize=1)
def _bank_defaults() -> dict:
    try:
        with open(_DEFAULTS, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {e["key"]: e.get("value")
                for e in data.get("response", {}).get("list", []) if "key" in e}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


@register
class AppManagerParser(BaseParser):
    name = "config.appmanager"
    needs = ("appManagerDB",)

    def parse(self) -> Iterator[Record]:
        art = self.get("appManagerDB")
        if not art:
            return
        with self.open(art) as con:
            if "ItemTable" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "ItemTable"):
                kind, meaning = config_explain.explain(r.get("keyValue"), r.get("value"))
                yield ConfigItem(
                    provenance=self.prov(art, "ItemTable", rowid),
                    raw=r, key=r.get("keyValue"),
                    value=redact_value(r.get("keyValue"), r.get("value")),
                    kind=kind, meaning=meaning,
                )


@register
class BankConfigParser(BaseParser):
    name = "config.bank"
    needs = ("bank_app_manager_database",)

    def parse(self) -> Iterator[Record]:
        art = self.get("bank_app_manager_database")
        if not art:
            return
        defaults = _bank_defaults()
        with self.open(art) as con:
            if "bankAppManagerTable" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "bankAppManagerTable"):
                key = r.get("key")
                val = r.get("value")
                changed = None
                if key in defaults:
                    changed = (defaults[key] != val)
                kind, meaning = config_explain.explain(key, val)
                yield ConfigItem(
                    provenance=self.prov(art, "bankAppManagerTable", rowid),
                    raw=r, key=key, value=redact_value(key, val),
                    is_changed_from_default=changed,
                    kind=kind, meaning=meaning,
                )


class _DiagBase(BaseParser):
    table = "Event"

    def _emit(self, art) -> Iterator[Record]:
        from ..enrich import errorcodes
        with self.open(art) as con:
            if self.table not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, self.table):
                ev = r.get("event_data")
                d = {}
                if ev:
                    try:
                        d = json.loads(ev)
                    except (ValueError, TypeError):
                        d = {}
                loc = d.get("location") or {}
                ec = d.get("errorCode")
                ec = None if ec in (0, "0", None, "") else str(ec)
                yield DiagnosticEvent(
                    provenance=self.prov(art, self.table, rowid),
                    raw={k: r.get(k) for k in ("event_type", "customer_id",
                                                "device_id", "network_type")},
                    event_type=r.get("event_type"),
                    customer_id=r.get("customer_id"),
                    device_id=r.get("device_id"),
                    app_version=d.get("appVersion"),
                    network_type=r.get("network_type") or d.get("networkType"),
                    message=d.get("customMessage") or d.get("eventDescription"),
                    flow_name=d.get("flowName"),
                    screen_name=d.get("screenName"),
                    error_code=ec,
                    error_message=(d.get("errorMsg") or errorcodes.message(ec)),
                    battery_pct=_int(d.get("batteryPercentage")),
                    network_carrier=d.get("networkCarrier"),
                    process_state=d.get("processState"),
                    is_rooted=d.get("isRooted"),
                    session_id=d.get("appSessionId"),
                    latitude=_float(loc.get("lat")),
                    longitude=_float(loc.get("lon")),
                    timestamp=timestamps.decode(
                        r.get("event_log_time") or r.get("date_time")).to_dict(),
                )


@register
class PaytmDiagParser(_DiagBase):
    name = "diagnostics.paytm"
    needs = ("paytm_error_analytics",)
    table = "Event"

    def parse(self) -> Iterator[Record]:
        art = self.get("paytm_error_analytics")
        if art:
            yield from self._emit(art)


@register
class BankDiagParser(_DiagBase):
    name = "diagnostics.bank"
    needs = ("paytmbank_error_analytics",)
    table = "PBHawkEyeEvent"

    def parse(self) -> Iterator[Record]:
        art = self.get("paytmbank_error_analytics")
        if art:
            yield from self._emit(art)


def _int(v):
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
