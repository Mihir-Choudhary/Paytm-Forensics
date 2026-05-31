"""Offline IFSC resolution: bank code (first 4 chars of an IFSC) -> bank name.

Branch-level resolution needs the full RBI IFSC DB (~150k rows); we deliberately bundle
only the standardized bank-code map (small, offline) and expose the raw branch code.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Optional

from ..resources_util import resource_path

_IFSC_RE = re.compile(r"([A-Z]{4})0([A-Z0-9]{6})")


@lru_cache(maxsize=1)
def _codes() -> dict:
    try:
        with open(resource_path("ifsc_bank_codes.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def extract_ifsc(account_used: Optional[str]) -> Optional[str]:
    """account_used like '0000_HDFC0009999' -> 'HDFC0009999'."""
    if not account_used:
        return None
    m = _IFSC_RE.search(account_used.upper())
    return f"{m.group(1)}0{m.group(2)}" if m else None


def resolve(account_used: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return (bank_name, branch_code) for an account/IFSC string."""
    ifsc = extract_ifsc(account_used)
    if not ifsc:
        return None, None
    bank = _codes().get(ifsc[:4])
    branch = ifsc[5:]
    return bank, branch
