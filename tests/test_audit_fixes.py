"""Regression tests for the audit fixes (transaction/chat conflation)."""
import json
import os
import sqlite3
import tempfile

import pytest

from paytmforensics.core.case import Case

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")
pytestmark = pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")


@pytest.fixture(scope="module")
def out():
    d = tempfile.mkdtemp(prefix="ptmfx_audit_")
    c = Case(EXTRACTION, d, case_id="AUDIT")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    return d


def _load(out, dom):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (dom,))]
    con.close()
    return rows


def test_transactions_deduped_by_source_txn_id(out):
    # passbook + chat merged, but no payment counted twice (uniqueKey == sourceTxnId dedup)
    txns = _load(out, "transaction")
    ids = [t.get("source_txn_id") for t in txns if t.get("source_txn_id")]
    assert len(ids) == len(set(ids)), "duplicate source_txn_id across passbook+chat"
    srcs = {t.get("txn_source") for t in txns}
    assert srcs <= {"passbook", "chat"} and "chat" in srcs and "passbook" in srcs


def test_failed_declined_kept_but_not_settled(out):
    # failed/declined chat events ARE present (evidence) but marked settled=False
    txns = _load(out, "transaction")
    bad = [t for t in txns
           if "declin" in (t.get("narration") or "").lower()
           or (t.get("status_label") or "").upper() in ("FAILURE", "DECLINED")]
    assert bad, "expected failed/declined chat payments to be present as evidence"
    for t in bad:
        assert t.get("settled") is False, f"failed/declined should be settled=False: {t}"


def test_passbook_rrn_enriched(out):
    # passbook txns now carry the RRN (verified == chat RRN), bank account, and category
    from . import _truth
    pb = [t for t in _load(out, "transaction") if t.get("txn_source") == "passbook"]
    assert any(t.get("rrn") for t in pb), "no passbook RRN extracted"
    vpa_val = _truth.require("known_merchant.vpa")
    rrn_val = _truth.require("known_merchant.rrn")
    acct_val = _truth.require("known_merchant.account_used")
    hits = [t for t in pb if t.get("counterparty_vpa") == vpa_val]
    assert hits and hits[0]["rrn"] == rrn_val
    assert hits[0]["account_used"] == acct_val
    assert hits[0]["account_type"] == "savings"


def test_counterparty_mobile_extracted(out):
    # a known counterparty mobile is recovered from searchableStrings
    from . import _truth
    amt = float(_truth.require("known_counterparty.amount"))
    expected_mob = _truth.require("known_counterparty.mobile")
    txns = _load(out, "transaction")
    hits = [t for t in txns if t.get("amount") == amt and t.get("txn_source") == "passbook"]
    assert hits and hits[0].get("counterparty_mobile") == expected_mob


def test_only_settled_count_in_totals(out):
    # the pending passbook ₹70 (statusKey=1) must be settled=False -> excluded from money totals
    txns = _load(out, "transaction")
    pending = [t for t in txns if t.get("txn_source") == "passbook" and not t.get("settled")]
    assert pending, "expected at least one non-settled passbook txn (the pending ₹70)"


def test_timeline_no_payment_double_listing(out):
    # payment messages are listed as transactions, NOT also as message events
    tl = _load(out, "timeline")
    msgs = _load(out, "message")
    n_nonpayment_msgs = sum(
        1 for m in msgs if not m.get("amount") and (m.get("timestamp") or {}).get("utc_iso"))
    msg_events = [e for e in tl if e.get("ref_domain") == "message"]
    assert len(msg_events) == n_nonpayment_msgs


def test_subject_not_inflated_as_counterparty(out):
    ents = _load(out, "entity")
    subj = [e for e in ents if e.get("is_subject")]
    assert len(subj) == 1
    # before the fix the subject had txn_count=46 (chat payments mis-attributed)
    assert subj[0]["txn_count"] < 10, f"subject txn_count still inflated: {subj[0]['txn_count']}"


def test_chat_txn_timestamps_parsed_utc(out):
    # chat transactions must carry a parsed UTC timestamp (from createdAt, not local txnDate)
    chat = [t for t in _load(out, "transaction") if t.get("txn_source") == "chat"]
    assert chat
    with_utc = [t for t in chat if (t.get("timestamp") or {}).get("utc_iso")]
    assert len(with_utc) == len(chat), "some chat txns missing utc_iso"
    assert all((t["timestamp"]["epoch_type"] == "unix_ms") for t in with_utc)


def test_timeline_includes_all_transactions(out):
    import json as _j
    con = sqlite3.connect(os.path.join(out, "case.db"))
    tl = [_j.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='timeline'")]
    con.close()
    n_txn = len(_load(out, "transaction"))
    txn_events = sum(1 for e in tl if e.get("ref_domain") == "transaction")
    assert txn_events == n_txn, f"timeline txn events {txn_events} != {n_txn}"


def test_timeline_provenance_has_hash(out):
    import json as _j
    con = sqlite3.connect(os.path.join(out, "case.db"))
    miss = 0
    for (d,) in con.execute("SELECT data FROM records WHERE domain='timeline'"):
        p = _j.loads(d)["provenance"]
        if p.get("source_file") and not p.get("ingest_sha256"):
            miss += 1
    con.close()
    assert miss == 0, f"{miss} timeline events missing source hash"


def test_chat_payments_still_present_as_messages(out):
    msgs = _load(out, "message")
    with_rrn = [m for m in msgs if m.get("rrn")]
    assert len(with_rrn) > 0          # RRNs preserved on messages
    with_amount = [m for m in msgs if m.get("amount")]
    assert len(with_amount) > 0       # amounts preserved on messages
