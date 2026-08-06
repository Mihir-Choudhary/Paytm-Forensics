"""Presentation-only masking of PII and bank identifiers.

Purpose: let an examiner demo, screen-share or screenshot a case without exposing the
subject's or a counterparty's identity. This is a **display layer**, deliberately:

  * `case.db` always holds the full, unmasked evidence — masking here must never change
    what was parsed, or the case would no longer be a faithful record.
  * Masking is **off by default**. The tool shows everything unless the examiner asks.
  * Masks preserve shape and length so layouts do not jump and an examiner can still see
    "a 10-digit number was here".
  * Personal *names* keep each word's first letter (`A••• F•••`) so an examiner can still
    tell two counterparties apart in a masked working view. Everything else — digits,
    UPI IDs, account and IFSC identifiers — is masked completely, including the PSP handle
    and bank code, both of which name the bank.

Policy — what counts as sensitive:

  IDENTITY  names, phone numbers, customer/Sendbird/device IDs, e-mail addresses
  BANKING   VPAs, account identifiers, IFSC/branch codes, masked account numbers,
            bank names, instrument names, RRNs and transaction IDs
  LOCATION  latitude/longitude and pincode
  FREE TEXT message bodies, narrations, search queries, notification text — these embed
            names and numbers, so identifier-shaped substrings are masked in place

Deliberately NOT masked: amounts, dates, counts, status/category labels, provenance and
hashes. Those carry the analysis and are not identifying on their own; hiding them would
make a masked view useless rather than merely discreet.
"""
from __future__ import annotations

import re

BULLET = "•"          # • — renders in every theme, fixed width in most fonts

# --------------------------------------------------------------------------- #
#  field classification, by record key
# --------------------------------------------------------------------------- #
IDENTITY_FIELDS = {
    "name", "names", "verified_name", "sender_name", "chat_with", "counterparty_name",
    "phone", "phones", "counterparty_mobile", "customer_id", "customer_ids",
    "sendbird_id", "sendbird_ids", "sender_id", "device_id", "user_id", "session_id",
    "email", "subject_name",
}
BANK_FIELDS = {
    "vpa", "vpas", "counterparty_vpa", "account_used", "account_bank", "account_branch",
    "ifsc_or_branch", "masked_number", "masked_account", "bank_name", "instrument",
    "rrn", "txn_id", "source_txn_id", "push_id", "campaign_id",
}
COORD_FIELDS = {"latitude", "longitude"}
LOCATION_FIELDS = COORD_FIELDS | {"pincode"}
#: free text that embeds identifiers; masked by pattern rather than wholesale
FREETEXT_FIELDS = {
    "content", "narration", "note", "query", "title", "message", "summary", "preview",
    "value", "detail", "body_preview", "error_message", "meaning",
}

SENSITIVE_FIELDS = IDENTITY_FIELDS | BANK_FIELDS | LOCATION_FIELDS | FREETEXT_FIELDS

# --------------------------------------------------------------------------- #
#  value patterns (for free text and for the report/exports)
# --------------------------------------------------------------------------- #
_VPA = re.compile(r"\b([A-Za-z0-9._\-]{2,})@([A-Za-z]{2,20})\b")
_PHONE = re.compile(r"(?<!\d)([6-9]\d{9})(?!\d)")
_LONGNUM = re.compile(r"(?<!\d)(\d{10,19})(?!\d)")        # RRNs, customer ids, card-ish
_IFSC = re.compile(r"(?<![A-Z0-9])([A-Z]{4})0([A-Z0-9]{6})(?![A-Z0-9])")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_COORD = re.compile(r"(-?\d{1,3}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")
#: name-ish assignments inside embedded JSON / query strings. Free-text masking is
#: otherwise pattern-based, so a plain personal name in a blob would survive: a
#: SharedPreferences value carried "displayName":"<subject>" straight into the report.
_NAME_ASSIGN = re.compile(
    r"""(?ix)
    ( ["']? (?: display_?name | full_?name | first_?name | last_?name | middle_?name
              | user_?name | customer_?name | payee_?name | payer_?name | benef\w*_?name
              | account_?holder\w* | nick_?name | name )
      ["']? \s* [:=] \s* ["']? )
    ( [^"',&}\]\n]{2,80} )
    """)


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _bul(n: int) -> str:
    return BULLET * max(1, n)


def mask_scalar(value, kind: str = "identity"):
    """Mask one value, preserving its shape so the grid stays readable."""
    if value is None or value == "":
        return value
    s = str(value)

    if kind == "coords":
        # Coordinates must stay NUMERIC: the map view does arithmetic on them (bounds,
        # marker placement). Reduce precision to ~1 decimal place, which is city-level
        # (~11 km) rather than a doorstep, and keep the type.
        try:
            return round(float(s), 1)
        except (TypeError, ValueError):
            return _bul(len(s))

    if kind == "bank":
        # Mask the whole identifier, keeping only structural separators so the value is
        # still recognisable as a UPI ID / account reference. The PSP handle and the IFSC
        # bank code are deliberately NOT preserved: both name the bank, which is exactly
        # what "hide bank details" is meant to hide.
        return _mask_keep_separators(s)

    if s.replace("+", "").replace(" ", "").isdigit():
        return _bul(len(s))
    return _mask_words(s)


def _mask_keep_separators(s: str) -> str:
    """Bullet every alphanumeric character, leaving @ . _ - / separators in place."""
    return "".join(ch if ch in "@._-/ " else BULLET for ch in s)


def _mask_words(s: str) -> str:
    """Keep each word's first character, mask the rest: 'Acme Foods' -> 'A••• F••••'."""
    out = []
    for w in s.split(" "):
        if len(w) <= 1:
            out.append(w)
        else:
            out.append(w[0] + _bul(len(w) - 1))
    return " ".join(out)


def mask_text(s, known_names=None):
    """Mask identifier-shaped substrings inside free text, leaving prose intact.

    `known_names` is an optional set of real names harvested from the case (person /
    entity records). Names have no distinguishing shape, so pattern matching alone cannot
    find them in a blob; literal replacement closes that gap.
    """
    if not isinstance(s, str) or not s:
        return s
    out = _EMAIL.sub(lambda m: _bul(len(m.group(0))), s)
    out = _NAME_ASSIGN.sub(lambda m: m.group(1) + _mask_words(m.group(2)), out)
    for nm in sorted(known_names or (), key=len, reverse=True):
        if nm and len(nm) > 2 and nm in out:
            out = out.replace(nm, _mask_words(nm))
    out = _VPA.sub(lambda m: f"{_bul(len(m.group(1)))}@{_bul(len(m.group(2)))}", out)
    # mask the bank code too: it names the bank, same reasoning as the PSP handle
    out = _IFSC.sub(lambda m: f"{_bul(4)}0{_bul(6)}", out)
    out = _PHONE.sub(lambda m: _bul(10), out)
    out = _LONGNUM.sub(lambda m: _bul(len(m.group(1))), out)
    # coordinate pairs, e.g. a timeline summary "12.34567,76.54321 (diagnostic)"
    out = _COORD.sub(
        lambda m: f"{float(m.group(1)):.1f}{BULLET*4},{float(m.group(2)):.1f}{BULLET*4}", out)
    return out


def field_kind(field: str) -> str | None:
    """Which masking rule applies to a record key, or None if it is not sensitive."""
    if field in COORD_FIELDS:
        return "coords"          # stays numeric, precision reduced
    if field == "pincode":
        return "identity"        # a postcode is digits, not a coordinate
    if field in BANK_FIELDS:
        return "bank"
    if field in IDENTITY_FIELDS:
        return "identity"
    if field in FREETEXT_FIELDS:
        return "freetext"
    return None


def mask_field(field: str, value, known_names=None):
    """Mask a single record field according to its classification."""
    kind = field_kind(field)
    if kind is None or value is None or value == "":
        return value
    if isinstance(value, (list, tuple)):
        return [mask_field(field, v, known_names) for v in value]
    if isinstance(value, dict):
        return {k: mask_field(field, v, known_names) for k, v in value.items()}
    if kind == "freetext":
        return mask_text(str(value), known_names)
    return mask_scalar(value, kind)


def mask_any(key, value, known_names=None):
    """Recursively mask an arbitrary nested structure.

    `raw` holds whole source rows, which can nest dicts and lists several levels deep
    (a passbook row's JSON columns, a cart response). Masking only the top-level string
    values left identifiers sitting one level down — e.g. a customer id inside
    `raw["cart"]`, and a phone number inside a nested source column.
    """
    if isinstance(value, dict):
        return {k: mask_any(k, v, known_names) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_any(key, v, known_names) for v in value]
    if field_kind(key):
        return mask_field(key, value, known_names)
    if isinstance(value, str):
        return mask_text(value, known_names)
    return value


def mask_record(rec: dict, known_names=None) -> dict:
    """Return a masked copy of a record. Provenance and hashes are left untouched."""
    out = {}
    for k, v in rec.items():
        if k in ("provenance", "domain"):
            out[k] = v
        elif k == "raw":
            out[k] = mask_any("raw", v or {}, known_names)
        else:
            out[k] = mask_field(k, v, known_names)
    return out


def summary_line() -> str:
    """One-line description of what masking does, for a UI banner / report note."""
    return ("Sensitive-data masking is ON: names, phone numbers, customer and device IDs, "
            "UPI IDs, account/IFSC identifiers, RRNs and precise coordinates are hidden. "
            "Amounts, dates, statuses and provenance are shown unchanged. The case "
            "database still holds the complete unmasked evidence.")
