"""FR-5 Location history: bank_signal GPS events + WebView cookie lat/long/pincode."""
from __future__ import annotations

import json
from typing import Iterator

from .base import BaseParser, register
from ..core.models import LocationFix, Record
from ..ingest import sqlite_ro as sql
from ..enrich import timestamps


@register
class SignalLocationParser(BaseParser):
    name = "location.signal"
    needs = ("bank_signal",)

    def parse(self) -> Iterator[Record]:
        art = self.get("bank_signal")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "SignalEventDb" not in sql.list_tables(con):
                return
            for rowid, r in sql.rows(con, "SignalEventDb"):
                ev = r.get("signalEvent")
                if not ev:
                    continue
                try:
                    outer = json.loads(ev)
                except (ValueError, TypeError):
                    continue
                if outer.get("eventType") != "location_event":
                    continue
                payload = outer.get("payload")
                try:
                    p = json.loads(payload) if isinstance(payload, str) else (payload or {})
                except (ValueError, TypeError):
                    p = {}
                lat, lon = p.get("latitude"), p.get("longitude")
                if lat is None or lon is None:
                    continue
                yield LocationFix(
                    provenance=self.prov(art, "SignalEventDb", rowid),
                    raw=p,
                    latitude=_f(lat), longitude=_f(lon), speed=_f(p.get("speed")),
                    source_kind="signal",
                    timestamp=timestamps.decode(r.get("deviceDateTime")).to_dict(),
                )


@register
class CookieLocationParser(BaseParser):
    name = "location.cookie"
    needs = ("Cookies",)

    def parse(self) -> Iterator[Record]:
        art = self.get("Cookies")
        if not art:
            return
        with sql.open_ro(art.abs_path) as con:
            if "cookies" not in [t.lower() for t in sql.list_tables(con)]:
                return
            lat = lon = pincode = None
            ts_raw = None
            for _rid, r in sql.rows(con, "cookies"):
                name = (r.get("name") or "").lower()
                val = r.get("value")
                if name == "lat":
                    lat = _f(val); ts_raw = r.get("creation_utc")
                elif name == "long":
                    lon = _f(val)
                elif name == "enteredpincode":
                    pincode = val
            if lat is not None and lon is not None:
                yield LocationFix(
                    provenance=self.prov(art, "cookies"),
                    raw={"lat": lat, "long": lon, "pincode": pincode},
                    latitude=lat, longitude=lon, source_kind="cookie",
                    pincode=pincode,
                    timestamp=timestamps.decode(ts_raw, hint="webkit_us").to_dict(),
                )


def _f(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
