"""Rigorous tests for M5: reporting & export.

  T. Export JSON/CSV - correctness, determinism, provenance columns
  U. HTML report     - contains case meta, summary, provenance, integrity note
  V. Report integrity- .sha256 sidecar matches file; reproducible HTML body
  W. PDF             - graceful behaviour when WeasyPrint absent
"""
import hashlib
import json
import os
import re
import tempfile

import pytest

from paytmforensics.core.case import Case
from paytmforensics.report import exporters, html as htmlrep

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")


@pytest.fixture(scope="module")
def case_dir():
    if not os.path.isdir(EXTRACTION):
        pytest.skip("no extraction")
    out = tempfile.mkdtemp(prefix="ptmfx_m5_")
    c = Case(EXTRACTION, out, case_id="M5-CASE", examiner="Tester", evidence_number="EVZ")
    c.ingest(); c.parse_all(); c.carve(); c.correlate(); c.build_timeline(); c.close()
    return out


# ----- T. Export ----------------------------------------------------------- #
def test_T_json_export_roundtrip(tmp_path):
    rows = [{"a": 1, "b": [1, 2], "provenance": {"origin": "live"}}]
    p = str(tmp_path / "x.json")
    n = exporters.export(rows, p, "json")
    assert n == 1
    back = json.load(open(p, encoding="utf-8"))
    assert back == rows

def test_T_csv_has_provenance_columns(tmp_path):
    rows = [{"amount": 50, "provenance": {"source_file": "f", "origin": "live",
                                          "ingest_sha256": "abc"}}]
    p = str(tmp_path / "x.csv")
    exporters.export(rows, p, "csv")
    text = open(p, encoding="utf-8").read()
    assert "prov.source_file" in text and "prov.origin" in text and "abc" in text

def test_T_csv_deterministic(tmp_path):
    rows = [{"amount": 1, "vpas": ["a@b"], "provenance": {"origin": "live"}},
            {"amount": 2, "vpas": ["c@d"], "provenance": {"origin": "carved"}}]
    p1 = str(tmp_path / "1.csv"); p2 = str(tmp_path / "2.csv")
    exporters.export(rows, p1, "csv"); exporters.export(rows, p2, "csv")
    assert open(p1, encoding="utf-8").read() == open(p2, encoding="utf-8").read()

def test_T_unsupported_format(tmp_path):
    with pytest.raises(ValueError):
        exporters.export([], str(tmp_path / "x.zzz"), "zzz")


# ----- U. HTML report ------------------------------------------------------ #
def test_U_html_contains_case_meta_and_sections(case_dir):
    doc = htmlrep.build_html(case_dir)
    assert "M5-CASE" in doc and "Tester" in doc and "EVZ" in doc
    assert "Forensic Analysis Report" in doc
    assert "Transactions" in doc
    assert "Integrity &amp; method" in doc or "Integrity & method" in doc
    # provenance + carved styling present
    assert "origin-live" in doc or "origin-carved" in doc

def test_U_html_has_manifest_hashes(case_dir):
    doc = htmlrep.build_html(case_dir)
    assert "Input manifest" in doc
    # at least one sha256-looking token
    assert re.search(r"[0-9a-f]{64}", doc)

def test_U_html_escapes(case_dir):
    # ensure no raw unescaped angle brackets from data leak structure (basic check)
    doc = htmlrep.build_html(case_dir, table_limit=5)
    assert "<script>" not in doc.lower()


# ----- V. Report integrity ------------------------------------------------- #
def test_V_sha256_sidecar_matches(case_dir, tmp_path):
    out = str(tmp_path / "report.html")
    digest = htmlrep.generate(case_dir, out, fmt="html")
    actual = hashlib.sha256(open(out, "rb").read()).hexdigest()
    assert digest == actual
    sidecar = open(out + ".sha256", encoding="utf-8").read()
    assert digest in sidecar

def test_V_html_body_reproducible(case_dir):
    # build_html excludes the generation timestamp line? it includes it; compare structure
    a = htmlrep.build_html(case_dir)
    b = htmlrep.build_html(case_dir)
    # only the "Report (UTC)" line may differ; strip it then compare
    strip = lambda s: re.sub(r"<td><b>Report \(UTC\)</b></td><td>[^<]*</td>", "", s)
    assert strip(a) == strip(b)


# ----- W. PDF graceful ----------------------------------------------------- #
def test_W_pdf_generation(case_dir, tmp_path):
    # PDF via WeasyPrint if present, else Qt WebEngine fallback; otherwise a clear error.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    out = str(tmp_path / "r.pdf")
    try:
        digest = htmlrep.generate(case_dir, out, fmt="pdf")
    except RuntimeError:
        pytest.skip("no PDF backend available in this environment")
    assert os.path.exists(out) and os.path.getsize(out) > 0
    assert len(digest) == 64


def test_charts_in_report(case_dir):
    doc = htmlrep.build_html(case_dir)
    assert "Analytics" in doc and "<svg" in doc
    assert "Money in vs out" in doc and "Spend by category" in doc
