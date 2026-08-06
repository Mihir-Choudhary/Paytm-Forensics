"""FR (prefs): selected high-value shared_prefs/*.xml key/value extraction.

We surface the forensically meaningful preference files rather than every analytics XML.
Sensitive token-bearing files (Data*.xml) are intentionally NOT parsed here; they are
catalogued by the encrypted-artifact parser instead.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Iterator

from .base import BaseParser, register
from ..core.models import PrefItem, Record

# Files of primary forensic interest. NOT an allowlist any more: every shared_prefs XML
# is parsed (a 7-name allowlist silently dropped 31 of 38 files on real data, including a
# Sendbird session key), and this set only drives display ordering / prominence.
INTEREST = {
    "bank_secure_prefs.xml", "appsflyer-data.xml",
    "com.google.android.gms.measurement.prefs.xml",
    "com.google.firebase.crashlytics.xml", "com.google.android.gms.appid.xml",
    "net.one97.paytm_preferences.xml", "unread_count_prefs.xml",
}

# Encrypted stores are catalogued by the encrypted parser, not dumped here.
SKIP_FILES = {"Data.xml", "DataUPI.xml", "DataERUPEE.xml"}

# exact key names to redact (length-only)
REDACT = {"sso_token=", "pb_auth_token", "bank_user_token", "key_bank_token",
          "bank_refresh_token", "log_id_token", "afUninstallToken"}

# Secrets also hide behind unpredictable key names (base64 Firebase keys, SDK session
# keys), so redact on the KEY pattern or a token-shaped VALUE as well.
REDACT_KEY_RE = re.compile(
    r"(token|secret|session[_-]?key|passw|auth|credential|private[_-]?key|api[_-]?key)",
    re.I)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{16,}")
_FCM = re.compile(r'"token"\s*:\s*"[^"]{20,}"')


def _needs_redaction(key: str, value) -> bool:
    """Exact key list, then the shared key-name / value-shape rules."""
    from ..enrich.secrets import is_secret_key, redact_text
    if key in REDACT or is_secret_key(key):
        return True
    v = "" if value is None else str(value)
    return redact_text(v) != v


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
    needs = tuple(INTEREST)

    def __init__(self, artifacts, hashes, all_artifacts=None):
        super().__init__(artifacts, hashes, all_artifacts)
        self._xml = [a for a in self.all_artifacts
                     if a.rel_path.startswith("shared_prefs/")
                     and a.rel_path.lower().endswith(".xml")
                     and os.path.basename(a.rel_path) not in SKIP_FILES]

    def available(self) -> bool:
        return bool(self._xml)

    def parse(self) -> Iterator[Record]:
        for art in sorted(self._xml, key=lambda a: a.rel_path):
            fname = os.path.basename(art.rel_path)
            for k, v in _read(art.abs_path).items():
                shown = v
                if v and _needs_redaction(k, v):
                    shown = f"<redacted:{len(str(v))} chars>"
                yield PrefItem(
                    provenance=self.prov(art, None),
                    raw={"key": k},
                    key=k, value=shown, pref_file=fname,
                )
