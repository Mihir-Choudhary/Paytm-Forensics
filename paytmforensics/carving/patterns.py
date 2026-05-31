"""Validators for high-value forensic identifiers used during carving."""
from __future__ import annotations

import re

RRN_RE = re.compile(r"^\d{12}$")                       # NPCI retrieval reference number
PHONE_RE = re.compile(r"^[6-9]\d{9}$")                 # Indian mobile
VPA_RE = re.compile(r"^[a-zA-Z0-9._-]{2,}@[a-zA-Z]{3,}$")
TXNID_RE = re.compile(r"^(?:PYTM|PTM|AXI|HDF|UPI|ICI|SBI)[A-Za-z0-9]{8,}$")


def is_rrn(s: str) -> bool:
    return bool(isinstance(s, str) and RRN_RE.match(s))


def is_phone(s: str) -> bool:
    return bool(isinstance(s, str) and PHONE_RE.match(s))


def is_vpa(s: str) -> bool:
    return bool(isinstance(s, str) and VPA_RE.match(s) and len(s) < 60)


def is_txn_id(s: str) -> bool:
    return bool(isinstance(s, str) and TXNID_RE.match(s))


def has_any_identifier(values) -> bool:
    """True if a reconstructed record contains at least one forensic identifier
    (keeps false positives down — we only keep carved records that look meaningful)."""
    for v in values:
        if isinstance(v, str) and (is_vpa(v) or is_phone(v) or is_txn_id(v) or is_rrn(v)):
            return True
    return False


def classify(values) -> dict:
    """Pull labelled identifiers out of a reconstructed record's values."""
    out = {"vpas": [], "phones": [], "rrns": [], "txn_ids": [], "texts": []}
    for v in values:
        if not isinstance(v, str):
            continue
        if is_vpa(v):
            out["vpas"].append(v)
        elif is_phone(v):
            out["phones"].append(v)
        elif is_rrn(v):
            out["rrns"].append(v)
        elif is_txn_id(v):
            out["txn_ids"].append(v)
        elif 2 <= len(v) <= 80:
            out["texts"].append(v)
    return out
