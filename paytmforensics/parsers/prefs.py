"""FR (prefs): selected high-value shared_prefs/*.xml key/value extraction.

We surface the forensically meaningful preference files rather than every analytics XML.
Sensitive token-bearing files (Data*.xml) are intentionally NOT parsed here; they are
catalogued by the encrypted-artifact parser instead.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Iterator

from .base import BaseParser, register
from ..core.models import PrefItem, Record

# files worth surfacing (others are analytics noise)
INTEREST = {
    "bank_secure_prefs.xml", "appsflyer-data.xml",
    "com.google.android.gms.measurement.prefs.xml",
    "com.google.firebase.crashlytics.xml", "com.google.android.gms.appid.xml",
    "net.one97.paytm_preferences.xml", "unread_count_prefs.xml",
}

# keys to redact (length-only) — secrets that should not be copied verbatim into a report
REDACT = {"sso_token=", "pb_auth_token", "bank_user_token", "key_bank_token",
          "bank_refresh_token", "log_id_token", "afUninstallToken"}


def _read(path: str) -> dict:
    out = {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return out
    for child in root:
        name = child.get("name")
        if not name:
            continue
        val = child.text if child.tag == "string" else child.get("value")
        out[name] = val
    return out


@register
class PrefsParser(BaseParser):
    name = "prefs"
    # discovery indexes by filename; we just need any prefs to exist
    needs = tuple(INTEREST)

    def parse(self) -> Iterator[Record]:
        for fname in INTEREST:
            art = self.get(fname)
            if not art:
                continue
            for k, v in _read(art.abs_path).items():
                shown = v
                if k in REDACT and v:
                    shown = f"<redacted:{len(str(v))} chars>"
                yield PrefItem(
                    provenance=self.prov(art, None),
                    raw={"key": k},
                    key=k, value=shown, pref_file=fname,
                )
