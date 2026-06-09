"""Regression tests for cycle 4 (2026-06-10): entity pivot, annotations,
audit-chain verification, in-GUI case dialogs. Synthetic data only.
"""
import json
import os
import sqlite3
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PAYTM_NO_WEBMAP", "1")

from paytmforensics.gui.entitylogic import (
    entity_matches_transaction, entity_matches_message, related_records,
    entity_header_rows, _norm_phone,
)
from paytmforensics.core.annotations import AnnotationStore, record_key
from paytmforensics.core.audit import AuditLog, read_entries, verify_chain


# --------------------------------------------------------------------------- #
#  synthetic mini case (entity + matching txns/messages)
# --------------------------------------------------------------------------- #
ENTITY = {
    "domain": "entity",
    "names": ["Test Cafe"], "phones": ["1234567890"],
    "vpas": ["testcafe@synthbank"], "customer_ids": ["CID-TC"],
    "sendbird_ids": ["SB-TC"], "person_type": "MERCHANT", "is_subject": False,
    "txn_count": 2, "total_received": 0.0, "total_paid": 176.0, "msg_count": 1,
    "provenance": {"source_file": "(correlation)", "source_table": None,
                   "rowid": None, "byte_offset": None, "origin": "live",
                   "confidence": 1.0, "ingest_sha256": None},
}

TXN_VPA = {"domain": "transaction", "amount": 76.0, "direction": "debit",
           "counterparty_vpa": "testcafe@synthbank", "counterparty_name": "Someone Else",
           "timestamp": {"utc_iso": "2026-05-12T07:15:03+00:00"}}
TXN_PHONE = {"domain": "transaction", "amount": 100.0, "direction": "debit",
             "counterparty_mobile": "+91 1234567890",
             "timestamp": {"utc_iso": "2026-05-10T10:00:00+00:00"}}
TXN_OTHER = {"domain": "transaction", "amount": 9.0, "direction": "credit",
             "counterparty_name": "Unrelated Grocer",
             "timestamp": {"utc_iso": "2026-05-11T10:00:00+00:00"}}
MSG_SB = {"domain": "message", "sender_id": "SB-TC", "content": "thanks!",
          "channel_url": "chan_1",
          "timestamp": {"utc_iso": "2026-05-12T08:00:00+00:00"}}
MSG_OTHER = {"domain": "message", "sender_id": "SB-XX", "chat_with": "Unrelated Grocer",
             "channel_url": "chan_2",
             "timestamp": {"utc_iso": "2026-05-12T09:00:00+00:00"}}


def _mini_case_dir() -> str:
    out = tempfile.mkdtemp(prefix="ptmfx_c4_")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    con.execute(
        """CREATE TABLE records (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               domain TEXT NOT NULL, origin TEXT NOT NULL,
               source_file TEXT, source_table TEXT, data TEXT NOT NULL)"""
    )
    for i, rec in enumerate([ENTITY, TXN_VPA, TXN_PHONE, TXN_OTHER, MSG_SB, MSG_OTHER]):
        rec = dict(rec)
        rec.setdefault("provenance", {
            "source_file": "databases/synthetic.db", "source_table": "t",
            "rowid": i + 1, "byte_offset": None, "origin": "live",
            "confidence": 1.0, "ingest_sha256": "ab12cd34" * 8})
        con.execute(
            "INSERT INTO records (domain, origin, source_file, source_table, data) "
            "VALUES (?,?,?,?,?)",
            (rec["domain"], "live", rec["provenance"]["source_file"], "t",
             json.dumps(rec, ensure_ascii=False)))
    con.commit(); con.close()
    with open(os.path.join(out, "case_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "SYN-4", "examiner": "Synthetic"}, f)
    return out


# ------------------------------- entitylogic -------------------------------- #
def test_phone_normalisation():
    assert _norm_phone("+91 1234567890") == "1234567890"
    assert _norm_phone("1234567890") == "1234567890"
    assert _norm_phone("0091-1234567890") == "1234567890"
    assert _norm_phone(None) == ""


def test_entity_matching_rules():
    assert entity_matches_transaction(ENTITY, TXN_VPA)        # by VPA
    assert entity_matches_transaction(ENTITY, TXN_PHONE)      # by +91 phone
    assert not entity_matches_transaction(ENTITY, TXN_OTHER)
    assert entity_matches_message(ENTITY, MSG_SB)             # by sendbird id
    assert not entity_matches_message(ENTITY, MSG_OTHER)


def test_related_records_partition():
    txns, msgs = related_records(ENTITY, [TXN_VPA, TXN_PHONE, TXN_OTHER],
                                 [MSG_SB, MSG_OTHER])
    assert len(txns) == 2 and len(msgs) == 1


def test_header_rows_skip_empties():
    rows = dict(entity_header_rows(ENTITY))
    assert rows["Names"] == "Test Cafe"
    assert "Total received" not in rows          # ₹0 hidden
    assert rows["Total paid"] == "₹176"


# ------------------------------- annotations -------------------------------- #
def test_annotation_crud_and_persistence():
    case_dir = _mini_case_dir()
    store = AnnotationStore(case_dir)
    assert store.get(TXN_VPA) == (False, "")
    store.set_flag(TXN_VPA, True)
    store.set_note(TXN_VPA, "exhibit A")
    assert store.get(TXN_VPA) == (True, "exhibit A")
    assert store.count_flagged() == 1
    store.close()
    # persists across reopen (sidecar file, case.db untouched)
    store2 = AnnotationStore(case_dir)
    assert store2.get(TXN_VPA) == (True, "exhibit A")
    assert record_key(TXN_VPA) in store2.flagged_keys()
    # clearing both flag and note removes the row entirely
    store2.set_flag(TXN_VPA, False)
    store2.set_note(TXN_VPA, "")
    assert store2.count_flagged() == 0
    assert store2.get(TXN_VPA) == (False, "")
    store2.close()


def test_annotation_export_merges():
    store = AnnotationStore(_mini_case_dir())
    store.set_flag(TXN_VPA, True)
    store.set_note(TXN_VPA, "n1")
    out = store.annotate_export(TXN_VPA)
    assert out["annotation"] == {"flagged": True, "note": "n1"}
    assert out["amount"] == TXN_VPA["amount"]
    store.close()


def test_annotations_continue_audit_chain():
    case_dir = _mini_case_dir()
    audit_path = os.path.join(case_dir, "audit.log")
    AuditLog(audit_path).log("ingest", {"files": 1})          # pre-existing chain
    store = AnnotationStore(case_dir, audit=AuditLog(audit_path))
    store.set_flag(TXN_VPA, True)
    store.close()
    chain = verify_chain(audit_path)
    assert chain["ok"] and chain["entries"] == 2
    acts = [e["action"] for e in read_entries(audit_path)]
    assert acts == ["ingest", "annotate"]
    # note text must not leak into the audit log (length only)
    store = AnnotationStore(case_dir, audit=AuditLog(audit_path))
    store.set_note(TXN_VPA, "sensitive words")
    entries = read_entries(audit_path)
    assert "sensitive" not in json.dumps(entries)
    assert entries[-1]["detail"]["note_len"] == len("sensitive words")
    store.close()


def test_degenerate_provenance_records_get_distinct_keys():
    # correlation-derived entities all share source "(correlation)" with no
    # rowid — flagging one must NEVER flag the others (caught on real data)
    prov = {"source_file": "(correlation)", "source_table": None,
            "rowid": None, "byte_offset": None, "origin": "live"}
    e1 = {"domain": "entity", "names": ["A"], "provenance": dict(prov)}
    e2 = {"domain": "entity", "names": ["B"], "provenance": dict(prov)}
    assert record_key(e1) != record_key(e2)
    store = AnnotationStore(tempfile.mkdtemp())
    store.set_flag(e1, True)
    assert store.is_flagged(e1) and not store.is_flagged(e2)
    store.close()


# ------------------------------ audit chain --------------------------------- #
def test_verify_chain_detects_tamper():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "audit.log")
    log = AuditLog(p)
    for i in range(5):
        log.log("step", {"i": i})
    assert verify_chain(p) == {"ok": True, "entries": 5, "first_bad": None}
    lines = open(p, encoding="utf-8").read().splitlines()
    lines[2] = lines[2].replace('"i": 2', '"i": 99')          # tamper entry 3
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    rep = verify_chain(p)
    assert rep["ok"] is False and rep["first_bad"] == 3


# ------------------------------ Qt integration ------------------------------ #
def test_entity_pivot_opens_with_matching_records():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from paytmforensics.gui.app import MainWindow
    win = MainWindow(_mini_case_dir())
    win._select_domain("entity")
    assert win._model.domain == "entity"
    row = next(i for i in range(win._model.rowCount())
               if "Test Cafe" in (win._model.record_at(i).get("names") or []))
    win._on_row_double_click(win._model.index(row, 0))
    page = win._entity_page
    assert page is not None and win.stack.currentWidget() is page
    assert len(page.txns) == 2 and len(page.msgs) == 1
    # back returns to the entities table
    page.back.emit()
    assert win.stack.currentIndex() == 1 and win._model.domain == "entity"
    win.close()


def test_flagging_decorates_filters_and_audits():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from paytmforensics.gui.app import MainWindow
    case_dir = _mini_case_dir()
    win = MainWindow(case_dir)
    win._select_domain("transaction")
    total = win._model.rowCount()
    rec = win._model.record_at(0)
    win._toggle_flag(0, rec, True)
    assert win._model.data(win._model.index(0, 0)).startswith("★"), \
        "flagged row must show a star in column 0"
    assert "FLAGGED" in win.detail.toPlainText()
    win.f_flagged.setChecked(True)                 # toggled -> _apply
    assert win._model.rowCount() == 1 < total
    win.f_flagged.setChecked(False)
    assert win._model.rowCount() == total
    chain = verify_chain(os.path.join(case_dir, "audit.log"))
    assert chain["ok"] and chain["entries"] >= 1
    win.close()


def test_audit_dialog_builds():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from paytmforensics.gui.casedialogs import AuditLogDialog
    case_dir = _mini_case_dir()
    AuditLog(os.path.join(case_dir, "audit.log")).log("ingest", {})
    dlg = AuditLogDialog(case_dir)
    assert dlg is not None


if __name__ == "__main__":
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
