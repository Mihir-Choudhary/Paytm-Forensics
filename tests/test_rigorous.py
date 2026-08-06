"""Rigorous validation suite for PaytmForensics.

Categories:
  A. Completeness   - parser output == ground-truth raw SQL counts (no data loss)
  B. Accuracy       - specific known values are extracted correctly
  C. Decoding       - timestamp / enum / vpa unit correctness
  D. Integrity      - byte-level read-only proof (sha+size+mtime, no new files)
  E. Reproducibility- canonical export is byte-identical across runs
  F. Provenance     - every record fully attributed + ingest hash present
  G. Encoding       - ₹ / emoji / Indian-language text preserved
  H. Robustness     - corrupt / truncated / empty / missing DBs never crash the run

Run: pytest paytmforensics/tests/test_rigorous.py -v
"""
import hashlib
import json
import os
import sqlite3
import struct
import tempfile
import time

import pytest

from paytmforensics.core.case import Case
from paytmforensics.core import integrity, artifact
from paytmforensics.ingest import sqlite_ro as sql
from paytmforensics.enrich import timestamps, enums, vpa, errorcodes

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION",
    r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm",
)
DBDIR = os.path.join(EXTRACTION, "databases")

pytestmark = pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")


# --------------------------------------------------------------------------- #
#  Shared fixture: run a full case once, expose case.db + domain records
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def case_out():
    out = tempfile.mkdtemp(prefix="ptmfx_")
    case = Case(EXTRACTION, out, case_id="RIG")
    t0 = time.time()
    case.ingest()
    results = case.parse_all()
    case.build_timeline()
    elapsed = time.time() - t0
    summ = case.summary()
    case.close()
    return {"out": out, "results": results, "summary": summ, "elapsed": elapsed}


def _records(out, domain):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,))]
    con.close()
    return rows



# NOTE (audit finding F-02): these helpers previously read through `sql.open_ro`, the same
# WAL-blind reader the parsers used, so expected and actual shared a blind spot and the
# assertions held whether or not WAL-resident rows were being dropped. Ground truth now
# comes from a WAL-aware read. Residual coupling is acknowledged: both sides now use the
# same *library*, so the decisive WAL tests use literal expected values against a
# synthetic fixture instead -- see tests/test_audit_regressions.py.
def _raw_count(db, table, where=None):
    path = os.path.join(DBDIR, db)
    if not os.path.exists(path):
        return None
    with sql.open_with_wal(path) if sql.has_wal(path) else sql.open_ro(path) as con:
        if table not in sql.list_tables(con):
            return 0
        q = f"SELECT COUNT(*) FROM '{table}'"
        if where:
            q += f" WHERE {where}"
        return con.execute(q).fetchone()[0]


# =========================================================================== #
#  A. COMPLETENESS  (no data loss vs ground truth)
# =========================================================================== #
def test_A_passbook_count(case_out):
    assert case_out["results"].get("transactions.passbook") == _raw_count("passbook.db", "UthListingEntity")

def test_A_chat_message_count(case_out):
    assert case_out["results"].get("chats") == _raw_count("chatDb.db", "ChatMessageEntity")

def test_A_contacts_count(case_out):
    # contacts.users = all TBL_USERS except the subject (isMe=1)
    total = _raw_count("chatDb.db", "TBL_USERS")
    me = _raw_count("chatDb.db", "TBL_USERS", "isMe=1")
    assert case_out["results"].get("contacts.users") == total - me

def test_A_vpa_cache_count(case_out):
    assert case_out["results"].get("contacts.vpa_cache") == _raw_count("cache_database", "cache_table")

def test_A_consent_count(case_out):
    assert case_out["results"].get("consents") == _raw_count("ups_database", "ConsentTable")

def test_A_identity_exactly_one_subject(case_out):
    subjects = [r for r in _records(case_out["out"], "person") if r.get("is_subject")]
    assert len(subjects) == 1

def test_A_signal_locations_match_ground_truth(case_out):
    # count location_event rows in bank_signal independently
    path = os.path.join(DBDIR, "bank_signal")
    expected = 0
    with sql.open_with_wal(path) if sql.has_wal(path) else sql.open_ro(path) as con:
        for _rid, r in sql.rows(con, "SignalEventDb"):
            ev = r.get("signalEvent") or ""
            if '"eventType":"location_event"' in ev:
                expected += 1
    got = case_out["results"].get("location.signal")
    assert got == expected, f"signal locations {got} != {expected}"


# =========================================================================== #
#  B. ACCURACY  (known values)
# =========================================================================== #
def test_B_subject_identity_values(case_out):
    from . import _truth
    subj = [r for r in _records(case_out["out"], "person") if r.get("is_subject")][0]
    assert subj["customer_id"] == _truth.require("subject.customer_id")
    assert subj["phone"] == _truth.require("subject.phone")
    assert subj["name"] == _truth.require("subject.name")

def test_B_known_rrn_present(case_out):
    # chat payments (with RRN) live in the message domain, not transaction
    from . import _truth
    expected = _truth.require("known_rrn_any")
    rrns = {r.get("rrn") for r in _records(case_out["out"], "message") if r.get("rrn")}
    assert expected in rrns

def test_B_known_merchant_txn_decoded(case_out):
    from . import _truth
    vpa_val = _truth.require("known_merchant.vpa")
    txns = _records(case_out["out"], "transaction")
    hits = [t for t in txns if t.get("counterparty_vpa") == vpa_val]
    assert hits, f"known merchant txn ({vpa_val}) missing"
    t = hits[0]
    assert t.get("direction") in ("credit", "debit")
    assert t.get("status_label")               # decoded, not raw
    assert t.get("instrument")                 # bank name resolved
    assert isinstance(t.get("amount"), (int, float))

def test_B_location_fix_present(case_out):
    locs = _records(case_out["out"], "location")
    coords = [(l["latitude"], l["longitude"]) for l in locs
              if l.get("latitude") is not None and l.get("longitude") is not None]
    assert coords, "no decoded lat/lon pairs in location domain"

def test_B_cookie_location_has_pincode(case_out):
    locs = _records(case_out["out"], "location")
    cookie = [l for l in locs if l.get("source_kind") == "cookie"]
    assert cookie and cookie[0].get("pincode")    # parsed, not None


# =========================================================================== #
#  C. DECODING  (pure unit tests)
# =========================================================================== #
def test_C_unix_ms_decode():
    tv = timestamps.decode(1723742756754)
    assert tv.epoch_type == "unix_ms"
    assert tv.utc_iso.startswith("2024-08-15")

def test_C_webkit_us_decode():
    # 1775111945 unix-seconds expressed as webkit microseconds
    webkit = (1775111945 + 11644473600) * 1_000_000
    tv = timestamps.decode(webkit, hint="webkit_us")
    assert tv.epoch_type == "webkit_us"
    assert tv.utc_iso.startswith("2026-")

def test_C_invalid_timestamp_preserved():
    tv = timestamps.decode(0)
    assert tv.raw == 0 and tv.utc_iso is None

def test_C_enum_decoding():
    assert enums.txn_direction(1) == "credit"
    assert enums.txn_direction(2) == "debit"
    assert enums.txn_status(2) == "success"
    assert enums.work_state(0) == "ENQUEUED"
    assert enums.txn_direction(99) == "code:99"   # unknown -> labelled, not guessed

def test_C_vpa_psp_parse():
    assert vpa.split("upiswiggy@icici") == ("upiswiggy", "icici")
    assert "ICICI" in vpa.psp_name("upiswiggy@icici")
    assert vpa.looks_like_merchant("paytm-test1234@ptybl") is True

def test_C_errorcode_table_loaded():
    assert errorcodes.message(1103) and "passcode" in errorcodes.message(1103).lower()


# =========================================================================== #
#  D. INTEGRITY  (byte-level read-only proof)
# =========================================================================== #
def _deep_snapshot(root):
    """sha256 + size + mtime for every file."""
    snap = {}
    for dp, _d, files in os.walk(root):
        for n in files:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root)
            st = os.stat(p)
            sha, _md5, _sz = integrity.hash_file(p)
            snap[rel] = (sha, st.st_size, int(st.st_mtime))
    return snap

def test_D_source_byte_identical_after_run():
    before = _deep_snapshot(EXTRACTION)
    out = tempfile.mkdtemp(prefix="ptmfx_ro_")
    c = Case(EXTRACTION, out, case_id="RO")
    c.ingest(); c.parse_all(); c.build_timeline(); c.close()
    after = _deep_snapshot(EXTRACTION)
    assert before.keys() == after.keys(), "files added/removed in source!"
    diffs = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    assert not diffs, f"source bytes/mtime changed: {diffs}"

def test_D_no_sidecar_written_to_source():
    # tool must not create -wal/-shm on the read-only source while open
    out = tempfile.mkdtemp(prefix="ptmfx_sc_")
    before = set(os.listdir(DBDIR))
    c = Case(EXTRACTION, out, case_id="SC")
    c.ingest(); c.parse_all(); c.close()
    after = set(os.listdir(DBDIR))
    assert after == before, f"new files in source db dir: {after - before}"


# =========================================================================== #
#  E. REPRODUCIBILITY  (canonical export byte-identical)
# =========================================================================== #
def _canonical(out):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = []
    for (data,) in con.execute("SELECT data FROM records"):
        d = json.loads(data)
        rows.append(json.dumps(d, sort_keys=True, ensure_ascii=False))
    con.close()
    rows.sort()
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()

def test_E_canonical_export_stable():
    o1 = tempfile.mkdtemp(prefix="ptmfx_r1_")
    o2 = tempfile.mkdtemp(prefix="ptmfx_r2_")
    for o in (o1, o2):
        c = Case(EXTRACTION, o, case_id="REP")
        c.ingest(); c.parse_all(); c.build_timeline(); c.close()
    assert _canonical(o1) == _canonical(o2)


# =========================================================================== #
#  F. PROVENANCE
# =========================================================================== #
def test_F_every_record_attributed(case_out):
    con = sqlite3.connect(os.path.join(case_out["out"], "case.db"))
    bad = 0
    for (data,) in con.execute("SELECT data FROM records"):
        d = json.loads(data)
        p = d.get("provenance", {})
        if not p.get("source_file"):
            bad += 1
    con.close()
    assert bad == 0

def test_F_live_records_have_ingest_hash(case_out):
    # parsed live records should carry the source file's sha256
    con = sqlite3.connect(os.path.join(case_out["out"], "case.db"))
    missing = 0
    for (data,) in con.execute(
            "SELECT data FROM records WHERE domain IN ('transaction','message','person','location')"):
        d = json.loads(data)
        if d["provenance"]["origin"] == "live" and not d["provenance"].get("ingest_sha256"):
            missing += 1
    con.close()
    assert missing == 0


# =========================================================================== #
#  G. ENCODING
# =========================================================================== #
def test_G_rupee_and_emoji_preserved(case_out):
    # ₹ appears in chat message content; emoji tags appear on passbook transactions
    msgs = _records(case_out["out"], "message")
    assert any("₹" in (m.get("content") or "") for m in msgs), "₹ sign lost"
    txns = _records(case_out["out"], "transaction")
    assert any("🥘" in (t.get("tag") or "") for t in txns), "emoji tag lost"


# =========================================================================== #
#  H. ROBUSTNESS  (corrupt / truncated / empty / missing must not crash)
# =========================================================================== #
def _make_fake_extraction(tmp):
    dbd = os.path.join(tmp, "databases")
    os.makedirs(dbd, exist_ok=True)
    return dbd

def test_H_empty_file_db(tmp_path):
    dbd = _make_fake_extraction(str(tmp_path))
    open(os.path.join(dbd, "passbook.db"), "wb").close()        # 0-byte
    c = Case(str(tmp_path), str(tmp_path / "out"), case_id="EMPTY")
    c.ingest(); res = c.parse_all(); c.close()
    assert res.get("transactions.passbook", 0) in (0, None)     # no crash, no rows

def test_H_truncated_sqlite(tmp_path):
    dbd = _make_fake_extraction(str(tmp_path))
    # valid header then garbage (truncated DB)
    with open(os.path.join(dbd, "chatDb.db"), "wb") as f:
        f.write(b"SQLite format 3\x00" + os.urandom(200))
    c = Case(str(tmp_path), str(tmp_path / "out"), case_id="TRUNC")
    c.ingest(); res = c.parse_all(); c.close()
    # parsers tolerate; either 0 or graceful error flag, never an uncaught exception
    assert all(v == 0 or v == -1 or v is None or v >= 0 for v in res.values())

def test_H_valid_db_missing_tables(tmp_path):
    dbd = _make_fake_extraction(str(tmp_path))
    p = os.path.join(dbd, "ups_database")
    con = sqlite3.connect(p); con.execute("CREATE TABLE unrelated(x)"); con.commit(); con.close()
    c = Case(str(tmp_path), str(tmp_path / "out"), case_id="NOTBL")
    c.ingest(); res = c.parse_all(); c.close()
    assert res.get("consents", 0) in (0, None)

def test_H_missing_everything(tmp_path):
    os.makedirs(str(tmp_path / "databases"), exist_ok=True)
    c = Case(str(tmp_path), str(tmp_path / "out"), case_id="NONE")
    c.ingest(); res = c.parse_all(); n = c.build_timeline(); c.close()
    assert res == {} or all(v in (0, None) for v in res.values())
    assert n == 0

def test_H_garbage_xml_prefs(tmp_path):
    sp = os.path.join(str(tmp_path), "shared_prefs"); os.makedirs(sp, exist_ok=True)
    with open(os.path.join(sp, "bank_secure_prefs.xml"), "w", encoding="utf-8") as f:
        f.write("<<<not valid xml >>>")
    os.makedirs(str(tmp_path / "databases"), exist_ok=True)
    c = Case(str(tmp_path), str(tmp_path / "out"), case_id="BADXML")
    c.ingest(); res = c.parse_all(); c.close()   # must not raise
    assert True
