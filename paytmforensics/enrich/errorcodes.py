"""Error-code decoding using the bundled error_mapper.json (from the decompiled app).

Maps Paytm error codes to {message, reason, status, httpStatus}. Bundled locally; no
network. Lazy-loaded and cached.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Optional

from ..resources_util import resource_path

_RES = resource_path("error_mapper.json")


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        with open(_RES, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for e in data.get("errors", []):
        code = e.get("code")
        if code is not None:
            out[str(code)] = e
    return out


def lookup(code) -> Optional[dict]:
    if code is None or code == "":
        return None
    return _table().get(str(code))


def message(code) -> Optional[str]:
    e = lookup(code)
    return e.get("errorMessage") if e else None
