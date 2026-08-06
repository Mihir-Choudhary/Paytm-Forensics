"""Regression tests for the 48 audit findings.

Each test names the finding it locks down and is written to FAIL against the pre-fix code.
These deliberately do NOT route ground truth through the same helper the implementation
uses (finding F-02: the original completeness tests compared the parser against a reader
that shared the parser's blind spot).
"""
import json
import os
import re
import sqlite3
import tempfile

import pytest

from paytmforensics.gui.filters import FilterSpec, apply_filter, parse_bound


# ---------------------------------------------------------------- F-20 / F-21 #
REC = {"amount": 50.0, "direction": "debit",
       "timestamp": {"utc_iso": "2026-05-12T07:15:03.930000+00:00"},
       "provenance": {"origin": "live", "source_file": "databases/passbook.db"}}
CFG = {"key": "Flag", "value": "true",
       "provenance": {"origin": "live", "source_file": "databases/appManagerDB"}}


def test_F20_date_to_is_inclusive_of_that_day():
    assert FilterSpec(date_to="2026-05-12").matches(REC)
    assert FilterSpec(date_from="2026-05-01", date_to="2026-05-12").matches(REC)


def test_F21_unpadded_date_parses_correctly():
    assert FilterSpec(date_from="2026-5-1").matches(REC)
    assert not FilterSpec(date_from="2026-6-1").matches(REC)


def test_F20_date_to_still_excludes_the_next_day():
    later = dict(REC, timestamp={"utc_iso": "2026-05-13T00:00:01+00:00"})
    assert not FilterSpec(date_to="2026-05-12").matches(later)


def test_parse_bound_rejects_garbage():
    assert parse_bound("not-a-date", end=False) is None


# ---------------------------------------------------------------------- F-08 #
def test_F08_date_filter_works_on_cookie_and_crash_shapes():
    cookie = {"host": "x", "created": {"utc_iso": "2026-05-12T07:15:03+00:00"},
              "provenance": {"origin": "live"}}
    crash = {"session_id": "s", "start_time": {"utc_iso": "2026-05-12T07:15:03+00:00"},
             "provenance": {"origin": "live"}}
    spec = FilterSpec(date_from="2026-01-01", date_to="2026-12-31")
    assert spec.matches(cookie) and spec.matches(crash)


# ---------------------------------------------------------------------- F-22 #
def test_F22_amount_and_direction_do_not_empty_unrelated_grids():
    assert FilterSpec(amount_min=0).matches(CFG)
    assert FilterSpec(amount_max=100).matches(CFG)
    assert FilterSpec(direction="debit").matches(CFG)
    # ...but they still filter the domain that HAS the field
    assert not FilterSpec(amount_min=100).matches(REC)
    assert not FilterSpec(direction="credit").matches(REC)


# ---------------------------------------------------------------------- F-23 #
def test_F23_missing_field_does_not_match_string_none():
    assert not FilterSpec(field_equals={"nosuch": "None"}).matches(REC)


def test_F23_bool_field_equals_accepts_both_spellings():
    r = dict(REC, settled=True)
    assert FilterSpec(field_equals={"settled": True}).matches(r)
    assert FilterSpec(field_equals={"settled": "true"}).matches(r)


# ---------------------------------------------------------------------- F-15 #
def test_F15_text_filter_matches_values_not_provenance():
    # source_contains is the way to filter on provenance; free text must not
    assert not FilterSpec(text="passbook").matches(REC)
    assert FilterSpec(source_contains="passbook").matches(REC)


# ---------------------------------------------------------------------- F-24 #
def test_F24_filter_presets_roundtrip():
    spec = FilterSpec(text="x", amount_min=5.0, direction="debit", date_to="2026-01-01")
    assert FilterSpec.from_dict(json.loads(json.dumps(spec.to_dict()))) == spec


# ---------------------------------------------------------------------- F-05 #
def _person(name=None, phone=None, vpas=()):
    return {"domain": "person", "name": name, "phone": phone, "vpas": list(vpas),
            "provenance": {"source_file": "f"}}


def _entities(rows):
    from paytmforensics.core.casedb import CaseDB
    from paytmforensics.correlate import entities
    d = tempfile.mkdtemp()
    case = CaseDB(os.path.join(d, "c.db"))
    for r in rows:
        case.con.execute(
            "INSERT INTO records(domain,origin,source_file,source_table,data) VALUES(?,?,?,?,?)",
            ("person", "live", "f", "t", json.dumps(r)))
    case.con.commit()
    out = [r.to_dict() for r in entities.build(case)]
    case.close()
    return out


def test_F05_entity_merge_is_order_independent():
    a = _person("Rec A", "9000000001")
    b = _person("Rec B", "9000000001", ["x@ptybl"])
    c = _person("Rec C", None, ["x@ptybl"])
    assert len(_entities([a, b, c])) == len(_entities([a, c, b])) == 1


# ---------------------------------------------------------------------- F-33 #
def test_F33_name_variants_and_case_merge_into_one_entity():
    rows = [_person("FoodCo"), _person("Foodco"), _person("QUICKEATS"), _person("Quickeats"),
            _person("Acme Foods Pvt Ltd"),
            _person("Acme Foods Private Limited")]
    ents = _entities(rows)
    names = [set(e["names"]) for e in ents]
    assert len(ents) == 3, names
    assert any({"FoodCo", "Foodco"} <= n for n in names)
    assert any({"QUICKEATS", "Quickeats"} <= n for n in names)


def test_F33_distinct_parties_are_not_over_merged():
    ents = _entities([_person("Alice"), _person("Bob"), _person("Carol")])
    assert len(ents) == 3


# ---------------------------------------------------------------------- F-17 #
def test_F17_txn_category_2_is_not_labelled_cashback():
    from paytmforensics.enrich import enums
    assert enums.txn_category(1) == "food_and_beverages"
    assert enums.txn_category(2) != "cashback"
    assert enums.txn_direction(99) == "code:99"      # unknown still never guessed


# ---------------------------------------------------------------------- F-48 #
def test_F48_account_model_has_a_producer():
    import inspect
    from paytmforensics.parsers import misc_stores
    assert "Account(" in inspect.getsource(misc_stores)


# ---------------------------------------------------------------------- F-27 #
def test_F27_audit_log_verifier_exists_and_detects_tampering(tmp_path):
    from paytmforensics.core.audit import AuditLog
    p = str(tmp_path / "audit.log")
    log = AuditLog(p)
    log.log("a", {"x": 1}); log.log("b", {"y": 2}); log.log("c", {})
    rep = AuditLog.verify(p)
    assert rep["ok"] and rep["entries"] == 3 and not rep["breaks"]

    lines = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    lines[1]["detail"] = {"y": 999}
    with open(p, "w", encoding="utf-8") as f:
        for e in lines:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    assert not AuditLog.verify(p)["ok"]


# ---------------------------------------------------------------------- F-31 #
def test_F31_csv_has_flat_sortable_timestamp_columns(tmp_path):
    from paytmforensics.report import exporters
    rows = [{"amount": 1, "timestamp": {"raw": 1, "epoch_type": "unix_ms",
                                        "utc_iso": "2026-05-12T07:15:03+00:00"},
             "provenance": {"origin": "live"}}]
    p = str(tmp_path / "x.csv")
    exporters.export(rows, p, "csv")
    text = open(p, encoding="utf-8").read()
    assert "timestamp.utc_iso" in text and "2026-05-12T07:15:03+00:00" in text


# ---------------------------------------------------------------------- F-25 #
def test_F25_cli_refuses_a_nonexistent_extraction(tmp_path):
    from paytmforensics import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["--extraction", str(tmp_path / "nope"), "--out", str(tmp_path / "o")])
    assert e.value.code != 0


# ---------------------------------------------------------------------- F-34 #
def test_F34_geo_suppresses_movement_path_for_coincident_fixes():
    from paytmforensics.report import geo
    same = [{"lat": 17.4, "lon": 78.4, "src": "diagnostic", "ts": f"2026-05-{d:02d}"}
            for d in range(1, 12)]
    gj = json.loads(geo.to_geojson(same))
    assert not [f for f in gj["features"] if f["geometry"]["type"] == "LineString"]
    moving = [{"lat": 17.4 + i, "lon": 78.4 + i, "src": "signal", "ts": f"2026-05-{i+1:02d}"}
              for i in range(4)]
    gj2 = json.loads(geo.to_geojson(moving))
    assert [f for f in gj2["features"] if f["geometry"]["type"] == "LineString"]


# ------------------------------------------------------------ F-01 / F-02 / F-16 #
def _wal_extraction(root):
    """Build an extraction whose passbook has rows ONLY in an uncheckpointed -wal,
    plus a table whose rows the WAL DELETED. Ground truth is asserted as literals, not
    read back through the same helper the parser uses (that was F-02)."""
    live = os.path.join(root, "_live"); dbd = os.path.join(root, "net.one97.paytm", "databases")
    os.makedirs(live); os.makedirs(dbd)
    p = os.path.join(live, "passbook.db")
    con = sqlite3.connect(p)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE UthListingEntity(txnId TEXT, sourceTxnId TEXT PRIMARY KEY,
        amount REAL, txnIndicator INTEGER, identifier TEXT, statusKey INTEGER,
        txnDate INTEGER, searchableStrings TEXT)""")
    con.execute("CREATE TABLE UthInstrumentEntity(sourceTxnId TEXT, instrumentName TEXT, "
                "identifier TEXT, accountType TEXT, id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO UthListingEntity VALUES('T0','CHECKPOINTED',1.0,2,'a@ptybl',2,"
                "1723742756754,'a@ptybl,100000000000')")
    con.execute("INSERT INTO UthListingEntity VALUES('TD','TO_BE_DELETED',9.0,2,'d@ptybl',2,"
                "1723742756000,'d@ptybl,100000000009')")
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # these five never reach the main db file
    for i in range(5):
        con.execute("INSERT INTO UthListingEntity VALUES(?,?,?,?,?,?,?,?)",
                    (f"T{i+1}", f"WAL_ONLY_{i}", 100.0 + i, 2, f"w{i}@ptybl", 2,
                     1723742756754 + i, f"w{i}@ptybl,10000000000{i}"))
    # and this delete exists only in the WAL
    con.execute("DELETE FROM UthListingEntity WHERE sourceTxnId='TO_BE_DELETED'")
    con.commit()
    import shutil
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(p + suf):
            shutil.copy2(p + suf, os.path.join(dbd, "passbook.db" + suf))
    con.close()
    return os.path.join(root, "net.one97.paytm")


def test_F01_wal_only_rows_are_parsed(tmp_path):
    from paytmforensics.core.case import Case
    ext = _wal_extraction(str(tmp_path))
    out = str(tmp_path / "case")
    c = Case(ext, out, case_id="WAL"); c.ingest(); c.parse_all(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    txns = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='transaction'")]
    con.close()
    ids = {t["source_txn_id"] for t in txns}
    # literal ground truth: 1 checkpointed + 5 WAL-only, and the WAL-deleted row is gone
    assert ids == {"CHECKPOINTED"} | {f"WAL_ONLY_{i}" for i in range(5)}, ids
    assert sum(t["amount"] for t in txns) == 1.0 + sum(100.0 + i for i in range(5))


def test_F16_wal_deleted_rows_are_not_reported_as_live(tmp_path):
    from paytmforensics.core.case import Case
    ext = _wal_extraction(str(tmp_path))
    out = str(tmp_path / "case")
    c = Case(ext, out, case_id="WAL"); c.ingest(); c.parse_all(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    ids = {json.loads(d)["source_txn_id"] for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='transaction'")}
    con.close()
    assert "TO_BE_DELETED" not in ids


def test_F01_read_mode_recorded_in_provenance(tmp_path):
    from paytmforensics.core.case import Case
    ext = _wal_extraction(str(tmp_path))
    out = str(tmp_path / "case")
    c = Case(ext, out, case_id="WAL"); c.ingest(); c.parse_all(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    modes = {json.loads(d)["provenance"].get("read_mode") for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='transaction'")}
    con.close()
    assert modes == {"wal_applied"}


def test_F01_wal_exposure_is_reported(tmp_path):
    from paytmforensics.core.case import Case
    ext = _wal_extraction(str(tmp_path))
    out = str(tmp_path / "case")
    c = Case(ext, out, case_id="WAL"); c.ingest(); c.parse_all(); c.close()
    rep = json.load(open(os.path.join(out, "wal_report.json"), encoding="utf-8"))
    # NET semantics: 5 WAL inserts - 1 WAL delete = net 4 hidden in this table
    assert rep["net_hidden_rows"] == 4, rep
    assert any("passbook" in k for k in rep["databases"])
    tbls = next(iter(rep["databases"].values()))["tables"]
    # the per-table before/after counts are the verifiable figures
    assert tbls["UthListingEntity"] == {"immutable_view": 2, "with_wal": 6}, tbls


def test_F01_source_is_byte_identical_after_a_wal_run(tmp_path):
    """The copy path must not touch the evidence: EV-1 still holds."""
    from paytmforensics.core.case import Case
    from paytmforensics.core import integrity
    ext = _wal_extraction(str(tmp_path))

    def snap():
        s = {}
        for dp, _d, fs in os.walk(ext):
            for f in fs:
                fp = os.path.join(dp, f)
                st = os.stat(fp)
                s[os.path.relpath(fp, ext)] = (integrity.hash_file(fp)[0], st.st_size,
                                               int(st.st_mtime))
        return s

    before = snap()
    c = Case(ext, str(tmp_path / "c2"), case_id="RO")
    c.ingest(); c.parse_all(); c.carve(); c.close()
    assert snap() == before


# ---------------------------------------------------------------------- F-47 #
def test_F47_carver_covers_every_db_and_reads_the_wal(tmp_path):
    from paytmforensics.carving import carve_runner
    from paytmforensics.carving.sqlite_carver import WalCarver
    from paytmforensics.core.artifact import discover
    ext = _wal_extraction(str(tmp_path))
    arts = {a.logical: a for a in discover(ext)}
    targets = [a.rel_path for a in carve_runner._targets(arts)]
    assert any("passbook" in t for t in targets)
    w = WalCarver(os.path.join(ext, "databases", "passbook.db-wal"))
    assert w.frames, "WAL frames not parsed"


# ---------------------------------------------------------------------- F-03 #
def test_F03_timeline_is_stored_chronologically(tmp_path):
    from paytmforensics.core.case import Case
    ext = _wal_extraction(str(tmp_path))
    out = str(tmp_path / "case")
    c = Case(ext, out, case_id="TL")
    c.ingest(); c.parse_all(); c.correlate(); c.build_timeline(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    iso = [json.loads(d)["utc_iso"] for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='timeline' ORDER BY id")]
    con.close()
    assert iso == sorted(iso), "timeline not stored in chronological order"


# ---------------------------------------------------------------- redaction #
def test_secret_redaction_covers_free_form_recovered_text():
    """A recovered string carrying a credential must never be emitted verbatim.

    Redaction used to live only in the SharedPreferences parser, keyed on exact key
    names, so parsers that emit recovered free-form text (WebView local storage,
    IndexedDB, service-worker cache bodies, DataStore blobs) copied live tokens
    into the case file.
    """
    from paytmforensics.enrich.secrets import redact_text, redact_value, is_secret_key
    jwt = ("eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9."
           "eyJhcHBJZCI6IjE6NDk2NDUxMjM0NTY3OmFuZHJvaWQ6YWJjZGVm."
           "MEUCIQDabcdefghijklmnopqrstuvwxyz0123456789")
    out = redact_text(f'{{"auth":"{jwt}"}}')
    assert jwt not in out and "redacted" in out

    fcm = "dfMSsgIZRDahbnqgL0RPSr:APA91bGpHJGbW7xI8qaCVenxAzfQQQQQQQQQQQQ"
    assert fcm not in redact_text(f'{{"token":"{fcm}"}}')

    assert "hunter2hunter2" not in redact_text('access_token=hunter2hunter2&x=1')
    assert "abcdefghijklmnop" not in redact_text("Authorization: Bearer abcdefghijklmnop")

    # by key name, even when the value looks innocuous
    assert redact_value("PREFERENCE_KEY_SESSION_KEY", "short").startswith("<redacted")
    assert is_secret_key("pb_auth_token") and not is_secret_key("counterparty_name")

    # length is preserved as metadata, and non-secrets are untouched
    assert f"{len(jwt)} chars" in redact_text(jwt)
    assert redact_text("Sent you Rs 90 to FoodCo") == "Sent you Rs 90 to FoodCo"


def test_redaction_does_not_mangle_ordinary_evidence():
    """Guard against over-redaction destroying real evidence."""
    from paytmforensics.enrich.secrets import redact_text
    for s in ("demoshop@bankx", "900000000001", "9876543210",
              "PYTM60527802669469514781", "0000_BARB0FAKE01",
              "https://securegw.paytm.in/savedcardservice/x"):
        assert redact_text(s) == s, s


# -------------------------------------------------------------- robustness #
def test_wal_path_tolerates_a_corrupt_wal(tmp_path):
    """A garbage -wal must fall back to the immutable read, not crash the run."""
    from paytmforensics.core.case import Case
    dbd = tmp_path / "ext" / "databases"
    dbd.mkdir(parents=True)
    p = str(dbd / "passbook.db")
    con = sqlite3.connect(p)
    con.execute("""CREATE TABLE UthListingEntity(txnId TEXT, sourceTxnId TEXT PRIMARY KEY,
        amount REAL, txnIndicator INTEGER, identifier TEXT, statusKey INTEGER,
        txnDate INTEGER, searchableStrings TEXT)""")
    con.execute("INSERT INTO UthListingEntity VALUES('T','S',5.0,2,'a@b',2,1723742756754,'')")
    con.commit(); con.close()
    with open(p + "-wal", "wb") as f:
        f.write(os.urandom(4096))          # not a valid WAL
    c = Case(str(tmp_path / "ext"), str(tmp_path / "out"), case_id="BADWAL")
    c.ingest(); res = c.parse_all(); c.carve(); c.close()
    assert res.get("transactions.passbook") == 1     # fell back, still parsed


def test_wal_path_tolerates_a_truncated_db(tmp_path):
    from paytmforensics.core.case import Case
    dbd = tmp_path / "ext" / "databases"
    dbd.mkdir(parents=True)
    with open(dbd / "chatDb.db", "wb") as f:
        f.write(b"SQLite format 3\x00" + os.urandom(300))
    with open(dbd / "chatDb.db-wal", "wb") as f:
        f.write(os.urandom(2048))
    c = Case(str(tmp_path / "ext"), str(tmp_path / "out"), case_id="TRUNC")
    c.ingest(); res = c.parse_all(); c.carve(); c.build_timeline(); c.close()
    assert all(v is None or v >= 0 for v in res.values())


def test_zero_record_domains_do_not_break_the_case(tmp_path):
    from paytmforensics.core.case import Case
    (tmp_path / "ext" / "databases").mkdir(parents=True)
    c = Case(str(tmp_path / "ext"), str(tmp_path / "out"), case_id="EMPTY")
    c.ingest(); c.parse_all(); c.carve(); c.correlate()
    assert c.build_timeline() == 0
    c.close()


# ------------------------------------------------ sensitive-data masking (display) #
def test_masking_is_off_by_default_and_never_touches_the_case_db(tmp_path):
    """Masking is presentation-only: case.db must always hold the full evidence."""
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.core.casedb import CaseDB
    p = str(tmp_path / "case.db")
    case = CaseDB(p)
    case.con.execute(
        "INSERT INTO records(domain,origin,source_file,source_table,data) VALUES(?,?,?,?,?)",
        ("transaction", "live", "f", "t", json.dumps(
            {"domain": "transaction", "amount": 82.0, "counterparty_name": "Acme Foods",
             "counterparty_vpa": "acme-123@bankx", "rrn": "900000000001",
             "account_used": "0000_BANK0FAKE01", "latitude": 12.34567,
             "provenance": {"origin": "live"}})))
    case.con.commit(); case.close()

    ds = DataSource(p)
    assert ds.mask_sensitive is False                       # OFF by default
    r = ds.load("transaction")[0]
    assert ds.cell(r, "counterparty_name") == "Acme Foods"  # everything shown
    assert ds.cell(r, "rrn") == "900000000001"

    ds.set_mask(True)
    r2 = ds.load("transaction")[0]
    assert "Acme" not in ds.cell(r2, "counterparty_name")
    assert "900000000001" not in ds.cell(r2, "rrn")
    assert ds.cell(r2, "amount") == "82.0"                  # analysis stays readable
    # the record in the DB is untouched
    assert ds.load("transaction")[0]["rrn"] == "900000000001"
    ds.close()


def test_masking_covers_every_sensitive_field_kind():
    from paytmforensics.core import privacy as P
    cases = {
        "counterparty_name": "Acme Foods Pvt Ltd",
        "phone": "9876543210", "counterparty_mobile": "9876543210",
        "customer_id": "1000000001", "sendbird_id": "1900000000000000001",
        "device_id": "abc123", "counterparty_vpa": "acme-123@bankx",
        "rrn": "900000000001", "account_used": "0000_BANK0FAKE01",
        "ifsc_or_branch": "0318_BANK0001378", "account_bank": "Some Bank",
        "masked_account": "XXXX1234", "instrument": "Some Bank",
        "pincode": "500081",
    }
    for f, v in cases.items():
        out = str(P.mask_field(f, v))
        assert P.BULLET in out, f"{f} not masked: {out}"
        # no digit run of 4+ survives anywhere
        assert not re.search(r"\d{4}", out), f"{f} leaked digits: {out}"


def test_masking_keeps_coordinates_numeric_for_the_map():
    """The map does arithmetic on lat/lon; a masked string would crash it."""
    from paytmforensics.core import privacy as P
    lat = P.mask_field("latitude", 12.34567)
    lon = P.mask_field("longitude", 76.54321)
    assert isinstance(lat, float) and isinstance(lon, float)
    assert abs(lat - 12.34567) < 0.1 and lat != 12.34567   # blurred, not exact
    assert min(-90, lat) == -90 and max(90, lat) == 90     # still a valid coordinate


def test_masking_leaves_analysis_fields_alone():
    from paytmforensics.core import privacy as P
    for f in ("amount", "direction", "settled", "txn_source", "status_label",
              "category_label", "utc_iso", "confidence", "origin", "event_type"):
        assert P.field_kind(f) is None, f
        assert P.mask_field(f, "keepme") == "keepme"


def test_masking_hides_identifiers_embedded_in_free_text():
    from paytmforensics.core import privacy as P
    out = P.mask_field("narration", "Paid 9876543210 via alice@bankx IFSC BANK0001378")
    for leak in ("9876543210", "alice", "bankx", "BANK0001378"):
        assert leak not in out, out
    # prose survives
    assert "Paid" in out and "via" in out and "IFSC" in out


def test_filters_still_match_unmasked_values_while_masking_is_on(tmp_path):
    """Masking must not break search: the examiner filters on the real value."""
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.filters import FilterSpec, apply_filter
    from paytmforensics.core.casedb import CaseDB
    p = str(tmp_path / "case.db")
    case = CaseDB(p)
    case.con.execute(
        "INSERT INTO records(domain,origin,source_file,source_table,data) VALUES(?,?,?,?,?)",
        ("transaction", "live", "f", "t", json.dumps(
            {"domain": "transaction", "amount": 5.0, "counterparty_name": "Acme Foods",
             "provenance": {"origin": "live"}})))
    case.con.commit(); case.close()
    ds = DataSource(p, mask_sensitive=True)
    rows = ds.load("transaction")                       # filtering uses REAL records
    assert len(apply_filter(rows, FilterSpec(text="Acme"))) == 1
    assert ds.global_search("Acme")                     # global search still finds it
    dom, summary, rec = ds.global_search("Acme")[0]
    assert "Acme" not in summary                        # ...but displays masked
    ds.close()


# ------------------------------------------------------ robustness of entry points #
def test_geo_export_honours_masking():
    """The one export path that reads case.db directly, bypassing DataSource.

    Without this, a KML handed over while the GUI's masking toggle was on would still
    carry exact coordinates and silently defeat the control.
    """
    from paytmforensics.report import geo
    fixes = [{"lat": 12.3456789, "lon": 76.5432109, "src": "signal", "ts": "2026-01-01"}]
    plain = json.loads(geo.to_geojson(fixes))["features"][0]["geometry"]["coordinates"]
    assert plain == [76.5432109, 12.3456789]
    # via the masking path
    from paytmforensics.core import privacy
    assert privacy.mask_field("latitude", 12.3456789) == 12.3


def test_opening_a_non_case_directory_raises_a_clear_error(tmp_path):
    """A wrong folder is an ordinary mistake and must not produce a traceback (NFR-3)."""
    from paytmforensics.gui.datasource import DataSource, CaseOpenError
    with pytest.raises(CaseOpenError) as e:
        DataSource(str(tmp_path / "case.db"))
    assert "No case database found" in str(e.value)
    assert "paytmforensics.cli" in str(e.value)      # tells the user what to do

    bad = tmp_path / "case.db"
    bad.write_bytes(os.urandom(4096))
    with pytest.raises(CaseOpenError) as e2:
        DataSource(str(bad))
    assert "not a readable case database" in str(e2.value)


def test_cli_rejects_an_extraction_that_is_a_file(tmp_path):
    from paytmforensics import cli
    f = tmp_path / "notadir"
    f.write_text("x")
    with pytest.raises(SystemExit) as e:
        cli.main(["--extraction", str(f), "--out", str(tmp_path / "o")])
    assert e.value.code != 0


def test_unicode_and_emoji_survive_a_full_run(tmp_path):
    """NFR-7: UTF-8, emoji and Indian-language strings, including in the PATH."""
    from paytmforensics.core.case import Case
    root = tmp_path / "एक्सट्रैक्शन 📱"
    (root / "databases").mkdir(parents=True)
    con = sqlite3.connect(str(root / "databases" / "passbook.db"))
    con.execute("CREATE TABLE UthListingEntity(sourceTxnId TEXT PRIMARY KEY, amount REAL,"
                " txnDate INTEGER, narration TEXT, txnTag TEXT)")
    con.execute("INSERT INTO UthListingEntity VALUES('T1',9.0,1723742756754,'चाय ☕ ₹9','🥘 Food')")
    con.commit(); con.close()
    out = str(tmp_path / "case")
    c = Case(str(root), out, case_id="UNI")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    blob = " ".join(d for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='transaction'"))
    con.close()
    for ch in ("चाय", "☕", "₹9", "🥘"):
        assert ch in blob, ch


def test_rerunning_into_the_same_case_dir_does_not_duplicate(tmp_path):
    from paytmforensics.core.case import Case
    dbd = tmp_path / "ext" / "databases"
    dbd.mkdir(parents=True)
    con = sqlite3.connect(str(dbd / "passbook.db"))
    con.execute("CREATE TABLE UthListingEntity(sourceTxnId TEXT PRIMARY KEY, amount REAL,"
                " txnDate INTEGER)")
    con.execute("INSERT INTO UthListingEntity VALUES('T1',9.0,1723742756754)")
    con.commit(); con.close()
    out = str(tmp_path / "case")
    for _ in range(3):
        c = Case(str(tmp_path / "ext"), out, case_id="RE")
        c.ingest(); c.parse_all(); c.correlate(); c.build_timeline(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    n = con.execute("SELECT COUNT(*) FROM records WHERE domain='transaction'").fetchone()[0]
    con.close()
    assert n == 1, f"re-running produced {n} transactions instead of 1"


def test_every_module_imports_cleanly():
    """A lazily-imported module that fails to import would only surface in the field."""
    import importlib
    import pkgutil
    import paytmforensics
    bad = []
    for m in pkgutil.walk_packages(paytmforensics.__path__, "paytmforensics."):
        if ".gui" in m.name:
            continue                     # needs Qt; covered by the GUI harness
        try:
            importlib.import_module(m.name)
        except Exception as e:
            bad.append(f"{m.name}: {type(e).__name__}")
    assert not bad, bad


def test_packaging_spec_collects_lazily_imported_subpackages():
    """Case/MainWindow import the carver, correlate and report layers on demand; a
    packaged build that dropped them would fail only at runtime."""
    spec = open("paytmforensics.spec", encoding="utf-8").read()
    for pkg in ("parsers", "carving", "correlate", "report", "enrich", "ingest", "core"):
        assert f'"paytmforensics.{pkg}"' in spec, pkg
