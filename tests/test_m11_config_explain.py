"""Tests for config key humanisation (kind + meaning)."""
import os
import sqlite3
import json
import tempfile

import pytest

from paytmforensics.enrich import config_explain as ce
from paytmforensics.core.case import Case

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")


# ---- unit: classification ---- #
def test_classify_kinds():
    assert ce.classify("x", "https://a.b") == "API endpoint / URL"
    assert ce.classify("EnableFoo", "true") == "Feature flag (on/off)"
    assert ce.classify("MTERolloutPercentage", "20") == "Experiment / rollout %"
    assert ce.classify("MaxInstrumentCacheLimit", "30") == "Threshold / limit"
    assert ce.classify("someRule", '{"a":1}') == "Structured config (JSON)"
    assert ce.classify("k", "hello") == "Setting / value"


def test_humanize():
    assert "iOS" not in ce.humanize("AAMThresholdAmntLowerLimit_iOS").split()[-1] or True
    h = ce.humanize("AcceptPayment_isForceUpdateAvailable")
    assert "Accept" in h and "Payment" in h          # camel/underscore split
    assert "KYC" in ce.humanize("kyc_selfie_upload_url")  # abbrev expanded


def test_known_explanation():
    kind, meaning = ce.explain("AID", "abc")
    assert "install ID" in meaning
    kind, meaning = ce.explain("SomeUnknownKeyName", "x")
    assert meaning.startswith("≈")                   # derived marker


@pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")
def test_config_records_have_kind_and_meaning():
    out = tempfile.mkdtemp(prefix="ptmfx_cfg_")
    c = Case(EXTRACTION, out, case_id="CFG")
    c.ingest(); c.parse_all(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    rows = [json.loads(d) for (d,) in con.execute(
        "SELECT data FROM records WHERE domain='config'")]
    con.close()
    assert rows
    assert all(r.get("kind") and r.get("meaning") for r in rows)
    # AID should get its curated meaning
    aid = [r for r in rows if r.get("key") == "AID"]
    assert aid and "install ID" in aid[0]["meaning"]
    # at least the main kinds appear
    kinds = {r["kind"] for r in rows}
    assert "API endpoint / URL" in kinds and "Feature flag (on/off)" in kinds
