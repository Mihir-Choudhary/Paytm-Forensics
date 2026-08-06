"""M6: golden validation on a synthetic dataset (no real extraction needed) +
schema-drift hardening.

  X. Golden counts/values on the synthetic extraction (ground truth from synthetic.build)
  Y. Schema drift   - extra/renamed columns tolerated; parsers read by name
  Z. Full pipeline  - parse+carve+correlate+timeline+report all succeed on synthetic data
"""
import json
import os
import sqlite3
import tempfile

import pytest

from paytmforensics.core.case import Case
from paytmforensics.ingest import sqlite_ro as sql
from tests import synthetic


@pytest.fixture()
def synth(tmp_path):
    root = str(tmp_path / "extraction")
    os.makedirs(root, exist_ok=True)
    truth = synthetic.build(root)
    out = str(tmp_path / "case")
    c = Case(root, out, case_id="SYNTH", examiner="QA", evidence_number="SX")
    c.ingest()
    results = c.parse_all()
    carved = c.carve()
    ents = c.correlate()
    tl = c.build_timeline()
    c.close()
    return {"root": root, "out": out, "truth": truth, "results": results,
            "carved": carved, "entities": ents, "timeline": tl}


def _records(out, domain):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (domain,))]
    con.close()
    return rows


# ----- X. Golden -------------------------------------------------------------#
@pytest.mark.parametrize("parser", [
    "transactions.passbook", "chats",
    "contacts.users", "contacts.vpa_cache", "consents", "location.signal", "jobs",
])
def test_X_parser_counts_match_truth(synth, parser):
    assert synth["results"].get(parser) == synth["truth"][parser]


def test_X_subject_values(synth):
    subj = [p for p in _records(synth["out"], "person") if p.get("is_subject")]
    assert len(subj) == 1
    assert subj[0]["customer_id"] == synth["truth"]["subject_customer_id"]
    assert subj[0]["phone"] == synth["truth"]["subject_phone"]


def test_X_rrns_recovered(synth):
    # RRNs are carried on chat messages, not passbook transactions
    rrns = {m.get("rrn") for m in _records(synth["out"], "message") if m.get("rrn")}
    assert synth["truth"]["known_rrns"].issubset(rrns)


def test_X_enum_and_errorcode_decoded(synth):
    txns = _records(synth["out"], "transaction")
    by_id = {t.get("source_txn_id"): t for t in txns}
    assert by_id["PTMAAA111"]["direction"] == "debit"
    assert by_id["PTMAAA111"]["status_label"] == "success"
    assert by_id["PTMAAA111"]["category_label"] == "food_and_beverages"
    # error code 1103 decoded via bundled error_mapper.json
    assert by_id["PTMBBB222"]["direction"] == "credit"
    assert by_id["PTMBBB222"]["error_message"] and "passcode" in by_id["PTMBBB222"]["error_message"].lower()


def test_X_subject_token_redacted(synth):
    prefs = _records(synth["out"], "pref")
    tok = [p for p in prefs if p.get("key") == "sso_token="]
    assert tok and tok[0]["value"].startswith("<redacted:")


# ----- Y. Schema drift -------------------------------------------------------#
def test_Y_extra_and_missing_columns_tolerated(tmp_path):
    """A passbook table with extra columns + a missing optional column must still parse."""
    root = str(tmp_path / "drift"); dbd = os.path.join(root, "databases")
    os.makedirs(dbd, exist_ok=True)
    con = sqlite3.connect(os.path.join(dbd, "passbook.db"))
    # note: no 'narration', extra 'futureCol'; parser reads by name -> tolerant
    con.execute("""CREATE TABLE UthListingEntity(
        sourceTxnId TEXT PRIMARY KEY, amount REAL, txnIndicator INTEGER,
        identifier TEXT, txnDate INTEGER, futureCol TEXT)""")
    con.execute("INSERT INTO UthListingEntity VALUES('Z1',12.5,2,'x@ybl',1724056316881,'new')")
    con.commit(); con.close()
    out = str(tmp_path / "o")
    c = Case(root, out, case_id="DRIFT")
    c.ingest(); res = c.parse_all(); c.close()
    assert res.get("transactions.passbook") == 1
    t = _records(out, "transaction")[0]
    assert t["amount"] == 12.5 and t["direction"] == "debit"
    assert t.get("narration") is None     # missing column -> None, no crash


# ----- Z. Full pipeline + report --------------------------------------------#
def test_Z_rerun_is_idempotent(tmp_path):
    """Re-running a full build into the same case dir must NOT duplicate records."""
    root = str(tmp_path / "ex"); os.makedirs(root, exist_ok=True)
    synthetic.build(root)
    out = str(tmp_path / "case")

    def run():
        c = Case(root, out, case_id="IDEM")
        c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline()
        s = c.summary(); c.close(); return s

    first = run()
    second = run()
    assert first == second, f"re-run changed counts: {first} -> {second}"


def test_Z_full_pipeline_and_report(synth):
    from paytmforensics.report import html as htmlrep
    out = synth["out"]
    rp = os.path.join(out, "report.html")
    digest = htmlrep.generate(out, rp, fmt="html")
    assert os.path.exists(rp) and len(digest) == 64
    doc = open(rp, encoding="utf-8").read()
    assert "SYNTH" in doc and "Transactions" in doc
    # timeline built
    assert synth["timeline"] > 0
