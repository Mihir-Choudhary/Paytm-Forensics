"""FR (capabilities): features the account CAN use, from passbook filter config and
remote-config flags. This documents *availability* — never that a feature was used.

Sources (verified structure):
  - app_uthDir/uthSearchFilterMetaData1.json:
        apiUrlsInfo: {endpointName: url}
        filters: [{displayName, requestParam, values:[{displayName, isActive, requestParamValue}]}]
  - files/frc_*_firebase_activate.json:
        {"configs_key": {flagName: "true"/"false"/...}}
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from .base import BaseParser, register
from ..core.models import Capability, Record

# passbook filter requestParam -> human category (only feature-bearing filters)
_FILTER_CATEGORY = {
    "paymentSystem": "Payment instrument",
    "mandateFilter": "Automatic payments",
    "verticalName": "Orders & bookings vertical",
}
_FLAG_RE = re.compile(
    r"mandate|ipo|reserve|pocket|autopay|recurring|split|trading|postpaid", re.I)


@register
class CapabilitiesParser(BaseParser):
    name = "capabilities"
    needs = ("uthSearchFilterMetaData1.json",)

    def available(self) -> bool:
        return self.get("uthSearchFilterMetaData1.json") is not None or self._firebase() is not None

    def _firebase(self):
        for a in self.artifacts.values():
            if a.logical.endswith("_firebase_activate.json"):
                return a
        return None

    def parse(self) -> Iterator[Record]:
        uth = self.get("uthSearchFilterMetaData1.json")
        if uth:
            yield from self._parse_uth(uth)
        fb = self._firebase()
        if fb:
            yield from self._parse_flags(fb)

    def _parse_uth(self, art) -> Iterator[Record]:
        try:
            with open(art.abs_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        # server API endpoints (where the data lives server-side)
        for name, url in (d.get("apiUrlsInfo") or {}).items():
            yield Capability(provenance=self.prov(art, None), raw={},
                             category="Server API endpoint", name=name,
                             value=url, available=True)
        # feature-bearing passbook filters -> capabilities
        for flt in (d.get("filters") or []):
            cat = _FILTER_CATEGORY.get(flt.get("requestParam"))
            if not cat:
                continue
            for v in (flt.get("values") or []):
                yield Capability(provenance=self.prov(art, None), raw={},
                                 category=cat, name=v.get("displayName"),
                                 value=v.get("requestParamValue"),
                                 available=bool(v.get("isActive")))

    def _parse_flags(self, art) -> Iterator[Record]:
        try:
            with open(art.abs_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        flags = d.get("configs_key") if isinstance(d, dict) else None
        if not isinstance(flags, dict):
            return
        for k, val in flags.items():
            if _FLAG_RE.search(k):
                yield Capability(provenance=self.prov(art, None), raw={},
                                 category="Feature flag (remote config)", name=k,
                                 value=str(val), available=(str(val).lower() == "true"))
