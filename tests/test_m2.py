"""Rigorous tests for M2: remaining parsers + entity correlation.

Categories continue from test_rigorous.py:
  A. Completeness   - counts vs ground truth
  B. Accuracy       - known values
  I. Correlation    - entity merge correctness + aggregation
  J. Encrypted      - catalogue correctness + NO decryption / no secret leakage
  K. Redaction      - token secrets redacted in prefs
"""
import json
import os
import sqlite3
import tempfile

import pytest

from paytmforensics.core.case import Case
from paytmforensics.ingest import sqlite_ro as sql

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")
DBDIR = os.path.join(EXTRACTION, "databases")
pytestmark = pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")


@pytest.fixture(scope="module")
def case_out():
    out = tempfile.mkdtemp(prefix="ptmfx_m2_")
    c = Case(EXTRACTION, out, case_id="M2")
    c.ingest()
    res = c.parse_all()
    ents = c.correlate()
    c.build_timeline()
    c.close()
    return {"out": out, "results": res, "entities": ents}


def _records(out, domain):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,))]
    con.close()
    return rows


def _raw_count(db, table):
    path = os.path.join(DBDIR, db)
    if not os.path.exists(path):
        return None
    with sql.open_ro(path) as con:
        if table not in sql.list_tables(con):
            return 0
        return con.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]


# ----- A. Completeness ----------------------------------------------------- #
def test_A_jobs_count(case_out):
    # workdb lives under no_backup/, not databases/
    wpath = os.path.join(EXTRACTION, "no_backup", "androidx.work.workdb")
    with sql.open_ro(wpath) as con:
        expected = con.execute("SELECT COUNT(*) FROM WorkSpec").fetchone()[0]
    assert case_out["results"].get("jobs") == expected

def test_A_appmanager_count(case_out):
    assert case_out["results"].get("config.appmanager") == _raw_count("appManagerDB", "ItemTable")

def test_A_bankconfig_count(case_out):
    assert case_out["results"].get("config.bank") == _raw_count("bank_app_manager_database", "bankAppManagerTable")

def test_A_bankdiag_count(case_out):
    assert case_out["results"].get("diagnostics.bank") == _raw_count("paytmbank_error_analytics", "PBHawkEyeEvent")

def test_A_notifications_count(case_out):
    expected = (_raw_count("PaytmMessageDatabase", "NotificationData") or 0) + \
               (_raw_count("PaytmMessageDatabase", "PushData") or 0)
    assert case_out["results"].get("notifications") == expected

def test_A_storefront_flags_count(case_out):
    assert case_out["results"].get("state.storefront_flags") == _raw_count("storefront_db_try3", "sf_v_cache_table")


# ----- B. Accuracy --------------------------------------------------------- #
def test_B_sms_worker_present(case_out):
    workers = {j.get("worker_class") for j in _records(case_out["out"], "job")}
    assert any("SmsProcessWorker" in (w or "") for w in workers)

def test_B_job_state_decoded(case_out):
    jobs = _records(case_out["out"], "job")
    labels = {j.get("state_label") for j in jobs}
    assert labels & {"ENQUEUED", "SUCCEEDED", "RUNNING", "BLOCKED"}

def test_B_diag_customer_id(case_out):
    from . import _truth
    expected = _truth.require("subject.customer_id")
    diags = _records(case_out["out"], "diagnostic")
    assert diags and any(d.get("customer_id") == expected for d in diags)

def test_B_diag_app_version_parsed(case_out):
    diags = _records(case_out["out"], "diagnostic")
    assert any(d.get("app_version") for d in diags)


# ----- I. Correlation ------------------------------------------------------ #
def test_I_subject_entity_exists(case_out):
    ents = _records(case_out["out"], "entity")
    subj = [e for e in ents if e.get("is_subject")]
    assert len(subj) == 1
    from . import _truth
    expected = _truth.require("subject.customer_id")
    assert expected in subj[0]["customer_ids"]

def test_I_no_duplicate_customer_ids_across_entities(case_out):
    ents = _records(case_out["out"], "entity")
    seen = {}
    for e in ents:
        for cid in e.get("customer_ids", []):
            seen.setdefault(cid, 0)
            seen[cid] += 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, f"customer_id appears in multiple entities: {dupes}"

def test_I_aggregation_totals_nonnegative(case_out):
    ents = _records(case_out["out"], "entity")
    assert ents
    for e in ents:
        assert e["total_received"] >= 0 and e["total_paid"] >= 0
        assert e["txn_count"] >= 0

def test_I_merchant_has_transactions(case_out):
    # at least one counterparty should have aggregated transactions
    ents = _records(case_out["out"], "entity")
    assert any(e["txn_count"] > 0 for e in ents)


# ----- J. Encrypted catalogue (must NOT decrypt or leak) ------------------- #
def test_J_dataupi_catalogued(case_out):
    enc = _records(case_out["out"], "encrypted")
    upi = [e for e in enc if e.get("name") == "DataUPI.xml"]
    assert upi, "DataUPI.xml not catalogued"
    e = upi[0]
    assert "AES-256-GCM" in e["cipher"]
    assert e["keystore_alias"] == "NPCI-UPI"
    assert "TEE" in e["reason"] or "AndroidKeyStore" in e["reason"]

def test_J_no_plaintext_secret_in_encrypted_records(case_out):
    # the catalogue must never contain decrypted field values like datak/token/k0
    enc = _records(case_out["out"], "encrypted")
    blob = json.dumps(enc)
    for forbidden in ("datak", "\"k0\"", "token\":"):
        assert forbidden not in blob


# ----- K. Redaction -------------------------------------------------------- #
def test_K_tokens_redacted_in_prefs(case_out):
    prefs = _records(case_out["out"], "pref")
    for p in prefs:
        if p.get("key") in ("sso_token=", "pb_auth_token", "afUninstallToken"):
            v = p.get("value") or ""
            assert v == "" or v.startswith("<redacted:"), f"token not redacted: {p['key']}={v!r}"

def test_K_subject_mobile_in_prefs(case_out):
    prefs = _records(case_out["out"], "pref")
    from . import _truth
    expected = _truth.require("subject_prefs.mobile_key_value")
    assert any(p.get("key") == "mobile" and p.get("value") == expected for p in prefs)
