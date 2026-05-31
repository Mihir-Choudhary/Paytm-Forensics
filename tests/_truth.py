"""Tiny helper: load tests/_truth.json (gitignored) for PII-value assertions.

The file holds known values from an examiner's real extraction (subject phone,
known RRN, etc.). It is NEVER committed. Tests that need a value call
`get('subject.phone')` — when the file or key is missing, the test is skipped
via `pytest.skip(...)` so a fresh clone still runs the rest of the suite.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PATH = os.path.join(_HERE, "_truth.json")


@lru_cache(maxsize=1)
def _load() -> dict:
    if not os.path.isfile(_PATH):
        return {}
    try:
        with open(_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def get(dotted: str, default=None):
    """Look up a dotted key path. Returns `default` if missing."""
    cur = _load()
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def require(dotted: str):
    """Return the value at `dotted`, or skip the calling test if absent."""
    v = get(dotted)
    if v in (None, "", []) or (isinstance(v, str) and v.startswith("<")):
        pytest.skip(f"tests/_truth.json missing key '{dotted}' — "
                    "see tests/_truth_example.json for the expected schema")
    return v
