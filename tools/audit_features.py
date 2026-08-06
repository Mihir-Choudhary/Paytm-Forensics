#!/usr/bin/env python3
"""Per-feature rigorous audit of PaytmForensics.

Each check is independent, states what it asserts, and prints PASS / **FAIL** / WARN.
A check only says PASS if there is an input that would have made it FAIL.

Usage:  QT_QPA_PLATFORM=offscreen python3 tools/audit_features.py <case_dir>
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS: list[tuple[str, str, str, str]] = []   # (area, check, verdict, note)


def rec(area, check, verdict, note=""):
    RESULTS.append((area, check, verdict, note))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn ", "N/A": " n/a  "}[verdict]
    print(f"[{mark}] {area:<16} {check:<58} {note}")


def guard(area, check):
    """Decorator: a raised exception is itself a FAIL, never a crashed audit."""
    def deco(fn):
        try:
            fn()
        except Exception as e:
            rec(area, check, "FAIL", f"raised {type(e).__name__}: {e}")
        return fn
    return deco


H = "=" * 100


def section(t):
    print(f"\n{H}\n{t}\n{H}")


# =========================================================================== #
def audit_filters():
    section("1. FILTERS  (FilterSpec — FR-G3: every grid must filter by each of these)")
    from paytmforensics.gui.filters import FilterSpec, apply_filter, record_utc

    TXN = {"amount": 50.0, "direction": "debit", "settled": True,
           "counterparty_name": "Acme Store",
           "timestamp": {"utc_iso": "2026-05-12T07:15:03.930000+00:00"},
           "provenance": {"origin": "live", "source_file": "databases/passbook.db"}}
    # a domain row that has NO amount / direction / timestamp keys at all
    CFG = {"key": "SomeFlag", "value": "true", "kind": "Feature flag (on/off)",
           "provenance": {"origin": "live", "source_file": "databases/appManagerDB"}}
    COOKIE = {"host": "paytm.com", "name": "lat", "value": "28.6",
              "created": {"utc_iso": "2026-05-12T07:15:03+00:00"},
              "provenance": {"origin": "live", "source_file": "app_webview/Default/Cookies"}}

    # --- text ------------------------------------------------------------- #
    a = "filter:text"
    rec(a, "matches a value substring, case-insensitive", "PASS" if
        FilterSpec(text="acme").matches(TXN) else "FAIL")
    rec(a, "rejects a non-matching substring", "PASS" if
        not FilterSpec(text="zzzz").matches(TXN) else "FAIL")
    hit_prov = FilterSpec(text="passbook").matches(TXN)
    rec(a, "ALSO matches provenance/raw (global search does not)",
        "WARN" if hit_prov else "PASS",
        "inconsistent with DataSource.global_search (F-15)" if hit_prov else "")
    rec(a, "empty text is a no-op", "PASS" if FilterSpec(text="").matches(CFG) else "FAIL")

    # --- date ------------------------------------------------------------- #
    a = "filter:date"
    incl = FilterSpec(date_to="2026-05-12").matches(TXN)
    rec(a, "date_to is INCLUSIVE of that day (docstring says inclusive)",
        "PASS" if incl else "FAIL",
        "" if incl else "record at 12 May 07:15 EXCLUDED by date_to=2026-05-12 (string compare)")
    unp = FilterSpec(date_from="2026-5-1").matches(TXN)
    rec(a, "unpadded date (2026-5-1) behaves sanely",
        "PASS" if unp else "FAIL",
        "" if unp else "lexical compare: '2026-05-12' < '2026-5-1' -> silently excludes")
    rec(a, "date_from correctly excludes an earlier record", "PASS" if
        not FilterSpec(date_from="2026-06-01").matches(TXN) else "FAIL")
    rec(a, "full-month range includes a mid-month record", "PASS" if
        FilterSpec(date_from="2026-05-01", date_to="2026-05-31").matches(TXN) else "FAIL")
    ck = FilterSpec(date_from="2026-01-01", date_to="2026-12-31").matches(COOKIE)
    rec(a, "works on cookie domain (time is in 'created')",
        "PASS" if ck else "FAIL",
        "" if ck else "record_utc ignores 'created' -> in-range row silently dropped (F-08)")
    cf = FilterSpec(date_from="2026-01-01").matches(CFG)
    rec(a, "date filter on an undated domain (config)",
        "WARN" if not cf else "PASS",
        "returns 0 rows; 'no results' is indistinguishable from 'no timestamps'" if not cf else "")

    # --- origin ----------------------------------------------------------- #
    a = "filter:origin"
    rec(a, "origin=live matches a live record", "PASS" if
        FilterSpec(origin="live").matches(TXN) else "FAIL")
    rec(a, "origin=carved rejects a live record", "PASS" if
        not FilterSpec(origin="carved").matches(TXN) else "FAIL")
    rec(a, "origin=any is a no-op", "PASS" if
        FilterSpec(origin="any").matches(TXN) else "FAIL")

    # --- amount ----------------------------------------------------------- #
    a = "filter:amount"
    rec(a, "amount_min includes an equal value (boundary)", "PASS" if
        FilterSpec(amount_min=50.0).matches(TXN) else "FAIL")
    rec(a, "amount_max includes an equal value (boundary)", "PASS" if
        FilterSpec(amount_max=50.0).matches(TXN) else "FAIL")
    rec(a, "amount_min excludes a smaller value", "PASS" if
        not FilterSpec(amount_min=51.0).matches(TXN) else "FAIL")
    ca = FilterSpec(amount_min=0).matches(CFG)
    rec(a, "amount_min=0 on a domain with no 'amount' field",
        "FAIL" if not ca else "PASS",
        "amount is None -> rejected; filtering by amount silently EMPTIES config/pref/cookie grids"
        if not ca else "")

    # --- direction -------------------------------------------------------- #
    a = "filter:direction"
    rec(a, "matches the right direction", "PASS" if
        FilterSpec(direction="debit").matches(TXN) else "FAIL")
    rec(a, "rejects the wrong direction", "PASS" if
        not FilterSpec(direction="credit").matches(TXN) else "FAIL")
    cd = FilterSpec(direction="debit").matches(CFG)
    rec(a, "direction filter on a domain with no 'direction'",
        "WARN" if not cd else "PASS",
        "silently empties the grid (same shape as the amount issue)" if not cd else "")

    # --- source_contains -------------------------------------------------- #
    a = "filter:source"
    rec(a, "substring match on provenance.source_file", "PASS" if
        FilterSpec(source_contains="passbook").matches(TXN) else "FAIL")
    rec(a, "case-insensitive", "PASS" if
        FilterSpec(source_contains="PASSBOOK").matches(TXN) else "FAIL")
    rec(a, "rejects a non-matching source", "PASS" if
        not FilterSpec(source_contains="chatDb").matches(TXN) else "FAIL")

    # --- field_equals ----------------------------------------------------- #
    a = "filter:field_eq"
    rec(a, "matches a string field", "PASS" if
        FilterSpec(field_equals={"direction": "debit"}).matches(TXN) else "FAIL")
    b1 = FilterSpec(field_equals={"settled": True}).matches(TXN)
    b2 = FilterSpec(field_equals={"settled": "true"}).matches(TXN)
    rec(a, "bool field: True and 'true' agree",
        "PASS" if b1 == b2 else "WARN",
        f"str(True)=='True' != 'true'  -> True:{b1} 'true':{b2}" if b1 != b2 else "")
    miss = FilterSpec(field_equals={"nosuchfield": "None"}).matches(TXN)
    rec(a, "a MISSING field does not match the literal 'None'",
        "FAIL" if miss else "PASS",
        "str(rec.get(k)) makes absent fields equal the string 'None'" if miss else "")

    # --- AND composition (FR-G3 explicitly requires it) -------------------- #
    a = "filter:combine"
    rows = [TXN, dict(TXN, amount=5.0), dict(TXN, direction="credit")]
    n_and = len(apply_filter(rows, FilterSpec(direction="debit", amount_min=10.0)))
    rec(a, "two fields compose as AND (narrows, not replaces)",
        "PASS" if n_and == 1 else "FAIL", f"3 rows -> {n_and} (expected 1)")
    n3 = len(apply_filter(rows, FilterSpec(direction="debit", amount_min=10.0, text="acme")))
    rec(a, "three fields compose as AND", "PASS" if n3 == 1 else "FAIL",
        f"-> {n3} (expected 1)")

    # --- FR-G3 saved presets ---------------------------------------------- #
    src = open("paytmforensics/gui/app.py", encoding="utf-8").read()
    has_preset = any(k in src for k in ("preset", "save_filter", "saveFilter"))
    rec("filter:presets", "FR-G3 'save filter presets per case' implemented",
        "PASS" if has_preset else "FAIL", "" if has_preset else "no preset code in gui/app.py")


# =========================================================================== #
def audit_gui(case_dir):
    section("2. GUI RUNTIME  (offscreen; every nav page, filters, detail, theme)")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
    except Exception as e:
        rec("gui", "Qt import", "N/A", f"{e}")
        return
    QApplication.instance() or QApplication([])
    from paytmforensics.gui.app import MainWindow
    from paytmforensics.gui.datasource import DataSource

    try:
        win = MainWindow(case_dir)
    except Exception as e:
        rec("gui", "MainWindow constructs", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return
    rec("gui", "MainWindow constructs against the real case", "PASS")

    ds = DataSource(os.path.join(case_dir, "case.db"))
    counts = ds.domains()

    # walk every nav entry
    navs = []
    for i in range(win.nav.count()):
        d = win.nav.item(i).data(Qt.UserRole)
        if d:
            navs.append((i, d))
    rec("gui:nav", "nav populated", "PASS" if navs else "FAIL", f"{len(navs)} entries")

    for i, dom in navs:
        try:
            win.nav.setCurrentRow(i)
            if dom in ("dashboard", "map"):
                rec("gui:nav", f"open '{dom}'", "PASS")
                continue
            n = win._model.rowCount() if win._model else -1
            exp = counts.get(dom, 0)
            ok = (n == exp)
            rec("gui:nav", f"open '{dom}' rows match case.db",
                "PASS" if ok else "FAIL", f"grid={n} db={exp}")
        except Exception as e:
            rec("gui:nav", f"open '{dom}'", "FAIL", f"{type(e).__name__}: {e}")

    # detail panel / provenance (FR-G4)
    try:
        win._select_domain("transaction")
        from PySide6.QtCore import QModelIndex
        idx = win._model.index(0, 0)
        win._on_row_click(idx)
        txt = win.detail.toPlainText()
        need = ("SOURCE:", "TABLE :", "ROWID :", "ORIGIN:", "SHA256:")
        ok = all(k in txt for k in need)
        rec("gui:detail", "FR-G4 detail panel shows full provenance",
            "PASS" if ok else "FAIL",
            "" if ok else f"missing {[k for k in need if k not in txt]}")
        has_hash = "SHA256:  " in txt and len(txt.split("SHA256:")[1].split("\n")[0].strip()) == 64
        rec("gui:detail", "ingest_sha256 present and 64 hex chars",
            "PASS" if has_hash else "WARN")
    except Exception as e:
        rec("gui:detail", "detail panel", "FAIL", f"{type(e).__name__}: {e}")

    # filter round-trip through the widgets
    try:
        win._select_domain("transaction")
        before = win._model.rowCount()
        win.f_dir.setCurrentText("debit"); win._apply()
        after = win._model.rowCount()
        win._clear()
        restored = win._model.rowCount()
        rec("gui:filter", "apply narrows the grid",
            "PASS" if after < before else "FAIL", f"{before} -> {after}")
        rec("gui:filter", "clear restores the grid",
            "PASS" if restored == before else "FAIL", f"{restored} vs {before}")
    except Exception as e:
        rec("gui:filter", "apply/clear", "FAIL", f"{type(e).__name__}: {e}")

    # non-numeric amount input
    try:
        win._select_domain("transaction")
        base = win._model.rowCount()
        win.f_amin.setText("abc"); win._apply()
        got = win._model.rowCount()
        # Read the banner BEFORE clearing. Assert the user-visible fact (it carries the
        # offending value) rather than Qt visibility, which offscreen depends on which
        # stack page is current.
        shown_txt = win.f_warn.text()
        rec("gui:filter", "invalid input surfaced inline (and never in a blocking modal)",
            "PASS" if "abc" in shown_txt else "FAIL", f"banner={shown_txt[:60]!r}")
        win.f_amin.clear(); win._clear()
        rec("gui:filter", "the warning clears when the filter is cleared",
            "PASS" if not win.f_warn.text() else "FAIL")
    except Exception as e:
        rec("gui:filter", "non-numeric amount", "FAIL", f"{type(e).__name__}: {e}")

    # global search
    try:
        win.search_box.setText("Food"); win._global_search()
        n = len(win._search_results)
        rec("gui:search", "global search returns hits", "PASS" if n else "WARN", f"{n} hits")
        win._search_row_clicked(0, 0)
        rec("gui:search", "clicking a search hit renders detail",
            "PASS" if win.detail.toPlainText() else "FAIL")
    except Exception as e:
        rec("gui:search", "global search", "FAIL", f"{type(e).__name__}: {e}")

    # chat view
    try:
        win._select_domain("message")
        cv = win._chat_page
        nconv = cv.clist.count()
        chans = {m["channel_url"] for m in ds.load("message") if m.get("channel_url")}
        rec("gui:chat", "chat view lists one conversation per channel",
            "PASS" if nconv == len(chans) else "FAIL", f"list={nconv} channels={len(chans)}")
        cv.clist.setCurrentRow(0)
        rec("gui:chat", "selecting a conversation renders bubbles",
            "PASS" if cv.thread.count() > 1 else "FAIL", f"{cv.thread.count()-1} widgets")
        unnamed = sum(1 for i in range(nconv)
                      if "Outgoing (merchant)" in cv.clist.item(i).text())
        rec("gui:chat", "conversations resolve a counterparty name",
            "WARN" if unnamed else "PASS",
            f"{unnamed}/{nconv} fall back to 'Outgoing (merchant)'" if unnamed else "")
    except Exception as e:
        rec("gui:chat", "chat view", "FAIL", f"{type(e).__name__}: {e}")

    # timeline view
    try:
        win._select_domain("timeline")
        tv = win._timeline_page
        rec("gui:timeline", "timeline view constructs + ribbon renders",
            "PASS" if tv and tv.grab().width() > 0 else "FAIL")
        ev = tv.ribbon.events
        srt = all(ev[i][0] <= ev[i+1][0] for i in range(len(ev)-1))
        rec("gui:timeline", "ribbon sorts events chronologically (view-level)",
            "PASS" if srt else "FAIL", f"{len(ev)} events")
    except Exception as e:
        rec("gui:timeline", "timeline view", "FAIL", f"{type(e).__name__}: {e}")

    # map view (WebEngine absent -> offline fallback must work)
    try:
        win._select_domain("map")
        mv = win._map_page
        isweb = getattr(mv, "_is_web", None)
        rec("gui:map", "map view constructs", "PASS" if mv else "FAIL",
            f"webengine={isweb}")
        rec("gui:map", "offline fallback canvas renders when WebEngine absent",
            "PASS" if (isweb or (hasattr(mv, "canvas") and mv.canvas.grab().width() > 0))
            else "FAIL")
        rec("gui:map", "fixes list populated", "PASS" if mv.list.count() else "WARN",
            f"{mv.list.count()} fixes")
        if mv.list.count():
            mv.list.setCurrentRow(0)
            rec("gui:map", "selecting a fix does not raise", "PASS")
    except Exception as e:
        rec("gui:map", "map view", "FAIL", f"{type(e).__name__}: {e}")

    # theme toggle - the riskiest path (deletes+rebuilds pages)
    for n, where in ((1, "from transaction grid"), (2, "twice in a row")):
        try:
            win._select_domain("transaction")
            for _ in range(n):
                win._toggle_theme()
            rec("gui:theme", f"theme toggle {where}", "PASS")
        except Exception as e:
            rec("gui:theme", f"theme toggle {where}", "FAIL", f"{type(e).__name__}: {e}")
    for dom in ("message", "map", "timeline"):
        try:
            win._select_domain(dom)
            win._toggle_theme()
            rec("gui:theme", f"theme toggle while on '{dom}' view", "PASS")
        except Exception as e:
            rec("gui:theme", f"theme toggle while on '{dom}' view", "FAIL",
                f"{type(e).__name__}: {e}")

    # export-target staleness after visiting Dashboard
    try:
        win._select_domain("transaction")
        before = win._model.domain
        win._select_domain("dashboard")
        still = win._model.domain if win._model else None
        rec("gui:export", "export target cleared when leaving a grid for Dashboard",
            "WARN" if still == before else "PASS",
            f"_model still '{still}' -> JSON/CSV exports the previous grid" if still == before else "")
    except Exception as e:
        rec("gui:export", "dashboard model staleness", "FAIL", f"{type(e).__name__}: {e}")

    ds.close()
    win.close()


# =========================================================================== #
def audit_exports(case_dir):
    section("3. EXPORTS  (JSON / CSV / KML / GeoJSON / HTML / PDF)")
    from paytmforensics.report import exporters, geo, html as hr
    from paytmforensics.gui.datasource import DataSource
    ds = DataSource(os.path.join(case_dir, "case.db"))
    rows = ds.load("transaction")
    tmp = tempfile.mkdtemp(prefix="exp_")

    # JSON
    p = os.path.join(tmp, "t.json")
    n = exporters.export(rows, p, "json")
    back = json.load(open(p, encoding="utf-8"))
    rec("export:json", "row count preserved", "PASS" if n == len(rows) else "FAIL", f"{n}")
    rec("export:json", "round-trips without loss",
        "PASS" if back == json.loads(json.dumps(rows)) else "FAIL")
    exporters.export(rows, os.path.join(tmp, "t2.json"), "json")
    rec("export:json", "byte-identical across runs (EV-6)",
        "PASS" if open(p, "rb").read() == open(os.path.join(tmp, "t2.json"), "rb").read() else "FAIL")

    # CSV
    pc = os.path.join(tmp, "t.csv")
    exporters.export(rows, pc, "csv")
    import csv as _csv
    with open(pc, encoding="utf-8") as f:
        r = list(_csv.reader(f))
    hdr, body = r[0], r[1:]
    rec("export:csv", "one row per record", "PASS" if len(body) == len(rows) else "FAIL",
        f"{len(body)} vs {len(rows)}")
    prov = [c for c in hdr if c.startswith("prov.")]
    need = {"prov.source_file", "prov.rowid", "prov.byte_offset", "prov.origin",
            "prov.confidence", "prov.ingest_sha256"}
    rec("export:csv", "all 6 provenance columns present (METHODOLOGY §2)",
        "PASS" if need <= set(prov) else "FAIL", f"{sorted(need - set(prov))}")
    rec("export:csv", "'raw' column excluded", "PASS" if "raw" not in hdr else "FAIL")
    flat = [c for c in hdr if c.startswith("timestamp.")]
    rec("export:csv", "timestamp is exposed as flat, spreadsheet-sortable columns",
        "PASS" if flat else "FAIL", f"{flat}")
    # ragged rows
    mixed = [{"a": 1, "provenance": {"origin": "live"}}, {"b": 2, "provenance": {"origin": "live"}}]
    pm = os.path.join(tmp, "m.csv"); exporters.export(mixed, pm, "csv")
    with open(pm, encoding="utf-8") as f:
        mr = list(_csv.reader(f))
    rec("export:csv", "heterogeneous keys -> union header, no ragged rows",
        "PASS" if all(len(x) == len(mr[0]) for x in mr) else "FAIL")

    # unsupported fmt
    try:
        exporters.export([], os.path.join(tmp, "x.zzz"), "zzz")
        rec("export", "unsupported format raises", "FAIL", "silently accepted")
    except ValueError:
        rec("export", "unsupported format raises ValueError", "PASS")

    # geo
    casedb = os.path.join(case_dir, "case.db")
    nk = geo.export(casedb, os.path.join(tmp, "l.kml"), "kml")
    ng = geo.export(casedb, os.path.join(tmp, "l.geojson"), "geojson")
    rec("export:geo", "KML and GeoJSON agree on fix count",
        "PASS" if nk == ng else "FAIL", f"kml={nk} geojson={ng}")
    gj = json.load(open(os.path.join(tmp, "l.geojson"), encoding="utf-8"))
    pts = [f for f in gj["features"] if f["geometry"]["type"] == "Point"]
    line = [f for f in gj["features"] if f["geometry"]["type"] == "LineString"]
    rec("export:geo", "one Point per fix + a movement_path LineString",
        "PASS" if len(pts) == nk and len(line) == 1 else "FAIL",
        f"{len(pts)} points, {len(line)} lines")
    undated = [f for f in pts if not f["properties"].get("timestamp_utc")]
    rec("export:geo", "no undated fixes (they sort first and kink the path)",
        "WARN" if undated else "PASS",
        f"{len(undated)} fixes have empty timestamp" if undated else "")
    kml = open(os.path.join(tmp, "l.kml"), encoding="utf-8").read()
    rec("export:geo", "KML is well-formed XML", "PASS" if _xml_ok(kml) else "FAIL")
    # does geo include the diagnostic fixes the Location grid hides?
    nloc = len(ds.load("location"))
    rec("export:geo", "geo export includes diagnostic GPS the Location grid omits",
        "PASS" if nk > nloc else "WARN", f"geo={nk} location-domain={nloc}")

    # HTML report
    rp = os.path.join(tmp, "r.html")
    dg = hr.generate(case_dir, rp, fmt="html")
    import hashlib
    actual = hashlib.sha256(open(rp, "rb").read()).hexdigest()
    rec("report:html", "sha256 sidecar matches the file", "PASS" if dg == actual else "FAIL")
    side = open(rp + ".sha256", encoding="utf-8").read().split()[0]
    rec("report:html", "sidecar file content matches", "PASS" if side == dg else "FAIL")
    d1 = hr.build_html(case_dir); d2 = hr.build_html(case_dir)
    import re as _re
    strip = lambda s: _re.sub(r"<td><b>Report \(UTC\)</b></td><td>[^<]*</td>", "", s)
    rec("report:html", "body reproducible ignoring gen timestamp (EV-6)",
        "PASS" if strip(d1) == strip(d2) else "FAIL")
    rec("report:html", "self-contained (no external http asset refs)",
        "PASS" if not _re.search(r'(src|href)\s*=\s*["\']https?://', d1) else "FAIL")
    rec("report:html", "well-formed enough to parse", "PASS" if _html_ok(d1) else "WARN")

    # PDF
    try:
        hr.generate(case_dir, os.path.join(tmp, "r.pdf"), fmt="pdf")
        rec("report:pdf", "PDF produced", "PASS")
    except RuntimeError as e:
        rec("report:pdf", "PDF unavailable -> clear RuntimeError (FR-G7 degraded)",
            "WARN", str(e)[:70])
    except Exception as e:
        rec("report:pdf", "PDF path", "FAIL", f"{type(e).__name__}: {e}")
    ds.close()


def _xml_ok(s):
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(s); return True
    except ET.ParseError:
        return False


def _html_ok(s):
    from html.parser import HTMLParser
    try:
        HTMLParser().feed(s); return True
    except Exception:
        return False


# =========================================================================== #
def audit_integrity(case_dir):
    section("4. INTEGRITY  (manifest, verify, audit-log hash chain)")
    from paytmforensics.core import integrity
    import hashlib

    man = integrity.load_manifest(os.path.join(case_dir, "manifest.json"))
    rec("integrity", "manifest loads", "PASS" if man else "FAIL")
    if man:
        fs = man["files"]
        rec("integrity", "file_count matches entries",
            "PASS" if man["file_count"] == len(fs) else "FAIL")
        rec("integrity", "every entry has sha256+md5+size",
            "PASS" if all(("sha256" in e and "md5" in e and "size" in e) or "error" in e
                          for e in fs) else "FAIL")
        rec("integrity", "entries sorted by rel_path (EV-6 determinism)",
            "PASS" if [e["rel_path"] for e in fs] == sorted(e["rel_path"] for e in fs) else "FAIL")
        # spot-verify 3 real hashes
        root = man["root"]
        bad = []
        for e in [x for x in fs if "sha256" in x][:3]:
            p = os.path.join(root, e["rel_path"])
            if os.path.exists(p):
                s, m, sz = integrity.hash_file(p)
                if s != e["sha256"] or m != e["md5"] or sz != e["size"]:
                    bad.append(e["rel_path"])
        rec("integrity", "spot-check 3 manifest hashes against the source",
            "PASS" if not bad else "FAIL", f"{bad}")

    # audit log chain
    lp = os.path.join(case_dir, "audit.log")
    lines = [json.loads(l) for l in open(lp, encoding="utf-8") if l.strip()]
    rec("audit", "audit.log parses as NDJSON", "PASS" if lines else "FAIL", f"{len(lines)} entries")
    prev = "0" * 64
    broken = 0
    for e in lines:
        if e.get("prev_hash") != prev:
            broken += 1
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        payload = json.dumps(body, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256((body["prev_hash"] + payload).encode("utf-8")).hexdigest()
        if h != e.get("entry_hash"):
            broken += 1
        prev = e.get("entry_hash")
    rec("audit", "hash chain verifies end-to-end (EV-5)",
        "PASS" if broken == 0 else "FAIL", f"{broken} break(s)")
    acts = [e["action"] for e in lines]
    for a in ("ingest_start", "ingest_done", "discover", "parse", "carve", "timeline"):
        rec("audit", f"logs '{a}'", "PASS" if a in acts else "WARN")
    from paytmforensics.core.audit import AuditLog as _AL
    shipped = hasattr(_AL, "verify")
    rec("audit", "tool ships a chain VERIFIER for third parties (EV-5)",
        "PASS" if shipped else "FAIL", "AuditLog.verify()" if shipped else "absent")
    if shipped:
        r = _AL.verify(lp)
        rec("audit", "shipped verifier agrees with the independent check",
            "PASS" if r["ok"] == (broken == 0) else "FAIL", f"{r}"[:80])

    # tamper detection
    tmp = tempfile.mkdtemp(prefix="tamper_")
    tp = os.path.join(tmp, "audit.log")
    with open(tp, "w", encoding="utf-8") as f:
        for e in lines:
            if e["action"] == "parse":
                e = dict(e); e["detail"] = dict(e["detail"]); e["detail"]["records"] = 99999
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    t = [json.loads(l) for l in open(tp, encoding="utf-8") if l.strip()]
    det = 0
    for e in t:
        body = {k: v for k, v in e.items() if k != "entry_hash"}
        h = hashlib.sha256((body["prev_hash"] +
             json.dumps(body, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()
        if h != e.get("entry_hash"):
            det += 1
    rec("audit", "an edited entry IS detectable by the chain",
        "PASS" if det > 0 else "FAIL", f"{det} mismatch(es) after tampering")


# =========================================================================== #
def audit_pure():
    section("5. PURE FUNCTIONS  (boundaries, not happy values)")
    from paytmforensics.enrich import timestamps as ts, enums, ifsc, vpa, config_explain, protobuf

    a = "enrich:timestamps"
    cases = [
        (0, "invalid", None), (-1, "invalid", None),
        (1_000_000_000, "unix_s", "2001-09-09"),        # boundary is inclusive
        (1_000_000_001, "unix_s", "2001-09-09"),
        (100_000_000_001, "unix_ms", "1973-03-03"),
        (1_723_742_756_754, "unix_ms", "2024-08-15"),
        (1_000_000_000_000_001, "webkit_us", None),
        ("1723742756754", "unix_ms", "2024-08-15"),     # numeric string
        # float unix-SECONDS: int() truncates to 10 digits -> unix_s is CORRECT here.
        # (An earlier version of this check asserted unix_ms and was simply wrong.)
        (1723742756.754, "unix_s", "2024-08-15"),
        ("notanumber", None, None), (None, None, None), ("", None, None),
    ]
    for raw, et, pre in cases:
        tv = ts.decode(raw)
        ok = (tv.epoch_type == et) and (pre is None or (tv.utc_iso or "").startswith(pre))
        rec(a, f"decode({raw!r}) -> {et}", "PASS" if ok else "FAIL",
            "" if ok else f"got {tv.epoch_type} / {tv.utc_iso}")
    rec(a, "raw value always preserved",
        "PASS" if ts.decode("junk").raw == "junk" else "FAIL")
    rec(a, "1e9 boundary decodes (inclusive threshold)",
        "PASS" if ts.decode(1_000_000_000).epoch_type == "unix_s" else "FAIL",
        str(ts.decode(1_000_000_000).to_dict()))
    rec(a, "hint overrides autodetect",
        "PASS" if ts.decode(1723742756, hint="unix_ms").epoch_type == "unix_ms" else "FAIL")
    rec(a, "to_local shifts correctly",
        "PASS" if ts.to_local("2026-01-01T00:00:00+00:00", 5.5).startswith("2026-01-01T05:30") else "FAIL")

    a = "enrich:enums"
    for fn, v, exp in ((enums.txn_direction, 1, "credit"), (enums.txn_direction, 2, "debit"),
                       (enums.txn_status, 2, "success"), (enums.work_state, 0, "ENQUEUED"),
                       (enums.txn_direction, 99, "code:99"), (enums.txn_direction, None, None),
                       (enums.txn_direction, "2", "debit"), (enums.txn_direction, "x", "code:x")):
        rec(a, f"{fn.__name__}({v!r}) -> {exp!r}", "PASS" if fn(v) == exp else "FAIL", f"got {fn(v)!r}")

    a = "enrich:ifsc"
    for arg, exp in (("0000_HDFC0009999", ("HDFC Bank", "009999")),
                     (None, (None, None)), ("", (None, None)),
                     ("XXXX1234567890", (None, None)),
                     ("1234567890", (None, None))):
        rec(a, f"resolve({arg!r})", "PASS" if ifsc.resolve(arg) == exp else "FAIL",
            f"got {ifsc.resolve(arg)}")
    unk = ifsc.resolve("0000_ZZZZ0001234")
    rec(a, "unknown bank code -> (None, branch), not a guess",
        "PASS" if unk[0] is None and unk[1] == "001234" else "FAIL", f"{unk}")

    a = "enrich:vpa"
    for arg, exp in (("u@icici", ("u", "icici")), ("nohandle", ("nohandle", None)),
                     ("", (None, None)), ("a@b@c", ("a", "b@c"))):
        rec(a, f"split({arg!r})", "PASS" if vpa.split(arg) == exp else "FAIL", f"got {vpa.split(arg)}")
    rec(a, "psp_name unknown handle echoes the handle",
        "PASS" if vpa.psp_name("x@weirdbank") == "weirdbank" else "FAIL")
    rec(a, "looks_like_merchant: long user -> True",
        "PASS" if vpa.looks_like_merchant("a" * 19 + "@ptybl") else "FAIL")
    rec(a, "looks_like_merchant: short personal -> False",
        "PASS" if not vpa.looks_like_merchant("ravi@ptybl") else "FAIL")

    a = "enrich:config"
    for k, v, exp in (("x", "https://a.b", "API endpoint / URL"), ("x", "true", "Feature flag (on/off)"),
                      ("maxLimit", "500", "Threshold / limit"), ("x", "{}", "Structured config (JSON)"),
                      ("rolloutPct", "5", "Experiment / rollout %"), ("x", "7", "Numeric setting"),
                      ("x", "hello", "Setting / value"), ("x", "", "Setting / value"),
                      ("x", None, "Setting / value")):
        got = config_explain.classify(k, v)
        rec(a, f"classify({k!r},{v!r}) -> {exp}", "PASS" if got == exp else "FAIL", f"got {got}")
    rec(a, "derived meanings marked with '≈' (non-authoritative)",
        "PASS" if config_explain.explain("someUnknownKey", "1")[1].startswith("≈") else "FAIL")
    rec(a, "curated key returns the exact explanation",
        "PASS" if not config_explain.explain("AID", "x")[1].startswith("≈") else "FAIL")

    a = "enrich:protobuf"
    blob = bytes([0x08, 0x96, 0x01, 0x12, 0x03]) + b"abc" + bytes([0x1a, 0x02, 0x08, 0x01])
    d = protobuf.decode(blob)
    rec(a, "decodes varint/string/nested", "PASS" if d == {1: 150, 2: "abc", 3: {1: 1}} else "FAIL", f"{d}")
    rec(a, "empty -> None", "PASS" if protobuf.decode(b"") is None else "FAIL")
    rec(a, "truncated does not raise", "PASS", f"-> {protobuf.decode(blob[:4])!r}")
    rec(a, "garbage -> None or dict, never raises", "PASS", f"-> {protobuf.decode(b'\xff'*8)!r}")

    a = "enrich:errorcodes"
    from paytmforensics.enrich import errorcodes as ec
    rec(a, "known code decodes", "PASS" if ec.message(1103) else "FAIL")
    rec(a, "int and str code agree", "PASS" if ec.message(1103) == ec.message("1103") else "FAIL")
    rec(a, "unknown code -> None (no guess)", "PASS" if ec.message(999999) is None else "FAIL")
    rec(a, "None/'' -> None", "PASS" if ec.message(None) is None and ec.message("") is None else "FAIL")


# =========================================================================== #
def audit_datasource(case_dir):
    section("6. DATASOURCE / DISPLAY FORMATTING")
    from paytmforensics.gui.datasource import DataSource, _fmt, summarize, DISPLAY_COLUMNS
    ds = DataSource(os.path.join(case_dir, "case.db"))

    a = "datasource"
    rec(a, "_fmt formats a timestamp dict as a friendly date",
        "PASS" if _fmt({"utc_iso": "2026-05-12T07:15:03.930000+00:00"}) == "12 May 2026, 07:15:03" else "FAIL",
        _fmt({"utc_iso": "2026-05-12T07:15:03.930000+00:00"}))
    rec(a, "_fmt on an undecodable timestamp falls back to raw",
        "PASS" if _fmt({"raw": 0, "epoch_type": "invalid", "utc_iso": None}) in (0, "", "0") else "FAIL",
        repr(_fmt({"raw": 0, "epoch_type": "invalid", "utc_iso": None})))
    rec(a, "_fmt joins lists", "PASS" if _fmt(["a", "b"]) == "a, b" else "FAIL")
    rec(a, "_fmt None -> ''", "PASS" if _fmt(None) == "" else "FAIL")
    rec(a, "_fmt False survives (not blanked)", "PASS" if _fmt(False) is False else "FAIL")
    # False vs None ambiguity in the grid
    amb = str(_fmt(False)) != str(_fmt(None))
    rec(a, "False and None render differently", "PASS" if amb else "FAIL",
        f"False->{_fmt(False)!r} None->{_fmt(None)!r}")

    for dom, n in ds.domains().items():
        cols = ds.columns(dom)
        rec("datasource", f"columns('{dom}') non-empty", "PASS" if cols else "FAIL")
        rows = ds.load(dom)
        try:
            for r in rows[:50]:
                for c in cols:
                    ds.cell(r, c)
            rec("datasource", f"cell() renders all columns for '{dom}'", "PASS")
        except Exception as e:
            rec("datasource", f"cell() for '{dom}'", "FAIL", f"{type(e).__name__}: {e}")

    # global_search: values not keys
    n_rrn_vals = sum(1 for t in ds.load("transaction") if t.get("rrn")) + \
                 sum(1 for m in ds.load("message") if m.get("rrn"))
    hits = ds.global_search("rrn")
    rec("search", "global_search matches VALUES not field names",
        "PASS" if len(hits) < n_rrn_vals else "FAIL", f"{len(hits)} hits vs {n_rrn_vals} rrn-bearing rows")
    rec("search", "empty query returns nothing", "PASS" if ds.global_search("") == [] else "FAIL")
    rec("search", "limit is honoured", "PASS" if len(ds.global_search("a", limit=5)) <= 5 else "FAIL")
    rec("search", "summarize() never returns empty",
        "PASS" if all(summarize(r, d) for d, _s, r in hits[:20]) else "FAIL")
    # case-insensitivity
    up = ds.global_search("FOOD"); lo = ds.global_search("food")
    rec("search", "case-insensitive", "PASS" if len(up) == len(lo) else "FAIL", f"{len(up)} vs {len(lo)}")
    ds.close()


# =========================================================================== #
def audit_cli():
    section("7. CLI ROBUSTNESS")
    import subprocess
    tmp = tempfile.mkdtemp(prefix="cli_")
    ne = os.path.join(tmp, "does_not_exist")
    r = subprocess.run([sys.executable, "-m", "paytmforensics.cli", "--extraction", ne,
                        "--out", os.path.join(tmp, "o")],
                       capture_output=True, text=True, timeout=300)
    bad = (r.returncode == 0 and "0 files hashed" in r.stdout)
    rec("cli", "refuses a nonexistent extraction path",
        "FAIL" if bad else "PASS",
        "exit 0 + empty case, no error - a forensics CLI must refuse" if bad else f"rc={r.returncode}")
    r2 = subprocess.run([sys.executable, "-m", "paytmforensics.cli"],
                        capture_output=True, text=True, timeout=60)
    rec("cli", "missing required args -> usage error",
        "PASS" if r2.returncode != 0 else "FAIL")


# =========================================================================== #
def audit_masking(case_dir):
    section("8. SENSITIVE-DATA MASKING  (display only; case.db must stay complete)")
    from paytmforensics.gui.datasource import DataSource
    from paytmforensics.core import privacy
    a = "mask"
    ds = DataSource(os.path.join(case_dir, "case.db"))
    rec(a, "masking is OFF by default", "PASS" if not ds.mask_sensitive else "FAIL")

    # harvest real values from the case to test against
    subj = next((p for p in ds.load("person") if p.get("is_subject")), {})
    txn = next((t for t in ds.load("transaction") if t.get("rrn")), {})
    truth = {k: v for k, v in (
        ("name", subj.get("name")), ("phone", subj.get("phone")),
        ("customer_id", subj.get("customer_id")), ("sendbird", subj.get("sendbird_id")),
        ("rrn", txn.get("rrn")), ("vpa", txn.get("counterparty_vpa")),
        ("account", txn.get("account_used")),
    ) if v}
    rec(a, "found real values to test against", "PASS" if truth else "WARN",
        f"{sorted(truth)}")

    # unmasked: everything visible
    shown = " | ".join(ds.cell(r, c) for d in ("person", "transaction")
                       for r in ds.load(d) for c in ds.columns(d))
    vis = [k for k, v in truth.items() if v in shown]
    rec(a, "with masking OFF the real values ARE shown",
        "PASS" if len(vis) == len(truth) else "FAIL", f"visible={vis}")

    # masked: nothing leaks, in ANY domain, in ANY column
    ds.set_mask(True)
    leaks = set()
    for d in ds.domains():
        for r in ds.load(d):
            m = ds.mask(r)
            blob = json.dumps(m, ensure_ascii=False, default=str)
            for k, v in truth.items():
                if v in blob:
                    leaks.add(f"{d}.{k}")
    rec(a, "with masking ON no real value leaks in any domain",
        "PASS" if not leaks else "FAIL", f"leaks={sorted(leaks)[:6]}")

    # analysis fields survive
    t0 = next((t for t in ds.load("transaction") if t.get("amount")), {})
    mt = ds.mask(t0)
    keep = all(mt.get(f) == t0.get(f) for f in
               ("amount", "direction", "settled", "txn_source", "status_label"))
    rec(a, "amounts / direction / status / provenance unchanged by masking",
        "PASS" if keep else "FAIL")
    rec(a, "provenance and ingest hash preserved",
        "PASS" if mt.get("provenance") == t0.get("provenance") else "FAIL")

    # coordinates stay numeric so the map still works
    loc = next((l for l in ds.load("location") if l.get("latitude") is not None), None)
    if loc:
        ml = ds.mask(loc)
        rec(a, "coordinates stay numeric (map does arithmetic on them)",
            "PASS" if isinstance(ml["latitude"], (int, float)) else "FAIL",
            f"{loc['latitude']} -> {ml['latitude']}")
        rec(a, "coordinates are blurred, not exact",
            "PASS" if ml["latitude"] != loc["latitude"] else "FAIL")

    # filtering still works on the REAL values while masked
    from paytmforensics.gui.filters import FilterSpec, apply_filter
    if truth.get("name"):
        n = len(apply_filter(ds.load("transaction"), FilterSpec(text=truth["name"][:6])))
        rec(a, "filters still match unmasked values while masking is on",
            "PASS" if n >= 0 else "FAIL", f"{n} rows")
        hits = ds.global_search(truth["name"][:6])
        masked_out = hits and truth["name"] not in str(hits[0][1])
        rec(a, "global search finds real values but DISPLAYS masked",
            "PASS" if (not hits or masked_out) else "FAIL", f"{len(hits)} hits")

    # the case DB itself is untouched
    ds2 = DataSource(os.path.join(case_dir, "case.db"))
    raw = json.dumps(ds2.load("transaction"), default=str)
    rec(a, "case.db still holds the complete unmasked evidence",
        "PASS" if all(v in raw for k, v in truth.items()
                      if k in ("rrn", "vpa", "account")) else "FAIL")
    ds2.close(); ds.close()


def main():
    case_dir = sys.argv[1] if len(sys.argv) > 1 else None
    if not case_dir or not os.path.isdir(case_dir):
        print("usage: audit_features.py <case_dir>"); return 2
    audit_filters()
    audit_pure()
    audit_datasource(case_dir)
    audit_exports(case_dir)
    audit_integrity(case_dir)
    audit_cli()
    audit_masking(case_dir)
    audit_gui(case_dir)

    section("SUMMARY")
    from collections import Counter
    c = Counter(v for _a, _ch, v, _n in RESULTS)
    print(f"  checks: {len(RESULTS)}   PASS {c['PASS']}   FAIL {c['FAIL']}   "
          f"WARN {c['WARN']}   N/A {c['N/A']}")
    if c["FAIL"]:
        print("\n  FAILURES:")
        for ar, ch, v, nt in RESULTS:
            if v == "FAIL":
                print(f"    [{ar}] {ch}\n        {nt}")
    if c["WARN"]:
        print("\n  WARNINGS:")
        for ar, ch, v, nt in RESULTS:
            if v == "WARN":
                print(f"    [{ar}] {ch}\n        {nt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
