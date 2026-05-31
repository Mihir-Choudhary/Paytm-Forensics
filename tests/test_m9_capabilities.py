"""Tests for the Capabilities parser (feature availability, not usage)."""
import os
import tempfile

import pytest

from paytmforensics.core.case import Case

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")
pytestmark = pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")


@pytest.fixture(scope="module")
def caps():
    out = tempfile.mkdtemp(prefix="ptmfx_m9_")
    c = Case(EXTRACTION, out, case_id="M9")
    c.ingest(); res = c.parse_all(); c.close()
    import json, sqlite3
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='capability' ORDER BY id")]
    con.close()
    return rows


def test_capabilities_extracted(caps):
    assert len(caps) > 0


def test_payment_instruments_present(caps):
    instruments = {c["name"] for c in caps if c["category"] == "Payment instrument"}
    # exact displayNames verified in the source file
    assert "Pocket Money" in instruments
    assert "APP_ACCOUNTS_PaytmPostpaid" in instruments
    assert "APP_ACCOUNTS_UpiCreditCard" in instruments


def test_automatic_payments_subfeatures(caps):
    autop = {c["name"] for c in caps if c["category"] == "Automatic payments"}
    assert "IPO & Trading Block Requests" in autop
    assert "Reserve Pay" in autop
    assert "Automatic Payments" in autop


def test_orders_bookings_verticals(caps):
    verts = {c["name"] for c in caps if c["category"] == "Orders & bookings vertical"}
    assert "Movies & Events" in verts
    assert "Travel" in verts


def test_server_endpoints_present(caps):
    eps = {c["name"]: c["value"] for c in caps if c["category"] == "Server API endpoint"}
    assert "mandateHistoryApi" in eps
    assert eps["mandateHistoryApi"].startswith("https://")


def test_remote_config_flags(caps):
    flags = {c["name"]: c["value"] for c in caps
             if c["category"] == "Feature flag (remote config)"}
    assert "recurring_mandate_enabled" in flags
    # values are taken verbatim from the file (strings)
    assert flags["recurring_mandate_enabled"] in ("true", "false")


def test_availability_is_boolean(caps):
    for c in caps:
        assert isinstance(c["available"], (bool, type(None)))
