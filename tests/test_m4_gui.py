"""Rigorous tests for M4: GUI data/filter logic + offscreen Qt smoke test.

  Q. Filter logic   - FilterSpec correctness (text/date/origin/amount/direction/source)
  R. DataSource     - domains/columns/load/cell formatting
  S. Qt smoke       - app + window construct offscreen; model loads; filter narrows rows
"""
import os
import tempfile

import pytest

# headless Qt
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.core.case import Case
from paytmforensics.gui.filters import FilterSpec, apply_filter, record_utc
from paytmforensics.gui.datasource import DataSource

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")


@pytest.fixture(scope="module")
def case_dir():
    out = tempfile.mkdtemp(prefix="ptmfx_m4_")
    if not os.path.isdir(EXTRACTION):
        pytest.skip("no extraction")
    c = Case(EXTRACTION, out, case_id="M4")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    return out


# ----- Q. Filter logic (pure, no extraction needed) ------------------------ #
def test_Q_text_filter():
    recs = [{"a": "FoodCo"}, {"a": "Quickeats"}]
    assert len(apply_filter(recs, FilterSpec(text="foodc"))) == 1

def test_Q_origin_filter():
    recs = [{"provenance": {"origin": "live"}}, {"provenance": {"origin": "carved"}}]
    assert len(apply_filter(recs, FilterSpec(origin="carved"))) == 1

def test_Q_amount_range():
    recs = [{"amount": 10.0}, {"amount": 100.0}, {"amount": 500.0}]
    out = apply_filter(recs, FilterSpec(amount_min=50, amount_max=200))
    assert [r["amount"] for r in out] == [100.0]

def test_Q_direction_filter():
    recs = [{"direction": "credit"}, {"direction": "debit"}]
    assert len(apply_filter(recs, FilterSpec(direction="debit"))) == 1

def test_Q_date_range():
    recs = [
        {"timestamp": {"utc_iso": "2024-01-01T00:00:00+00:00"}},
        {"timestamp": {"utc_iso": "2025-06-01T00:00:00+00:00"}},
    ]
    out = apply_filter(recs, FilterSpec(date_from="2025-01-01", date_to="2025-12-31"))
    assert len(out) == 1

def test_Q_source_contains():
    recs = [{"provenance": {"source_file": "databases/chatDb.db"}},
            {"provenance": {"source_file": "databases/passbook.db"}}]
    assert len(apply_filter(recs, FilterSpec(source_contains="passbook"))) == 1

def test_Q_combined_filters_AND():
    recs = [
        {"amount": 100.0, "direction": "debit", "provenance": {"origin": "live"}},
        {"amount": 100.0, "direction": "credit", "provenance": {"origin": "live"}},
    ]
    out = apply_filter(recs, FilterSpec(amount_min=50, direction="debit", origin="live"))
    assert len(out) == 1

def test_Q_record_utc_extraction():
    assert record_utc({"utc_iso": "2024-01-01T00:00:00+00:00"}) is not None
    assert record_utc({"timestamp": {"utc_iso": "x"}}) == "x"
    assert record_utc({"last_enqueue": {"utc_iso": "y"}}) == "y"
    assert record_utc({"nothing": 1}) is None


# ----- R. DataSource ------------------------------------------------------- #
def test_R_domains_and_load(case_dir):
    ds = DataSource(os.path.join(case_dir, "case.db"))
    doms = ds.domains()
    assert "transaction" in doms and doms["transaction"] > 0
    txns = ds.load("transaction")
    assert len(txns) == doms["transaction"]
    ds.close()

def test_R_columns_known_domain(case_dir):
    ds = DataSource(os.path.join(case_dir, "case.db"))
    cols = ds.columns("transaction")
    assert "amount" in cols and "rrn" in cols
    ds.close()

def test_R_cell_formats_timestamp_and_list(case_dir):
    ds = DataSource(os.path.join(case_dir, "case.db"))
    rec = {"timestamp": {"utc_iso": "2024-08-15T00:00:00+00:00"}, "vpas": ["a@b", "c@d"]}
    cell = ds.cell(rec, "timestamp")
    assert "Aug 2024" in cell and "T" not in cell      # friendly, not raw ISO
    assert ds.cell(rec, "vpas") == "a@b, c@d"
    ds.close()

def test_R_filtered_rows(case_dir):
    ds = DataSource(os.path.join(case_dir, "case.db"))
    all_txn = ds.rows("transaction")
    deb = ds.rows("transaction", FilterSpec(direction="debit"))
    assert len(deb) <= len(all_txn)
    assert all(r.get("direction") == "debit" for r in deb)
    ds.close()


# ----- S. Qt smoke (offscreen) --------------------------------------------- #
def test_S_window_constructs_and_filters(case_dir):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.app import MainWindow
    app = QApplication.instance() or QApplication([])
    win = MainWindow(case_dir)
    # nav populated
    assert win.nav.count() > 0
    # select the transaction domain
    for i in range(win.nav.count()):
        if win.nav.item(i).data(0x0100) == "transaction":  # Qt.UserRole
            win.nav.setCurrentRow(i)
            break
    assert win._model is not None
    total = win._model.rowCount()
    assert total > 0
    # apply a debit filter -> rows should not exceed total
    win.f_dir.setCurrentText("debit")
    win._apply()
    assert win._model.rowCount() <= total
    # row click populates provenance detail
    from PySide6.QtCore import QModelIndex
    idx = win._model.index(0, 0)
    win._on_row_click(idx)
    assert "SOURCE:" in win.detail.toPlainText()
    win.close()
