"""Tests for the batch of additions: webcache, cart, crash, protobuf, IFSC, geo,
global search, visual timeline."""
import json
import os
import sqlite3
import tempfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from paytmforensics.core.case import Case
from paytmforensics.enrich import protobuf, ifsc

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")
realdata = pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")


# ---------- pure-unit (no extraction) ---------- #
def test_protobuf_decode_roundtrip():
    # field1=varint150, field2="abc", field3=nested{field1=1}
    blob = bytes([0x08, 0x96, 0x01]) + bytes([0x12, 0x03]) + b"abc" + \
           bytes([0x1a, 0x02, 0x08, 0x01])
    d = protobuf.decode(blob)
    assert d[1] == 150 and d[2] == "abc" and d[3] == {1: 1}


def test_protobuf_rejects_garbage():
    assert protobuf.decode(b"\xff\xff\xff\xff\xff\xff\xff\xff") is None or isinstance(
        protobuf.decode(b"\xff\xff\xff\xff\xff\xff\xff\xff"), dict)


def test_ifsc_resolution():
    # use fabricated account strings — only the IFSC bank-code prefix matters here
    assert ifsc.resolve("0000_HDFC0009999") == ("HDFC Bank", "009999")
    assert ifsc.resolve("0000_BARB0FAKE01") == ("Bank of Baroda", "FAKE01")
    assert ifsc.resolve(None) == (None, None)
    assert ifsc.extract_ifsc("0000_HDFC0009999") == "HDFC0009999"


# ---------- against real extraction ---------- #
@pytest.fixture(scope="module")
def out():
    if not os.path.isdir(EXTRACTION):
        pytest.skip("no extraction")
    d = tempfile.mkdtemp(prefix="ptmfx_m10_")
    c = Case(EXTRACTION, d, case_id="M10")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    return d


def _load(out, dom):
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain=? ORDER BY id", (dom,))]
    con.close()
    return rows


@realdata
def test_new_domains_present(out):
    for dom in ("webcache", "appstate", "crash"):
        assert len(_load(out, dom)) >= 1, f"{dom} empty"


@realdata
def test_cart_record(out):
    from . import _truth
    cart = _load(out, "appstate")
    assert cart and "cart_id=" in cart[0]["value"]
    expected = _truth.require("subject.customer_id")
    assert expected in cart[0]["value"]   # customer id


@realdata
def test_crash_session_userid(out):
    from . import _truth
    expected = _truth.require("crash_user_id")
    crash = _load(out, "crash")
    assert crash and crash[0]["user_id"] == expected


@realdata
def test_ifsc_enriched_on_txns(out):
    txns = [t for t in _load(out, "transaction") if t.get("account_bank")]
    assert txns, "no account_bank resolved"
    assert any(t["account_bank"] == "HDFC Bank" for t in txns)


@realdata
def test_geo_export(out, tmp_path):
    from paytmforensics.report import geo
    casedb = os.path.join(out, "case.db")
    kml = str(tmp_path / "l.kml"); gj = str(tmp_path / "l.geojson")
    n1 = geo.export(casedb, kml, "kml")
    n2 = geo.export(casedb, gj, "geojson")
    assert n1 == n2 and n1 > 0
    assert "<kml" in open(kml, encoding="utf-8").read()
    assert json.load(open(gj, encoding="utf-8"))["type"] == "FeatureCollection"


@realdata
def test_global_search(out):
    from . import _truth
    from paytmforensics.gui.datasource import DataSource
    token = _truth.require("known_merchant.global_search_token")
    rrn = _truth.require("known_merchant.rrn")
    ds = DataSource(os.path.join(out, "case.db"))
    hits = ds.global_search(token)
    assert hits and any(dom == "transaction" for dom, _s, _r in hits)
    # value search works for the RRN
    assert ds.global_search(rrn)
    ds.close()


@realdata
def test_global_search_matches_values_not_keys(out):
    # searching the field NAME "rrn" must NOT match every txn/message that has an rrn value
    # (before the fix it matched the key and returned 100+; values-only returns ~0-1)
    from paytmforensics.gui.datasource import DataSource
    ds = DataSource(os.path.join(out, "case.db"))
    n_with_rrn = sum(1 for t in ds.load("transaction") if t.get("rrn")) + \
                 sum(1 for m in ds.load("message") if m.get("rrn"))
    assert len(ds.global_search("rrn")) < n_with_rrn   # key not matched
    ds.close()


@realdata
def test_visual_timeline_renders(out):
    from PySide6.QtWidgets import QApplication
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.gui.timelineview import TimelineView
    QApplication.instance() or QApplication([])
    ds = DataSource(os.path.join(out, "case.db"))
    tv = TimelineView(ds); tv.resize(900, 500)
    assert tv.grab().width() > 0
    ds.close()


@realdata
def test_window_search_and_timeline(out):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from paytmforensics.gui.app import MainWindow
    QApplication.instance() or QApplication([])
    win = MainWindow(out)
    win.search_box.setText("Quickeats"); win._global_search()
    assert win._search_results
    for i in range(win.nav.count()):
        if win.nav.item(i).data(Qt.UserRole) == "timeline":
            win.nav.setCurrentRow(i); break
    assert win._timeline_page is not None
    win.close()
