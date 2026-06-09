"""Regression tests for the GUI/filter fix batch (2026-06-10). Synthetic data only.

Covers:
  A1. date_to is inclusive of the whole end day
  A2. per-table text filter ignores provenance/raw (same subset as global search)
  A3. record_utc understands cookie/webcache "created" and crash "start_time"
  B2. keyboard navigation updates the detail pane (selection-model wiring)
  B3. type-aware column sorting, blanks last, persists across filtering
  B7. GUI opens case.db read-only
  +   search page owns a visible detail pane; double-click opens the domain view

No real extraction needed; everything runs against a fabricated mini case dir.
Pure-logic tests run without Qt; the Qt tests use the offscreen platform.
"""
import json
import os
import sqlite3
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.gui.filters import (
    FilterSpec, apply_filter, record_utc, searchable_values,
)
from paytmforensics.gui.sorting import sort_records

try:                                    # full Qt is optional for this module:
    from PySide6 import QtWidgets       # pure-logic tests still run without it
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


class _Skip(Exception):
    """Raised instead of pytest.skip when pytest itself is unavailable."""


def _require_qt():
    if not _HAS_QT:
        try:
            import pytest
        except ImportError:
            raise _Skip("PySide6 Qt modules not available")
        pytest.skip("PySide6 Qt modules not available")


# --------------------------------------------------------------------------- #
#  synthetic mini case
# --------------------------------------------------------------------------- #
def _mini_case_dir() -> str:
    """A tiny case dir (case.db + case_meta.json) with fabricated records."""
    out = tempfile.mkdtemp(prefix="ptmfx_fixes_")
    con = sqlite3.connect(os.path.join(out, "case.db"))
    con.execute(
        """CREATE TABLE records (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               domain TEXT NOT NULL, origin TEXT NOT NULL,
               source_file TEXT, source_table TEXT, data TEXT NOT NULL)"""
    )

    def add(domain, data):
        data.setdefault("provenance", {
            "source_file": "databases/synthetic.db", "source_table": "t",
            "rowid": 1, "byte_offset": None, "origin": "live",
            "confidence": 1.0, "ingest_sha256": "ab12cd34" * 8,
        })
        data["domain"] = domain
        con.execute(
            "INSERT INTO records (domain, origin, source_file, source_table, data) "
            "VALUES (?,?,?,?,?)",
            (domain, "live", "databases/synthetic.db", "t",
             json.dumps(data, ensure_ascii=False)))

    add("transaction", {"amount": 9.0, "direction": "debit", "settled": True,
                        "counterparty_name": "Test Grocer",
                        "timestamp": {"raw": 1, "epoch_type": "unix_ms",
                                      "utc_iso": "2026-05-12T07:15:03.930000+00:00"}})
    add("transaction", {"amount": 100.0, "direction": "credit", "settled": True,
                        "counterparty_name": "Test Cafe",
                        "timestamp": {"raw": 2, "epoch_type": "unix_ms",
                                      "utc_iso": "2026-05-10T10:00:00+00:00"}})
    add("transaction", {"amount": None, "direction": None, "settled": None,
                        "counterparty_name": "No Amount Row",
                        "timestamp": {"raw": 3, "epoch_type": "unix_ms",
                                      "utc_iso": "2026-05-13T00:00:01+00:00"}})
    add("cookie", {"host": ".example.test", "name": "sid", "value": "v",
                   "is_secure": True,
                   "created": {"raw": 4, "epoch_type": "webkit_us",
                               "utc_iso": "2024-08-15T10:00:00+00:00"}})
    con.commit(); con.close()
    with open(os.path.join(out, "case_meta.json"), "w", encoding="utf-8") as f:
        json.dump({"case_id": "SYN-1", "examiner": "Synthetic",
                   "evidence_number": "SYN"}, f)
    return out


# --------------------------------------------------------------------------- #
#  A1 — date_to inclusivity
# --------------------------------------------------------------------------- #
def test_date_to_includes_the_end_day():
    recs = [{"timestamp": {"utc_iso": "2026-05-12T07:15:03.930000+00:00"}}]
    out = apply_filter(recs, FilterSpec(date_from="2026-05-12", date_to="2026-05-12"))
    assert len(out) == 1, "a record ON the end date must match an inclusive range"


def test_date_to_still_excludes_later_days():
    recs = [{"timestamp": {"utc_iso": "2026-05-13T00:00:01+00:00"}}]
    assert apply_filter(recs, FilterSpec(date_to="2026-05-12")) == []


def test_date_to_full_iso_still_works():
    recs = [{"timestamp": {"utc_iso": "2026-05-12T07:15:03+00:00"}}]
    assert len(apply_filter(recs, FilterSpec(date_to="2026-05-12T08:00"))) == 1
    assert apply_filter(recs, FilterSpec(date_to="2026-05-12T07:00")) == []


# --------------------------------------------------------------------------- #
#  A2 — text filter must not match provenance/raw
# --------------------------------------------------------------------------- #
def test_text_filter_ignores_provenance_and_raw():
    rec = {"amount": 10.0, "counterparty_name": "Real Field",
           "provenance": {"source_file": "databases/passbook.db",
                          "ingest_sha256": "f00dfeedbeef"},
           "raw": {"internal_blob": "zzyzx-internal"}}
    assert apply_filter([rec], FilterSpec(text="f00dfeedbeef")) == []
    assert apply_filter([rec], FilterSpec(text="zzyzx")) == []
    assert apply_filter([rec], FilterSpec(text="passbook")) == []
    assert len(apply_filter([rec], FilterSpec(text="real field"))) == 1


def test_searchable_values_matches_global_search_subset():
    rec = {"a": 1, "provenance": {}, "raw": {}, "domain": "x", "b": "keep"}
    assert set(searchable_values(rec)) == {"a", "b"}


# --------------------------------------------------------------------------- #
#  A3 — record_utc knows created / start_time
# --------------------------------------------------------------------------- #
def test_record_utc_cookie_webcache_crash_shapes():
    assert record_utc({"created": {"utc_iso": "2024-08-15T10:00:00+00:00"}}) \
        == "2024-08-15T10:00:00+00:00"
    assert record_utc({"start_time": {"utc_iso": "2024-08-16T10:00:00+00:00"}}) \
        == "2024-08-16T10:00:00+00:00"


def test_date_filter_now_works_on_cookies():
    recs = [{"created": {"utc_iso": "2024-08-15T10:00:00+00:00"}},
            {"created": {"utc_iso": "2025-01-01T10:00:00+00:00"}}]
    out = apply_filter(recs, FilterSpec(date_from="2024-08-01", date_to="2024-08-31"))
    assert len(out) == 1


# --------------------------------------------------------------------------- #
#  B3 — type-aware sorting (pure logic; no Qt required)
# --------------------------------------------------------------------------- #
def test_sort_numeric_and_blanks_last():
    from paytmforensics.gui.datasource import DataSource
    ds = DataSource(os.path.join(_mini_case_dir(), "case.db"))
    rows = ds.load("transaction")

    out = sort_records(rows, "amount")
    assert [r.get("amount") for r in out] == [9.0, 100.0, None]
    out = sort_records(rows, "amount", descending=True)
    assert [r.get("amount") for r in out] == [100.0, 9.0, None], \
        "blanks must stay last even when descending"


def test_sort_numeric_strings_not_lexical():
    rows = [{"v": "9"}, {"v": "100"}, {"v": "23.5"}]
    assert [r["v"] for r in sort_records(rows, "v")] == ["9", "23.5", "100"]


def test_sort_timestamps_chronologically():
    from paytmforensics.gui.datasource import DataSource
    ds = DataSource(os.path.join(_mini_case_dir(), "case.db"))
    out = sort_records(ds.load("transaction"), "timestamp")
    isos = [(r.get("timestamp") or {}).get("utc_iso") for r in out]
    assert isos == sorted(isos), "timestamp sort must be chronological"


def test_sort_mixed_types_does_not_crash():
    rows = [{"v": 5}, {"v": "abc"}, {"v": None}, {"v": [1, 2]}, {"v": True}]
    out = sort_records(rows, "v")
    assert len(out) == 5 and out[-1]["v"] is None


def test_model_sort_persists_across_filter():
    _require_qt()
    from PySide6.QtCore import Qt
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.models import RecordTableModel

    ds = DataSource(os.path.join(_mini_case_dir(), "case.db"))
    m = RecordTableModel(ds, "transaction")
    col = m._cols.index("amount")
    m.sort(col, Qt.DescendingOrder)
    m.set_filter(FilterSpec(amount_min=1))
    assert [m.record_at(i).get("amount") for i in range(m.rowCount())] == [100.0, 9.0]


# --------------------------------------------------------------------------- #
#  B7 — case DB is opened read-only by the GUI layer
# --------------------------------------------------------------------------- #
def test_datasource_cannot_write_case_db():
    from paytmforensics.gui.datasource import DataSource
    ds = DataSource(os.path.join(_mini_case_dir(), "case.db"))
    try:
        ds.con.execute("INSERT INTO records (domain, origin, data) VALUES ('x','live','{}')")
        raised = False
    except sqlite3.OperationalError:
        raised = True
    assert raised, "GUI connection must be read-only"


def test_datasource_missing_db_raises_not_creates():
    from paytmforensics.gui.datasource import DataSource
    missing = os.path.join(tempfile.mkdtemp(prefix="ptmfx_nodb_"), "case.db")
    try:
        DataSource(missing)
        raised = False
    except sqlite3.OperationalError:
        raised = True
    assert raised
    assert not os.path.exists(missing), "read-only open must not create an empty DB"


# --------------------------------------------------------------------------- #
#  B1/B2 + search page — offscreen Qt smoke test
# --------------------------------------------------------------------------- #
def test_offscreen_search_detail_and_keyboard_nav():
    _require_qt()
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from paytmforensics.gui.app import MainWindow

    win = MainWindow(_mini_case_dir())

    # global search: detail pane lives ON the search page and fills on selection
    win.search_box.setText("test cafe")
    win._global_search()
    assert win._search_table.rowCount() == 1
    win._search_table.setCurrentCell(0, 0)
    assert "Test Cafe" in win._search_detail.toPlainText()
    assert win._search_page.isAncestorOf(win._search_detail), \
        "search detail must be visible on the search page itself"

    # double-click pivots to the domain view with the record selected
    win._open_search_hit(0)
    assert win.stack.currentIndex() == 1
    idx = win.table.currentIndex()
    assert idx.isValid()
    assert win._model.record_at(idx.row()).get("counterparty_name") == "Test Cafe"
    # the pivot selection must also have populated the detail pane (B2 wiring)
    assert "Test Cafe" in win.detail.toPlainText()

    # keyboard-style selection move (no mouse click) refreshes the detail pane
    other = 0 if idx.row() != 0 else 1
    win.table.setCurrentIndex(win._model.index(other, 0))
    assert win._model.record_at(other).get("counterparty_name") \
        in win.detail.toPlainText()

    # invalid filter input is refused rather than silently misapplied (A4)
    win.f_from.setText("12/05/2026")
    win._apply()
    assert win._model.rowCount() == 3, "bad date must not filter anything"
    assert "NOT applied" in win.statusBar().currentMessage()
    win.f_from.setText("2026-05-12"); win.f_to.setText("2026-05-12")
    win._apply()
    assert win._model.rowCount() == 1, "end day must be included (A1, via GUI path)"

    win.close()


if __name__ == "__main__":
    # standalone runner for environments without pytest
    import sys, traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except _Skip as e:
            print(f"SKIP  {fn.__name__}  ({e})")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    sys.exit(1 if failed else 0)
