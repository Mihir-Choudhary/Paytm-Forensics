"""Deterministic JSON / CSV exporters (FR-G7).

Output ordering is stable so exports are reproducible (EV-6). Nested values (lists/dicts)
are serialised as compact JSON within CSV cells.
"""
from __future__ import annotations

import csv
import json
from typing import Iterable


def _flatten_cell(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return "" if v is None else v


def export_json(rows: list[dict], path: str) -> int:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False, sort_keys=True)
    return len(rows)


def export_csv(rows: list[dict], path: str) -> int:
    # stable column set: union of top-level keys (minus raw), provenance flattened
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k in ("raw",):
                continue
            if k == "provenance":
                for pk in ("source_file", "source_table", "rowid", "byte_offset",
                           "origin", "confidence", "ingest_sha256"):
                    name = f"prov.{pk}"
                    if name not in cols:
                        cols.append(name)
                continue
            if k not in cols:
                cols.append(k)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            row = []
            for c in cols:
                if c.startswith("prov."):
                    row.append(_flatten_cell(r.get("provenance", {}).get(c[5:])))
                else:
                    row.append(_flatten_cell(r.get(c)))
            w.writerow(row)
    return len(rows)


def export(rows: Iterable[dict], path: str, fmt: str) -> int:
    rows = list(rows)
    if fmt == "json":
        return export_json(rows, path)
    if fmt == "csv":
        return export_csv(rows, path)
    raise ValueError(f"unsupported export format: {fmt}")
