"""Central secret redaction for anything the tool copies into a case file.

METHODOLOGY §6 requires token-bearing values to be length-redacted so live credentials are
not copied into a report. That was originally implemented only in the SharedPreferences
parser, keyed on an exact list of key names — so any parser that emits *recovered free-form
text* (WebView local storage, IndexedDB, service-worker cache bodies, DataStore blobs,
Java-serialized stores) could still copy a live token verbatim.

Everything here is value-shape based, so it works on recovered strings where there is no
key name to match against.
"""
from __future__ import annotations

import re

#: a 3-part JWT (header.payload.signature)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")
#: FCM / GCM registration tokens: "<instance-id>:APA91b<long>"
_FCM = re.compile(r"\b[A-Za-z0-9_\-]{8,}:APA91b[A-Za-z0-9_\-]{20,}")
#: an explicit token/secret assignment inside a JSON or query string
_ASSIGN = re.compile(
    r"""(?ix)
    ( "? (?: access[_-]?token | refresh[_-]?token | id[_-]?token | auth[_-]?token
          | sso[_-]?token | session[_-]?key | client[_-]?secret | api[_-]?key
          | password | passwd | secret | bearer ) "? \s* [:=] \s* "? )
    ( [A-Za-z0-9._\-+/]{12,} )
    """)
#: Authorization headers
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9._\-+/=]{12,})")

#: key names that mark a value as secret regardless of its shape
KEY_RE = re.compile(
    r"(token|secret|session[_-]?key|passw|auth|credential|private[_-]?key|api[_-]?key)",
    re.I)


def _mask(m, group: int = 0) -> str:
    val = m.group(group)
    return f"<redacted:{len(val)} chars>"


def redact_text(text):
    """Replace credential-shaped substrings in free-form recovered text.

    Length is preserved as metadata so an examiner can still say "a 300-character JWT was
    present here" without the case file carrying a usable credential.
    """
    if not isinstance(text, str) or not text:
        return text
    out = _JWT.sub(lambda m: _mask(m), text)
    out = _FCM.sub(lambda m: _mask(m), out)
    out = _BEARER.sub(lambda m: f"{m.group(1)} <redacted:{len(m.group(2))} chars>", out)
    out = _ASSIGN.sub(lambda m: f"{m.group(1)}<redacted:{len(m.group(2))} chars>", out)
    return out


def is_secret_key(key: str) -> bool:
    return bool(key) and bool(KEY_RE.search(str(key)))


def redact_value(key, value):
    """Redact by key name OR by value shape. Use where a key name is available."""
    if value in (None, ""):
        return value
    if is_secret_key(key):
        return f"<redacted:{len(str(value))} chars>"
    return redact_text(value)
