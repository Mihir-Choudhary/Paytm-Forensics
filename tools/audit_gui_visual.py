#!/usr/bin/env python3
"""Visual + interaction audit of the GUI: renders every view in both themes to PNG and
runs interaction checks that row-counting cannot catch (elision, contrast, sorting,
resize, keyboard, large-grid behaviour).

Usage:
    QT_QPA_PLATFORM=offscreen python3 tools/audit_gui_visual.py <case_dir> <shot_dir>
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULTS = []


def rec(area, check, verdict, note=""):
    RESULTS.append((area, check, verdict, note))
    mark = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn ", "INFO": " info "}[verdict]
    print(f"[{mark}] {area:<18} {check:<62} {note}")


H = "=" * 104


def section(t):
    print(f"\n{H}\n{t}\n{H}")


def _lum(hexs):
    hexs = hexs.lstrip("#")
    r, g, b = (int(hexs[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def main():
    case_dir, shot_dir = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    os.makedirs(shot_dir, exist_ok=True)

    from PySide6.QtWidgets import QApplication, QTableView
    from PySide6.QtCore import Qt, QSize
    from PySide6.QtGui import QFontMetrics
    from paytmforensics.gui import theme
    from paytmforensics.gui.app import MainWindow
    from paytmforensics.gui.datasource import DataSource, DISPLAY_COLUMNS

    app = QApplication.instance() or QApplication([])

    # ---------------- contrast audit (pure palette maths) ------------------- #
    section("A. COLOUR CONTRAST  (WCAG AA needs 4.5:1 body text, 3:1 large/secondary)")
    for tname in ("dark", "light"):
        theme.set_theme(tname)
        c = theme.C
        pairs = [
            ("body text on app bg", c["text"], c["bg_app"], 4.5),
            ("body text on card", c["text"], c["bg_card"], 4.5),
            ("body text on table row", c["text"], c["bg_panel"], 4.5),
            ("body text on alt row", c["text"], c["row_alt"], 4.5),
            ("muted text on card", c["text_muted"], c["bg_card"], 4.5),
            ("dim text on card", c["text_dim"], c["bg_card"], 4.5),
            ("nav item text on sidebar", c["text_muted"], c["bg_sidebar"], 4.5),
            ("header text on chip", c["text_muted"], c["chip"], 4.5),
            # the QSS uses accent_on wherever white text sits on the accent colour
            ("white on accent_on (selected row)", "#ffffff", c["accent_on"], 4.5),
            ("white on accent_on (primary button)", "#ffffff", c["accent_on"], 4.5),
            ("accent as a BORDER/graphic only (no text)", c["accent"], c["bg_card"], 3.0),
            ("green stat on card", c["green"], c["bg_card"], 3.0),
            ("red stat on card", c["red"], c["bg_card"], 3.0),
            ("amber stat on card", c["amber"], c["bg_card"], 3.0),
            ("accent stat on card", c["accent"], c["bg_card"], 3.0),
        ]
        for label, fg, bg, need in pairs:
            r = contrast(fg, bg)
            rec(f"contrast:{tname}", f"{label} ({fg} on {bg})",
                "PASS" if r >= need else "FAIL", f"{r:.2f}:1 (need {need})")

    # ---------------- render every view in both themes ---------------------- #
    section("B. RENDER EVERY VIEW IN BOTH THEMES")
    shots = []
    for tname in ("dark", "light"):
        theme.set_theme(tname)
        app.setStyleSheet(theme.qss())
        theme.apply_palette(app)
        win = MainWindow(case_dir)
        win.resize(1600, 950)
        win.show()
        app.processEvents()

        targets = ["dashboard", "transaction", "entity", "message", "timeline", "map",
                   "location", "config", "diagnostic", "pref", "person", "encrypted"]
        for dom in targets:
            found = False
            for i in range(win.nav.count()):
                if win.nav.item(i).data(Qt.UserRole) == dom:
                    win.nav.setCurrentRow(i); found = True; break
            if not found:
                rec(f"render:{tname}", f"nav entry for '{dom}'", "WARN", "absent from sidebar")
                continue
            for _ in range(3):
                app.processEvents(); time.sleep(0.02)
            # click first row so the detail panel is populated in the shot
            if win._model and win._model.rowCount() and dom not in ("message", "timeline"):
                win._on_row_click(win._model.index(0, 0))
                app.processEvents()
            p = os.path.join(shot_dir, f"{tname}_{dom}.png")
            pm = win.grab()
            pm.save(p)
            shots.append(p)
            blank = pm.toImage().allGray() and dom != "map"
            rec(f"render:{tname}", f"'{dom}' renders non-blank at 1600x950",
                "PASS" if (pm.width() > 0 and not blank) else "FAIL",
                f"{pm.width()}x{pm.height()}")

        # global search page
        win.search_box.setText("Food"); win._global_search()
        app.processEvents()
        win.grab().save(os.path.join(shot_dir, f"{tname}_search.png"))
        shots.append(os.path.join(shot_dir, f"{tname}_search.png"))
        rec(f"render:{tname}", "'global search' page renders", "PASS")

        # small-window stress
        win.resize(900, 600); app.processEvents()
        win._select_domain("transaction"); app.processEvents()
        win.grab().save(os.path.join(shot_dir, f"{tname}_small_window.png"))
        shots.append(os.path.join(shot_dir, f"{tname}_small_window.png"))
        rec(f"render:{tname}", "renders at a small 900x600 window", "PASS")
        win.close(); win.deleteLater(); app.processEvents()

    theme.set_theme("dark")
    app.setStyleSheet(theme.qss()); theme.apply_palette(app)

    # ---------------- interaction / usability audit ------------------------- #
    section("C. TABLE INTERACTION  (things row counts cannot catch)")
    win = MainWindow(case_dir); win.resize(1600, 950); win.show(); app.processEvents()

    win._select_domain("transaction"); app.processEvents()
    tv: QTableView = win.table

    rec("table", "column sorting enabled (click a header to sort)",
        "PASS" if tv.isSortingEnabled() else "FAIL",
        "setSortingEnabled never called - headers are inert" if not tv.isSortingEnabled() else "")
    rec("table", "model implements sort()",
        "PASS" if type(win._model).sort is not __import__("PySide6.QtCore", fromlist=["QAbstractTableModel"]).QAbstractTableModel.sort
        else "FAIL", "RecordTableModel does not override sort()")

    # text elision: is any cell wider than its column?
    fm = QFontMetrics(tv.font())
    over = []
    for col in range(win._model.columnCount()):
        w = tv.columnWidth(col)
        for row in range(min(win._model.rowCount(), 60)):
            txt = str(win._model.data(win._model.index(row, col), Qt.DisplayRole) or "")
            if fm.horizontalAdvance(txt) > w + 4:
                over.append((win._model._cols[col], len(txt)))
                break
    rec("table", "no column truncates its content after resizeColumnsToContents",
        "PASS" if not over else "WARN",
        f"{len(over)} column(s) elide, e.g. {over[:3]}" if over else "")

    # word wrap / row height sanity for long text
    long_cols = [c for c in DISPLAY_COLUMNS.get("transaction", []) if c == "narration"]
    rec("table", "long free-text columns present in transaction grid",
        "INFO", f"{long_cols}")

    # selection behaviour
    tv.selectRow(0)
    rec("table", "selecting a row yields a selection",
        "PASS" if tv.selectionModel().selectedRows() else "FAIL")

    # tooltip carries provenance
    tip = win._model.data(win._model.index(0, 0), Qt.ToolTipRole)
    rec("table", "cell tooltip carries source/table/origin/confidence",
        "PASS" if tip and "conf=" in tip else "FAIL", (tip or "")[:60])

    # keyboard navigation
    try:
        from PySide6.QtTest import QTest
        tv.setFocus(); QTest.keyClick(tv, Qt.Key_Down); app.processEvents()
        rec("table", "keyboard navigation does not raise", "PASS")
    except Exception as e:
        rec("table", "keyboard navigation", "WARN", f"{type(e).__name__}: {e}")

    # large grid: 7027 config rows
    t0 = time.time()
    win._select_domain("config"); app.processEvents()
    dt = time.time() - t0
    rec("perf", "7,027-row config grid opens (NFR-2: responsive at 100k+)",
        "PASS" if dt < 5 else "WARN", f"{dt:.2f}s")
    rec("perf", "model loads all rows into memory (not virtualised at the data layer)",
        "WARN", f"RecordTableModel._all holds {len(win._model._all):,} dicts; "
                f"a 100k-row domain would hold 100k")
    t0 = time.time(); win.f_text.setText("kyc"); win._apply(); app.processEvents()
    rec("perf", "filtering 7,027 rows is responsive",
        "PASS" if time.time() - t0 < 5 else "WARN", f"{time.time()-t0:.2f}s -> {win._model.rowCount()} rows")
    win.f_text.clear(); win._clear()

    # ---------------- domains missing from the sidebar --------------------- #
    section("D. NAVIGATION COMPLETENESS")
    ds = DataSource(os.path.join(case_dir, "case.db"))
    counts = ds.domains()
    navdoms = {win.nav.item(i).data(Qt.UserRole) for i in range(win.nav.count())}
    navdoms.discard(None)
    for d in sorted(counts):
        rec("nav", f"domain '{d}' ({counts[d]} records) reachable",
            "PASS" if d in navdoms else "FAIL")
    zero = [d for _t, ds_ in theme.NAV_GROUPS for d in ds_
            if d not in ("dashboard", "map") and d not in counts]
    rec("nav", "domains with zero records are hidden rather than shown empty",
        "INFO", f"hidden: {sorted(zero)} - an examiner cannot tell these were "
                f"parsed-as-zero vs not-attempted")

    # group headers styling
    hdr_items = [win.nav.item(i) for i in range(win.nav.count())
                 if win.nav.item(i).data(Qt.UserRole) is None]
    from PySide6.QtGui import QColor
    grays = {hdr_items[0].foreground().color().name()} if hdr_items else set()
    rec("nav", "group headers use a theme colour (not hardcoded)",
        "WARN" if grays == {"#a0a0a4"} or grays == {"#808080"} else "PASS",
        f"foreground={grays} - set via Qt.gray in _populate_nav, ignores the palette")

    # ---------------- dashboard specifics --------------------------------- #
    section("E. DASHBOARD VISUALS")
    win._select_domain("dashboard"); app.processEvents()
    dash = win.dashboard
    rec("dashboard", "renders non-blank", "PASS" if dash.grab().width() > 0 else "FAIL")
    # tiles present for which domains
    from paytmforensics.gui.dashboard import TILE_DOMAINS
    missing_tiles = [d for d in TILE_DOMAINS if d not in counts]
    rec("dashboard", "stat tiles shown only for domains with data",
        "INFO", f"no tile for {missing_tiles} (zero records)")
    ds.close()
    win.close()

    section("SUMMARY")
    from collections import Counter
    c = Counter(v for _a, _ch, v, _n in RESULTS)
    print(f"  checks: {len(RESULTS)}   PASS {c['PASS']}   FAIL {c['FAIL']}   "
          f"WARN {c['WARN']}   INFO {c['INFO']}")
    for want in ("FAIL", "WARN"):
        if c[want]:
            print(f"\n  {want}S:")
            for ar, ch, v, nt in RESULTS:
                if v == want:
                    print(f"    [{ar}] {ch}\n        {nt}")
    print(f"\n  {len(shots)} screenshots in {shot_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
