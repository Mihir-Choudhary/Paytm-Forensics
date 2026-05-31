"""Read-only SQLite access (EV-1).

Opens databases with mode=ro&immutable=1 so the engine never writes to the source file
(no journal, no WAL replay against evidence). Provides schema introspection and safe row
iteration that tolerates malformed databases (EV / NFR-3).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from urllib.parse import quote


@contextmanager
def open_ro(path: str):
    """Yield a read-only, immutable sqlite3 connection. Bytes decoded leniently."""
    uri = f"file:{quote(path)}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        yield con
    finally:
        con.close()


def list_tables(con: sqlite3.Connection) -> list[str]:
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    skip = {"android_metadata", "room_master_table", "sqlite_sequence"}
    return [r[0] for r in cur.fetchall() if r[0] not in skip]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    cur = con.execute(f"PRAGMA table_info('{table}')")
    return [r[1] for r in cur.fetchall()]


def rows(con: sqlite3.Connection, table: str):
    """Yield (rowid, dict) per row. Reads by column name; tolerant of errors."""
    try:
        cols = columns(con, table)
        # request rowid explicitly (works for normal rowid tables)
        try:
            cur = con.execute(f"SELECT rowid, * FROM '{table}'")
            has_rowid = True
        except sqlite3.OperationalError:
            cur = con.execute(f"SELECT * FROM '{table}'")
            has_rowid = False
        for r in cur:
            if has_rowid:
                rowid = r[0]
                values = r[1:]
            else:
                rowid = None
                values = r
            yield rowid, dict(zip(cols, values))
    except sqlite3.DatabaseError:
        return


def get_first(con: sqlite3.Connection, table: str, col: str, where: str | None = None):
    q = f"SELECT {col} FROM '{table}'"
    if where:
        q += f" WHERE {where}"
    q += " LIMIT 1"
    try:
        cur = con.execute(q)
        row = cur.fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
