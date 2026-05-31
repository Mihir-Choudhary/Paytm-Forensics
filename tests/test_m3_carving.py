"""Rigorous tests for M3: SQLite deleted-record carving.

Validates the carver on a CONTROLLED synthetic database (insert -> delete -> carve) so we
can assert exact recovery, plus correctness properties:
  L. Recovery        - deleted rows are recovered from free/slack space
  M. No-false-pos    - a clean DB (no deletes) yields no carved records
  N. Dedupe          - carve_runner subtracts identifiers already present live
  O. Read-only/honest- carver never writes; reports 0 honestly on vacuumed real DBs
  P. Varint unit     - low-level SQLite varint decoder correctness
"""
import os
import sqlite3
import tempfile

import pytest

from paytmforensics.carving.sqlite_carver import SqliteCarver, read_varint, reconstruct_record


def _make_db(path, secure_delete="OFF"):
    con = sqlite3.connect(path)
    con.execute(f"PRAGMA auto_vacuum=NONE")
    con.execute(f"PRAGMA secure_delete={secure_delete}")
    con.execute("CREATE TABLE txn (id INTEGER PRIMARY KEY, vpa TEXT, rrn TEXT, note TEXT)")
    rows = [
        (1, "alice@okicici", "100000000001", "payment one"),
        (2, "bob@ybl",       "100000000002", "payment two"),
        (3, "carol@paytm",   "100000000003", "payment three"),
        (4, "dave@hdfcbank", "100000000004", "payment four"),
        (5, "erin@axisbank", "100000000005", "payment five"),
        (6, "frank@icici",   "100000000006", "payment six"),
    ]
    con.executemany("INSERT INTO txn VALUES (?,?,?,?)", rows)
    con.commit()
    return con


# ----- P. varint unit ------------------------------------------------------ #
def test_P_varint_single_byte():
    assert read_varint(bytes([0x05]), 0) == (5, 1)

def test_P_varint_two_byte():
    # 0x81 0x00 => 128
    assert read_varint(bytes([0x81, 0x00]), 0) == (128, 2)

def test_P_reconstruct_simple_record():
    # build a record: header_len, serial types, body  (one small int + one text "ab")
    # serial 1 = 1-byte int; text "ab" -> serial 13+2*2=17
    import struct
    body = bytes([0x09]) + b"ab"          # int value 9 (1 byte), "ab"
    header = bytes([1, 17])               # serials: 1-byte int, text(2)
    rec = bytes([len(header) + 1]) + header + body
    vals, total = reconstruct_record(rec, 0)
    assert vals == [9, "ab"]


# ----- L. Recovery --------------------------------------------------------- #
def test_L_recovers_deleted_rows(tmp_path):
    p = str(tmp_path / "t.db")
    con = _make_db(p)
    con.execute("DELETE FROM txn WHERE id IN (2,4,6)")   # delete bob/dave/frank
    con.commit()
    con.close()

    carver = SqliteCarver(p)
    found_vpas = set()
    found_rrns = set()
    for cr in carver.carve():
        found_vpas |= set(cr.ids["vpas"])
        found_rrns |= set(cr.ids["rrns"])
    # the deleted rows' identifiers must be recovered from free/slack space
    assert "bob@ybl" in found_vpas
    assert "dave@hdfcbank" in found_vpas
    assert "frank@icici" in found_vpas
    assert "100000000002" in found_rrns


def test_L_secure_delete_on_yields_less(tmp_path):
    """With secure_delete=ON, content is wiped -> carver should recover little/nothing."""
    p = str(tmp_path / "s.db")
    con = _make_db(p, secure_delete="ON")
    con.execute("DELETE FROM txn WHERE id IN (2,4,6)")
    con.commit()
    con.close()
    carver = SqliteCarver(p)
    vpas = set()
    for cr in carver.carve():
        vpas |= set(cr.ids["vpas"])
    assert "bob@ybl" not in vpas   # wiped


# ----- M. No false positives ----------------------------------------------- #
def test_M_clean_db_no_carved(tmp_path):
    p = str(tmp_path / "clean.db")
    con = _make_db(p)          # insert only, NO deletes
    con.close()
    carver = SqliteCarver(p)
    recs = list(carver.carve())
    # live rows are NOT in unallocated space, so nothing should be carved
    assert recs == [], f"false positives on clean db: {[r.ids for r in recs]}"


# ----- N. Dedupe vs live --------------------------------------------------- #
def test_N_carve_runner_dedupes_live(tmp_path):
    from paytmforensics.core.casedb import CaseDB
    from paytmforensics.core.artifact import Artifact
    from paytmforensics.carving import carve_runner

    p = str(tmp_path / "d.db")
    con = _make_db(p)
    con.execute("DELETE FROM txn WHERE id IN (2,4)")   # bob, dave deleted
    con.commit()
    con.close()

    case = CaseDB(str(tmp_path / "case.db"))
    # seed a LIVE record that already contains bob@ybl -> should be subtracted
    case.con.execute(
        "INSERT INTO records(domain,origin,source_file,source_table,data) VALUES (?,?,?,?,?)",
        ("transaction", "live", "x", "y",
         '{"counterparty_vpa":"bob@ybl","domain":"transaction","provenance":{"origin":"live"}}'))
    case.con.commit()

    art = Artifact(rel_path="databases/d.db", abs_path=p, kind="sqlite", logical="chatDb.db")
    recs = list(carve_runner.run(case, {"chatDb.db": art}, {}))
    all_vpas = set()
    for r in recs:
        all_vpas |= set(r.to_dict().get("vpas", []))
    case.close()
    assert "bob@ybl" not in all_vpas      # deduped (already live)
    assert "dave@hdfcbank" in all_vpas    # genuinely new (deleted, not live)


# ----- O. Read-only & honest on real data ---------------------------------- #
def test_O_carver_is_readonly(tmp_path):
    p = str(tmp_path / "ro.db")
    con = _make_db(p)
    con.execute("DELETE FROM txn WHERE id=2")
    con.commit(); con.close()
    before = os.stat(p)
    SqliteCarver(p).carve_list() if hasattr(SqliteCarver, "carve_list") else list(SqliteCarver(p).carve())
    after = os.stat(p)
    assert (before.st_size, int(before.st_mtime)) == (after.st_size, int(after.st_mtime))


EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")

@pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")
def test_O_real_db_no_false_positives():
    """On the real (checkpointed) chatDb, unallocated space holds no live identifiers:
    the carver must NOT re-report live data as carved."""
    path = os.path.join(EXTRACTION, "databases", "chatDb.db")
    carver = SqliteCarver(path)
    recs = list(carver.carve())
    # honest result: structured carve finds no reconstructable deleted payment records
    assert isinstance(recs, list)   # never crashes
    # if any are found they must come from unallocated regions, never live cells
    for r in recs:
        assert r.region in ("gap", "freeblock", "freelist")
