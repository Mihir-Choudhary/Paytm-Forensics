"""Read-only SQLite access (EV-1).

Two access modes, both of which guarantee the evidence file is never written:

1. ``open_ro(path)`` — opens the source directly with ``mode=ro&immutable=1``. SQLite is
   forbidden from writing, journaling or replaying a WAL against the source. This is the
   safest possible open, but ``immutable=1`` also makes SQLite **ignore any ``-wal``
   sidecar**, so rows committed to the write-ahead log but not yet checkpointed are
   invisible, and rows the WAL *deleted* still appear.

2. ``open_with_wal(path)`` — when a non-empty ``-wal`` exists, copies the database and its
   ``-wal``/``-shm`` sidecars to a scratch directory and opens *the copy* with ``mode=ro``
   (no ``immutable``), so SQLite applies the WAL and the true current state is read. The
   source is only read; its SHA-256 is captured before and after the copy and compared, so
   any change is detected. This is the "operate on a working copy" path that PRD EV-1
   explicitly sanctions.

``open_best(path)`` picks (2) when a WAL is present and (1) otherwise, and reports which
was used so provenance can record it.

Schema introspection and row iteration tolerate malformed databases (NFR-3).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from urllib.parse import quote

_SIDECARS = ("-wal", "-shm")


def wal_path(path: str) -> str | None:
    """Return the path of a NON-EMPTY -wal sidecar, else None."""
    w = path + "-wal"
    try:
        return w if os.path.getsize(w) > 0 else None
    except OSError:
        return None


def has_wal(path: str) -> bool:
    return wal_path(path) is not None


def _sha256(path: str) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


@contextmanager
def open_ro(path: str):
    """Yield a read-only, immutable connection to the SOURCE file. WAL is ignored."""
    uri = f"file:{quote(path)}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.text_factory = lambda b: b.decode("utf-8", errors="replace")
    try:
        yield con
    finally:
        con.close()


@contextmanager
def open_with_wal(path: str):
    """Yield a connection to a scratch COPY of the db with its WAL applied.

    The source is only ever read. Its SHA-256 is captured before and after the copy and
    compared; a mismatch raises, because that would mean the evidence changed under us.
    The scratch copy is always removed.
    """
    before = _sha256(path)
    tmp = tempfile.mkdtemp(prefix="ptmf_wal_")
    try:
        base = os.path.basename(path)
        shutil.copy2(path, os.path.join(tmp, base))
        for suf in _SIDECARS:
            if os.path.exists(path + suf):
                shutil.copy2(path + suf, os.path.join(tmp, base + suf))
        if _sha256(path) != before:
            raise RuntimeError(f"source changed while copying: {path}")
        cp = os.path.join(tmp, base)
        # no immutable -> SQLite applies the WAL. Any write lands on the COPY.
        con = sqlite3.connect(f"file:{quote(cp)}?mode=ro", uri=True)
        con.text_factory = lambda b: b.decode("utf-8", errors="replace")
        try:
            yield con
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def open_best(path: str):
    """Open a database the most complete way that is still read-only.

    Yields ``(connection, mode)`` where mode is ``"wal_applied"`` or ``"immutable"``.
    Falls back to the immutable open if the copy path fails for any reason (NFR-3).
    """
    if has_wal(path):
        try:
            with open_with_wal(path) as con:
                yield con, "wal_applied"
                return
        except (OSError, sqlite3.DatabaseError, RuntimeError):
            pass
    with open_ro(path) as con:
        yield con, "immutable"


def wal_delta(path: str) -> dict:
    """Per-table row COUNTS visible WITHOUT vs WITH the WAL applied.

    Returns ``{"tables": {name: (immutable_count, true_count)}, "net_hidden": n,
    "net_deleted": n}``.

    IMPORTANT — these are **net row-count differences per table**, not a row-level diff.
    A table where the WAL both inserts 5 rows and deletes 1 reports ``net_hidden = 4``,
    not "5 hidden and 1 deleted". The per-table before/after counts are the honest,
    verifiable figures; the totals only summarise them. Do not read ``net_deleted`` as
    "exactly N rows were deleted" — read it as "an immutable-only read would over-report
    this table by N rows".
    """
    out: dict = {"net_hidden": 0, "net_deleted": 0, "tables": {}}
    if not has_wal(path):
        return out
    try:
        with open_with_wal(path) as con:
            truth = {}
            for t in list_tables(con):
                try:
                    truth[t] = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                except sqlite3.DatabaseError:
                    truth[t] = None
        with open_ro(path) as con:
            seen_tables = set(list_tables(con))
            for t, actual in truth.items():
                if actual is None:
                    continue
                seen = 0
                if t in seen_tables:
                    try:
                        seen = con.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                    except sqlite3.DatabaseError:
                        seen = 0
                if seen != actual:
                    out["tables"][t] = (seen, actual)
                    if actual > seen:
                        out["net_hidden"] += actual - seen
                    else:
                        out["net_deleted"] += seen - actual
    except (OSError, sqlite3.DatabaseError, RuntimeError):
        return out
    return out


def list_tables(con: sqlite3.Connection) -> list[str]:
    try:
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    except sqlite3.DatabaseError:
        return []
    skip = {"android_metadata", "room_master_table", "sqlite_sequence"}
    return [r[0] for r in cur.fetchall() if r[0] not in skip]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    try:
        cur = con.execute(f"PRAGMA table_info('{table}')")
        return [r[1] for r in cur.fetchall()]
    except sqlite3.DatabaseError:
        return []


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
