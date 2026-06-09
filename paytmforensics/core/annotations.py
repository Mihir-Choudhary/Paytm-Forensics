"""Analyst annotations: flags ("exhibit list") and notes per record.

Stored in a SEPARATE sidecar database (`annotations.db`, next to case.db) so the
case artifacts produced by the pipeline stay byte-stable and the GUI can keep
opening case.db strictly read-only. Annotations are analyst work product, not
evidence — they never modify parsed records, and every change is appended to
the case's hash-chained audit log when one is available.

Records are identified by their provenance (source file, table, rowid, byte
offset, origin) plus domain — stable across reopenings of the same case.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional


def record_key(rec: dict) -> str:
    """Stable identity for a parsed record (provenance + domain).

    Correlation-derived records (e.g. entities) all share degenerate provenance
    ("(correlation)", no rowid/offset) — for those a fingerprint of the record
    content is appended so distinct records never share one annotation. Content
    is deterministic across rebuilds of the same case (EV-6), so keys are too.
    """
    p = rec.get("provenance") or {}
    base = [rec.get("domain"), p.get("source_file"), p.get("source_table"),
            p.get("rowid"), p.get("byte_offset"), p.get("origin")]
    if p.get("rowid") is None and p.get("byte_offset") is None:
        body = {k: v for k, v in rec.items() if k not in ("provenance", "raw")}
        digest = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        base.append(digest[:16])
    return json.dumps(base, ensure_ascii=False)


class AnnotationStore:
    """CRUD for flags + notes, keyed by record_key()."""

    def __init__(self, case_dir: str, audit=None):
        self.path = os.path.join(case_dir, "annotations.db")
        self.audit = audit
        self.con = sqlite3.connect(self.path)
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS annotations (
                   record_key TEXT PRIMARY KEY,
                   domain     TEXT,
                   flagged    INTEGER NOT NULL DEFAULT 0,
                   note       TEXT NOT NULL DEFAULT '',
                   updated_utc TEXT NOT NULL
               )"""
        )
        self.con.commit()

    # ------------------------------------------------------------------ read #
    def get(self, rec: dict) -> tuple[bool, str]:
        row = self.con.execute(
            "SELECT flagged, note FROM annotations WHERE record_key=?",
            (record_key(rec),)).fetchone()
        return (bool(row[0]), row[1]) if row else (False, "")

    def is_flagged(self, rec: dict) -> bool:
        return self.get(rec)[0]

    def count_flagged(self) -> int:
        return self.con.execute(
            "SELECT COUNT(*) FROM annotations WHERE flagged=1").fetchone()[0]

    def flagged_keys(self) -> set[str]:
        return {r[0] for r in self.con.execute(
            "SELECT record_key FROM annotations WHERE flagged=1")}

    # ----------------------------------------------------------------- write #
    def _upsert(self, rec: dict, flagged: Optional[bool], note: Optional[str]):
        key = record_key(rec)
        cur_flag, cur_note = self.get(rec)
        new_flag = cur_flag if flagged is None else bool(flagged)
        new_note = cur_note if note is None else note
        if not new_flag and not new_note:
            self.con.execute("DELETE FROM annotations WHERE record_key=?", (key,))
        else:
            self.con.execute(
                "INSERT INTO annotations (record_key, domain, flagged, note, updated_utc) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(record_key) DO UPDATE SET "
                "flagged=excluded.flagged, note=excluded.note, "
                "updated_utc=excluded.updated_utc",
                (key, rec.get("domain"), int(new_flag), new_note,
                 datetime.now(timezone.utc).isoformat()))
        self.con.commit()
        if self.audit is not None:
            try:
                self.audit.log("annotate", {
                    "record_key": key, "flagged": new_flag,
                    "note_len": len(new_note),   # length only — notes may quote PII
                })
            except Exception:
                pass

    def set_flag(self, rec: dict, flagged: bool) -> None:
        self._upsert(rec, flagged=flagged, note=None)

    def set_note(self, rec: dict, note: str) -> None:
        self._upsert(rec, flagged=None, note=note)

    def annotate_export(self, rec: dict) -> dict:
        """Record + its annotation merged, for flagged-set exports."""
        flagged, note = self.get(rec)
        out = dict(rec)
        out["annotation"] = {"flagged": flagged, "note": note}
        return out

    def close(self) -> None:
        self.con.close()
