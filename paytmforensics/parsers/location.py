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
        with self.open(art) as con:
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
        with self.open(art) as con:
            if "cookies" not in [t.lower() for t in sql.list_tables(con)]:
                return
            # Group by host: pairing the LAST 'lat' row with the LAST 'long' row could
            # combine coordinates set by two different sites into one fabricated fix.
            per_host: dict = {}
            for rowid, r in sql.rows(con, "cookies"):
                name = (r.get("name") or "").lower()
                host = r.get("host_key") or ""
                if name not in ("lat", "long", "enteredpincode"):
                    continue
                slot = per_host.setdefault(host, {})
                if name == "lat":
                    slot["lat"] = _f(r.get("value"))
                    slot["ts"] = r.get("creation_utc")
                    slot["rowid"] = rowid
                elif name == "long":
                    slot["lon"] = _f(r.get("value"))
                else:
                    slot["pincode"] = r.get("value")
            for host, s in sorted(per_host.items()):
                if s.get("lat") is None or s.get("lon") is None:
                    continue
                yield LocationFix(
                    provenance=self.prov(art, "cookies", s.get("rowid")),
                    raw={"host": host, "lat": s["lat"], "long": s["lon"],
                         "pincode": s.get("pincode")},
                    latitude=s["lat"], longitude=s["lon"], source_kind="cookie",
                    pincode=s.get("pincode"),
                    timestamp=timestamps.decode(s.get("ts"), hint="webkit_us").to_dict(),
                )


def _f(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return None
