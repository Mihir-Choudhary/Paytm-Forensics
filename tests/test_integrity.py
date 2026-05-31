"""Admissibility-critical smoke tests (EV-1 read-only, EV-6 reproducibility).

Run:  pytest paytmforensics/tests/test_integrity.py
Set env PAYTM_EXTRACTION to point at a real/synthetic extraction; otherwise skipped.
"""
import json
import os
import tempfile

import pytest

from paytmforensics.core.case import Case
from paytmforensics.core import integrity

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION",
    r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm",
)

pytestmark = pytest.mark.skipif(
    not os.path.isdir(EXTRACTION), reason="no extraction available"
)


def _run(out):
    case = Case(EXTRACTION, out, case_id="TEST")
    case.ingest()
    case.parse_all()
    case.build_timeline()
    summ = case.summary()
    case.close()
    return summ


def test_read_only_source_unchanged():
    before = integrity.build_manifest(EXTRACTION)
    with tempfile.TemporaryDirectory() as out:
        _run(out)
    after = integrity.build_manifest(EXTRACTION)
    rep = integrity.verify_against(before, EXTRACTION)
    assert rep["ok"], f"source changed: {rep}"
    assert before["file_count"] == after["file_count"]


def test_reproducible_exports():
    with tempfile.TemporaryDirectory() as o1, tempfile.TemporaryDirectory() as o2:
        s1 = _run(o1)
        s2 = _run(o2)
        # domain counts must be identical across runs
        assert s1 == s2


def test_every_record_has_provenance():
    import sqlite3
    with tempfile.TemporaryDirectory() as out:
        _run(out)
        con = sqlite3.connect(os.path.join(out, "case.db"))
        for (data,) in con.execute("SELECT data FROM records"):
            d = json.loads(data)
            assert d["provenance"]["source_file"], "record missing source_file"
        con.close()
