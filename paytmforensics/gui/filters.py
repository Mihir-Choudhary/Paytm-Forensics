"""Pure-Python filtering logic (no Qt) — unit-testable headlessly (FR-G3)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

#: record keys that can carry an event time, in priority order. `expires` is deliberately
#: excluded: a cookie expiry is a future deadline, not when anything happened.
_TIME_KEYS = ("utc_iso",)
_TIME_DICT_KEYS = ("timestamp", "last_enqueue", "created", "start_time")


def record_utc(rec: dict) -> Optional[str]:
    """Extract a comparable UTC ISO string from any record shape."""
    for key in _TIME_KEYS:
        if rec.get(key):
            return rec[key]
    for key in _TIME_DICT_KEYS:
        ts = rec.get(key)
        if isinstance(ts, dict) and ts.get("utc_iso"):
            return ts["utc_iso"]
    return None


def record_has_time(rec: dict) -> bool:
    """True if this record *could* carry an event time (even if it is unset)."""
    return any(k in rec for k in _TIME_KEYS) or any(
        isinstance(rec.get(k), dict) for k in _TIME_DICT_KEYS)


def parse_bound(text: str | None, *, end: bool) -> Optional[datetime]:
    """Parse a user-typed date/datetime bound into an aware UTC datetime.

    Accepts ``YYYY-MM-DD``, unpadded ``YYYY-M-D``, a few common regional forms, and full
    ISO timestamps. A bare date used as an UPPER bound means the END of that day, so
    ``date_to=2026-05-12`` includes everything on the 12th -- comparing raw strings made
    it exclude the entire final day, and made an unpadded date exclude almost everything.
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    bare_date = False
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        bare_date = len(s) <= 10
    except ValueError:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                dt = datetime.strptime(s, fmt)
                bare_date = True
                break
            except ValueError:
                continue
        else:
            return None
    if bare_date and end:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _as_dt(utc_iso: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(utc_iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


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


def searchable_strings(rec: dict):
    """Strings a text search should match: record VALUES only.

    Excludes ``provenance``, ``raw`` and ``domain`` so a grid filter and the global search
    agree -- previously the grid also matched source filenames and ingest hashes while
    global search did not.
    """
    yield from _all_strings({k: v for k, v in rec.items()
                             if k not in ("provenance", "raw", "domain")})


_UNSET = object()


def _as_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


@dataclass
class FilterSpec:
    text: str = ""                       # case-insensitive substring across record values
    date_from: Optional[str] = None      # date or ISO; inclusive
    date_to: Optional[str] = None        # date or ISO; inclusive (bare date => end of day)
    origin: str = "any"                  # any | live | carved
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    direction: Optional[str] = None      # credit | debit | None
    source_contains: str = ""            # substring match on provenance.source_file
    field_equals: dict = field(default_factory=dict)
    #: when True, a record whose domain lacks the filtered field is KEPT rather than
    #: silently dropped. Filtering by amount on a grid with no amounts emptied it.
    skip_inapplicable: bool = True

    # ---------------------------------------------------------------- matching #
    def matches(self, rec: dict) -> bool:
        if self.origin != "any":
            if rec.get("provenance", {}).get("origin") != self.origin:
                return False

        if self.text:
            t = self.text.lower()
            if not any(t in s.lower() for s in searchable_strings(rec)):
                return False

        if self.date_from or self.date_to:
            utc = record_utc(rec)
            if utc is None:
                # a record that cannot carry a time is not "outside the range"
                if not (self.skip_inapplicable and not record_has_time(rec)):
                    return False
            else:
                dt = _as_dt(utc)
                if dt is None:
                    return False
                lo = parse_bound(self.date_from, end=False)
                hi = parse_bound(self.date_to, end=True)
                if lo and dt < lo:
                    return False
                if hi and dt > hi:
                    return False

        if self.amount_min is not None or self.amount_max is not None:
            amt = rec.get("amount", _UNSET)
            if amt is _UNSET or amt is None:
                if not self.skip_inapplicable:
                    return False
            else:
                try:
                    a = float(amt)
                except (TypeError, ValueError):
                    return False
                if self.amount_min is not None and a < self.amount_min:
                    return False
                if self.amount_max is not None and a > self.amount_max:
                    return False

        if self.direction:
            d = rec.get("direction", _UNSET)
            if d is _UNSET:
                if not self.skip_inapplicable:
                    return False
            elif d != self.direction:
                return False

        if self.source_contains:
            sf = rec.get("provenance", {}).get("source_file", "") or ""
            if self.source_contains.lower() not in sf.lower():
                return False

        for k, v in self.field_equals.items():
            if k not in rec:
                return False          # a missing field must not match the string "None"
            got = rec.get(k)
            if isinstance(got, bool) or isinstance(v, bool):
                if _as_bool(got) != _as_bool(v):
                    return False
            elif str(got) != str(v):
                return False
        return True

    # ------------------------------------------------------------- persistence #
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FilterSpec":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


def apply_filter(records: list[dict], spec: FilterSpec) -> list[dict]:
    return [r for r in records if spec.matches(r)]
