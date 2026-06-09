"""Pure-Python type-aware sorting for record tables (no Qt) — unit-testable headlessly.

Display cells are strings, so the view must NOT sort lexically ("9" > "100",
"12 May" before "2 Jan"). These helpers sort on the underlying values:
numbers numerically, timestamp dicts by their utc_iso, text case-insensitively.
Blank cells always sink to the bottom, whatever the direction.
"""
from __future__ import annotations


def sort_value(rec: dict, col: str):
    """Underlying value used for sorting (timestamp dicts collapse to utc_iso)."""
    v = rec.get(col)
    if isinstance(v, dict):
        v = v.get("utc_iso") or v.get("raw")
    return v


def is_blank(rec: dict, col: str) -> bool:
    v = sort_value(rec, col)
    return v is None or v == "" or v == []


def sort_key(rec: dict, col: str):
    """Tuple key (type_rank, number, text) keeps mixed-type columns comparable."""
    v = sort_value(rec, col)
    if isinstance(v, bool):
        return (0, float(v), "")
    if isinstance(v, (int, float)):
        return (0, float(v), "")
    if isinstance(v, list):
        return (1, 0.0, ", ".join(str(x) for x in v).lower())
    s = str(v)
    try:
        return (0, float(s), "")
    except ValueError:
        return (1, 0.0, s.lower())


def sort_records(rows: list[dict], col: str, descending: bool = False) -> list[dict]:
    """Stable sort of records on one column; blanks last in either direction."""
    blanks = [r for r in rows if is_blank(r, col)]
    filled = [r for r in rows if not is_blank(r, col)]
    filled.sort(key=lambda r: sort_key(r, col), reverse=descending)
    return filled + blanks
