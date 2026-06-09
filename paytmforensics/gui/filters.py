"""Pure-Python filtering logic (no Qt) — unit-testable headlessly (FR-G3)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

_USER_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$")


def parse_user_date(s: str | None) -> tuple[bool, Optional[str]]:
    """Validate/normalise an analyst-typed date for FilterSpec.

    Accepts "YYYY-MM-DD" optionally followed by " HH:MM[:SS]" or "THH:MM[:SS]".
    Returns (ok, normalised). Empty input is valid and means "no bound".
    A malformed date returns (False, None) so callers can refuse to apply the
    filter instead of silently matching nothing.
    """
    s = (s or "").strip()
    if not s:
        return True, None
    if not _USER_DATE_RE.match(s):
        return False, None
    return True, s.replace(" ", "T")


def record_utc(rec: dict) -> Optional[str]:
    """Extract a comparable UTC ISO string from any record shape."""
    for key in ("utc_iso",):
        if rec.get(key):
            return rec[key]
    # cookie/webcache use "created", crash uses "start_time"
    for key in ("timestamp", "last_enqueue", "created", "start_time"):
        ts = rec.get(key)
        if isinstance(ts, dict) and ts.get("utc_iso"):
            return ts["utc_iso"]
    return None


def searchable_values(rec: dict) -> dict:
    """The part of a record text search may look at: field values only, never the
    provenance/raw bookkeeping (else file paths and ingest hashes pollute matches).
    Shared by the per-table filter and global search so both behave identically."""
    return {k: v for k, v in rec.items() if k not in ("provenance", "raw", "domain")}


def _all_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _all_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _all_strings(v)
    elif obj is not None:
        yield str(obj)


@dataclass
class FilterSpec:
    text: str = ""                       # case-insensitive substring across field values
    date_from: Optional[str] = None      # ISO; inclusive
    date_to: Optional[str] = None        # ISO; inclusive (a bare date covers that whole day)
    origin: str = "any"                  # any | live | carved
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    direction: Optional[str] = None      # credit | debit | None
    source_contains: str = ""            # substring match on provenance.source_file
    field_equals: dict = field(default_factory=dict)

    def matches(self, rec: dict) -> bool:
        # origin
        if self.origin != "any":
            if rec.get("provenance", {}).get("origin") != self.origin:
                return False
        # text (across field values only — same subset as global search)
        if self.text:
            t = self.text.lower()
            if not any(t in s.lower() for s in _all_strings(searchable_values(rec))):
                return False
        # date range
        if self.date_from or self.date_to:
            utc = record_utc(rec)
            if utc is None:
                return False
            if self.date_from and utc < self.date_from:
                return False
            # prefix compare so date_to is inclusive at its own granularity:
            # date_to "2026-05-12" keeps "2026-05-12T23:59:59…" but drops the 13th
            if self.date_to and utc[:len(self.date_to)] > self.date_to:
                return False
        # amount
        amt = rec.get("amount")
        if self.amount_min is not None and (amt is None or amt < self.amount_min):
            return False
        if self.amount_max is not None and (amt is None or amt > self.amount_max):
            return False
        # direction
        if self.direction and rec.get("direction") != self.direction:
            return False
        # source file
        if self.source_contains:
            sf = rec.get("provenance", {}).get("source_file", "") or ""
            if self.source_contains.lower() not in sf.lower():
                return False
        # arbitrary field equals
        for k, v in self.field_equals.items():
            if str(rec.get(k)) != str(v):
                return False
        return True


def apply_filter(records: list[dict], spec: FilterSpec) -> list[dict]:
    return [r for r in records if spec.matches(r)]
