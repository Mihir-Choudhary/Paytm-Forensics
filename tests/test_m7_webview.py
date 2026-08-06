"""M7 tests: WebView cookies (full) + best-effort LevelDB local storage.

  AA. Cookies   - count vs ground truth, known values, webkit ts decode, synthetic parse
  AB. LevelDB   - raw extractor recovers known origin/key from crafted bytes; best-effort flag
"""
import os
import sqlite3
import tempfile

import pytest

from paytmforensics.core.case import Case
from paytmforensics.core.artifact import Artifact
from paytmforensics.ingest import sqlite_ro as sql
from paytmforensics.parsers.webview import CookiesParser, LocalStorageParser

EXTRACTION = os.environ.get(
    "PAYTM_EXTRACTION", r"C:\Users\admin\Documents\Projects\paytm\net.one97.paytm")


# ----- AB. LevelDB raw extractor (no real data needed) --------------------- #
def test_AB_localstorage_extracts_known_entry(tmp_path):
    # craft a chromium-localStorage-like leveldb fragment
    blob = b"\x00\x00_https://pay.example.com\x00\x01devToken\x00\x01ABC123TOKENVALUE\x00\x00"
    sdir = tmp_path / "Local Storage" / "leveldb"
    sdir.mkdir(parents=True)
    p = sdir / "000005.ldb"
    p.write_bytes(blob)
    art = Artifact(rel_path="app_webview/Default/Local Storage/leveldb/000005.ldb",
                   abs_path=str(p), kind="leveldb", logical="000005.ldb")
    parser = LocalStorageParser({"000005.ldb": art}, {})
    assert parser.available()
    recs = [r.to_dict() for r in parser.parse()]
    assert any(r["origin"] == "https://pay.example.com" and r["key"] == "devToken"
               for r in recs)
    assert all(r["best_effort"] for r in recs)


def test_AB_localstorage_absent_is_graceful():
    parser = LocalStorageParser({}, {})
    assert parser.available() is False
    assert list(parser.parse()) == []


# ----- AA. Cookies synthetic ----------------------------------------------- #
def _make_cookies_db(path):
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE cookies(creation_utc INTEGER, host_key TEXT, name TEXT,
        value TEXT, path TEXT, expires_utc INTEGER, is_secure INTEGER, is_httponly INTEGER)""")
    # webkit micros for 2026-01-01-ish
    cu = (1767225600 + 11644473600) * 1_000_000
    con.execute("INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?)",
                (cu, ".example.com", "dev-id", "realme-XYZ", "/", cu, 1, 0))
    con.execute("INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?)",
                (cu, "site.com", "lat", "12.97", "/", cu, 1, 1))
    con.commit(); con.close()


def test_AA_cookies_synthetic_parse(tmp_path):
    p = str(tmp_path / "Cookies")
    _make_cookies_db(p)
    art = Artifact(rel_path="app_webview/Default/Cookies", abs_path=p,
                   kind="sqlite", logical="Cookies")
    recs = [r.to_dict() for r in CookiesParser({"Cookies": art}, {}).parse()]
    assert len(recs) == 2
    dev = [r for r in recs if r["name"] == "dev-id"][0]
    assert dev["value"] == "realme-XYZ"
    assert dev["is_secure"] is True
    assert dev["created"]["epoch_type"] == "webkit_us"
    assert dev["created"]["utc_iso"].startswith("2026-01-01")


# ----- AA. Cookies on real extraction -------------------------------------- #
@pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")
def test_AA_cookies_count_matches_real():
    path = os.path.join(EXTRACTION, "app_webview", "Default", "Cookies")
    if not os.path.exists(path):
        pytest.skip("no cookies db")
    with sql.open_with_wal(path) if sql.has_wal(path) else sql.open_ro(path) as con:
        expected = con.execute("SELECT COUNT(*) FROM cookies").fetchone()[0]
    art = Artifact(rel_path="app_webview/Default/Cookies", abs_path=path,
                   kind="sqlite", logical="Cookies")
    recs = list(CookiesParser({"Cookies": art}, {}).parse())
    assert len(recs) == expected


@pytest.mark.skipif(not os.path.isdir(EXTRACTION), reason="no extraction")
def test_AA_known_cookie_present():
    out = tempfile.mkdtemp(prefix="ptmfx_m7_")
    c = Case(EXTRACTION, out, case_id="M7")
    c.ingest(); c.parse_all(); c.close()
    con = sqlite3.connect(os.path.join(out, "case.db"))
    import json
    cookies = [json.loads(d) for (d,) in
               con.execute("SELECT data FROM records WHERE domain='cookie'")]
    con.close()
    names = {ck["name"] for ck in cookies}
    assert "dev-id" in names or "_ga" in names
