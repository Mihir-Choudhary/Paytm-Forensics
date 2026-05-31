"""Generic protobuf wire-format decoder (no .proto needed).

Decodes raw protobuf bytes into field-number → value(s), recursing into nested
length-delimited messages and decoding printable bytes as UTF-8 strings. Best-effort:
used to surface analytics/transport payloads that are otherwise opaque blobs.
"""
from __future__ import annotations

from typing import Any


def _read_varint(buf: bytes, i: int):
    result = shift = 0
    while i < len(buf):
        b = buf[i]; i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            break
    return None, i


def decode(buf: bytes, depth: int = 0) -> dict | None:
    """Decode protobuf bytes into {field_num: value(s)}. Returns None if not valid."""
    if not buf or depth > 6:
        return None
    out: dict[int, Any] = {}
    i = 0
    n = len(buf)
    while i < n:
        tag, i = _read_varint(buf, i)
        if tag is None:
            return None
        field, wt = tag >> 3, tag & 7
        if field == 0:
            return None
        if wt == 0:                                   # varint
            val, i = _read_varint(buf, i)
            if val is None:
                return None
        elif wt == 1:                                 # 64-bit
            if i + 8 > n:
                return None
            val = int.from_bytes(buf[i:i + 8], "little"); i += 8
        elif wt == 5:                                 # 32-bit
            if i + 4 > n:
                return None
            val = int.from_bytes(buf[i:i + 4], "little"); i += 4
        elif wt == 2:                                 # length-delimited
            ln, i = _read_varint(buf, i)
            if ln is None or i + ln > n:
                return None
            chunk = buf[i:i + ln]; i += ln
            nested = decode(chunk, depth + 1)
            if nested is not None:
                val = nested
            else:
                try:
                    s = chunk.decode("utf-8")
                    val = s if s.isprintable() else chunk.hex()
                except UnicodeDecodeError:
                    val = chunk.hex()
        else:
            return None                               # unsupported wire type
        out.setdefault(field, [])
        out[field].append(val)
    # collapse single-element lists for readability
    return {k: (v[0] if len(v) == 1 else v) for k, v in out.items()}
