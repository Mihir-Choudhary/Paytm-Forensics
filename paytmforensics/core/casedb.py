"""Case database: a separate SQLite store for parsed/correlated results.

Kept entirely apart from the evidence (never written back to source). Backs the GUI grids
and exports. One generic 'records' table keyed by domain keeps the schema simple and the
exports reproducible (stable ordering by domain, then insertion order).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from .models import Record


class CaseDB:
    def __init__(self, path: str):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS records (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   domain TEXT NOT NULL,
                   origin TEXT NOT NULL,
                   source_file TEXT,
                   source_table TEXT,
                   data TEXT NOT NULL
               )"""
        )
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_domain ON records(domain)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_origin ON records(origin)")
        # start every case build clean so re-running into the same dir is idempotent
        self.con.execute("DELETE FROM records")
        try:
            self.con.execute("DELETE FROM sqlite_sequence WHERE name='records'")
        except sqlite3.OperationalError:
            pass
        self.con.commit()

    def add(self, rec: Record) -> None:
        d = rec.to_dict()
        self.con.execute(
            "INSERT INTO records (domain, origin, source_file, source_table, data) "
            "VALUES (?,?,?,?,?)",
            (rec.domain, rec.provenance.origin.value, rec.provenance.source_file,
             rec.provenance.source_table, json.dumps(d, ensure_ascii=False)),
        )

    def add_many(self, recs: Iterable[Record]) -> int:
        n = 0
        for r in recs:
            self.add(r)
            n += 1
        self.con.commit()
        return n

    def count_by_domain(self) -> dict:
        cur = self.con.execute("SELECT domain, COUNT(*) FROM records GROUP BY domain ORDER BY domain")
        return {r[0]: r[1] for r in cur.fetchall()}

    def iter_domain(self, domain: str):
        cur = self.con.execute(
            "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,)
        )
        for (data,) in cur:
            yield json.loads(data)

    def close(self) -> None:
        self.con.commit()
        self.con.close()
