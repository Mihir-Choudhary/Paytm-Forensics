"""Timestamp decoding with full transparency (EV-7).

Paytm stores Unix-millisecond timestamps almost everywhere; WebView (Chrome) cookies use
microseconds since 1601-01-01. We detect the epoch type heuristically by magnitude and
ALWAYS preserve the raw value alongside the decoded UTC ISO string.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from ..core.models import TimestampValue

# WebKit/Chrome epoch is 1601-01-01; offset to Unix epoch in seconds
_WEBKIT_OFFSET = 11644473600


def decode(value: Any, hint: Optional[str] = None) -> TimestampValue:
    """Decode a timestamp value, returning raw + epoch_type + utc_iso.

    hint may be "unix_ms", "unix_s", "webkit_us" to force interpretation.
    """
    if value is None or value == "":
        return TimestampValue(raw=value)
    try:
        n = int(value)
    except (ValueError, TypeError):
        return TimestampValue(raw=value)

    if n <= 0:
        return TimestampValue(raw=value, epoch_type="invalid")

    etype, dt = _interpret(n, hint)
    if dt is None:
        return TimestampValue(raw=value, epoch_type=etype)
    return TimestampValue(raw=value, epoch_type=etype, utc_iso=dt.isoformat())


def _interpret(n: int, hint: Optional[str]):
    try:
        if hint == "unix_s":
            return "unix_s", datetime.fromtimestamp(n, tz=timezone.utc)
        if hint == "unix_ms":
            return "unix_ms", datetime.fromtimestamp(n / 1000, tz=timezone.utc)
        if hint == "webkit_us":
            return "webkit_us", datetime.fromtimestamp(n / 1_000_000 - _WEBKIT_OFFSET, tz=timezone.utc)

        # Auto-detect by magnitude.
        # WebKit microseconds since 1601 for current dates ~ 1.3e16+
        if n > 1_000_000_000_000_000:                 # ~16+ digits -> webkit microseconds
            return "webkit_us", datetime.fromtimestamp(n / 1_000_000 - _WEBKIT_OFFSET, tz=timezone.utc)
        if n > 100_000_000_000:                        # ~13 digits -> unix milliseconds
            return "unix_ms", datetime.fromtimestamp(n / 1000, tz=timezone.utc)
        if n > 1_000_000_000:                          # ~10 digits -> unix seconds
            return "unix_s", datetime.fromtimestamp(n, tz=timezone.utc)
        return "unknown", None
    except (OverflowError, OSError, ValueError):
        return "out_of_range", None


def to_local(utc_iso: Optional[str], tz_offset_hours: float) -> Optional[str]:
    """Render a stored UTC ISO string in an examiner-selected timezone."""
    if not utc_iso:
        return None
    try:
        dt = datetime.fromisoformat(utc_iso)
        return (dt + timedelta(hours=tz_offset_hours)).isoformat()
    except ValueError:
        return None
