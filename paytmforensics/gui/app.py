"""PaytmForensics desktop GUI — modern dashboard + sidebar layout (FR-G1..FR-G8)."""
from __future__ import annotations

import json
import os
import sys

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QTableView, QLineEdit, QComboBox, QLabel, QSplitter,
    QTextEdit, QPushButton, QFileDialog, QFrame, QMessageBox, QStatusBar,
    QStackedWidget, QHeaderView, QGridLayout,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

# Import WebEngine before QApplication is created (required by Qt) so the map view works.
try:
    from PySide6 import QtWebEngineWidgets  # noqa: F401
    _HAS_WEBENGINE = True
except Exception:
    _HAS_WEBENGINE = False

from .datasource import DataSource, DOMAIN_LABELS
from .filters import FilterSpec
from .models import RecordTableModel
from .theme import qss, C, DOMAIN_STYLE, NAV_GROUPS, set_theme, current_theme, apply_palette
from .dashboard import Dashboard


class MainWindow(QMainWindow):
    def __init__(self, case_dir: str):
        super().__init__()
        self.case_dir = case_dir
        self.ds = DataSource(os.path.join(case_dir, "case.db"))
        self.meta = self._load_meta()
        self.setWindowTitle("PaytmForensics")
        self.resize(1440, 880)
        self._model: RecordTableModel | None = None
        self.setStyleSheet(qss())
        self._build()
        self._populate_nav()

    def _load_meta(self):
        try:
            return json.load(open(os.path.join(self.case_dir, "case_meta.json"), encoding="utf-8"))
        except Exception:
            return {}

    # ------------------------------------------------------------------ build #
    def _build(self):
        root = QWidget(); root.setObjectName("root")
        h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(self._sidebar())

        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        rv.addWidget(self._topbar())
        self.stack = QStackedWidget()
        self.stack.addWidget(self._dashboard_page())   # index 0
        self.stack.addWidget(self._table_page())        # index 1
        self._chat_page = None                          # lazy
        self._map_page = None                           # lazy
        self._search_page = None                        # lazy
        self._timeline_page = None                       # lazy
        rv.addWidget(self.stack, 1)
        h.addWidget(right, 1)

        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready")

    def _sidebar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("sidebar"); bar.setFixedWidth(252)
        v = QVBoxLayout(bar); v.setContentsMargins(0, 8, 0, 8); v.setSpacing(0)
        brand = QLabel("⬢  PaytmForensics"); brand.setObjectName("brand")
        sub = QLabel("UPI / Android evidence analyzer"); sub.setObjectName("brandSub")
        v.addWidget(brand); v.addWidget(sub)

        self.nav = QListWidget(); self.nav.setObjectName("nav")
        self.nav.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.nav.currentItemChanged.connect(self._on_nav)
        v.addWidget(self.nav, 1)
        return bar

    def _topbar(self) -> QWidget:
        bar = QWidget(); bar.setObjectName("topbar"); bar.setFixedHeight(64)
        h = QHBoxLayout(bar); h.setContentsMargins(24, 0, 20, 0); h.setSpacing(10)
        self.title = QLabel("Dashboard"); self.title.setObjectName("pageTitle")
        h.addWidget(self.title)
        h.addStretch()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("🔍  Search everything…")
        self.search_box.setFixedWidth(240)
        self.search_box.returnPressed.connect(self._global_search)
        h.addWidget(self.search_box)
        case_lbl = QLabel(f"  {self.meta.get('case_id','—')}  ")
        case_lbl.setObjectName("caseBadge")
        exm_lbl = QLabel(f"  {self.meta.get('examiner','—')}  ")
        exm_lbl.setObjectName("caseBadge")
        h.addWidget(case_lbl); h.addWidget(exm_lbl)
        self.theme_btn = QPushButton("☀ Light" if current_theme() == "dark" else "🌙 Dark")
        self.theme_btn.clicked.connect(self._toggle_theme)
        rep = QPushButton("Report"); rep.clicked.connect(lambda: self._report("html"))
        ej = QPushButton("JSON"); ej.clicked.connect(lambda: self._export("json"))
        ec = QPushButton("CSV"); ec.clicked.connect(lambda: self._export("csv"))
        rep.setObjectName("primary")
        for b in (self.theme_btn, rep, ej, ec):
            h.addWidget(b)
        self._build_menu()
        return bar

    def _build_menu(self):
        m = self.menuBar().addMenu("&Export")
        m.addAction("Current view → JSON", lambda: self._export("json"))
        m.addAction("Current view → CSV", lambda: self._export("csv"))
        m.addSeparator()
        m.addAction("Locations → KML", lambda: self._geo("kml"))
        m.addAction("Locations → GeoJSON", lambda: self._geo("geojson"))
        m.addSeparator()
        m.addAction("HTML report", lambda: self._report("html"))
        m.addAction("PDF report", lambda: self._report("pdf"))

    def _dashboard_page(self) -> QWidget:
        self.dashboard = Dashboard(self.case_dir, self.ds)
        self.dashboard.open_domain.connect(self._select_domain)
        return self.dashboard

    def _table_page(self) -> QWidget:
        page = QWidget(); v = QVBoxLayout(page)
        v.setContentsMargins(20, 16, 20, 16); v.setSpacing(12)
        v.addWidget(self._filter_card())

        split = QSplitter(Qt.Horizontal)
        self.table = QTableView()
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(self._on_row_click)
        split.addWidget(self.table)

        detail_wrap = QFrame(); detail_wrap.setObjectName("card")
        dl = QVBoxLayout(detail_wrap); dl.setContentsMargins(14, 12, 14, 12)
        dt = QLabel("Record detail & provenance"); dt.setObjectName("cardTitle")
        dl.addWidget(dt)
        self.detail = QTextEdit(); self.detail.setObjectName("detail"); self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a row to view full fields and source attribution.")
        dl.addWidget(self.detail)
        split.addWidget(detail_wrap)
        split.setSizes([920, 430])
        v.addWidget(split, 1)
        return page

    def _filter_card(self) -> QWidget:
        card = QFrame(); card.setObjectName("card")
        g = QGridLayout(card); g.setContentsMargins(16, 12, 16, 12); g.setHorizontalSpacing(10)
        self.f_text = QLineEdit(); self.f_text.setPlaceholderText("🔍  Search all fields…")
        self.f_text.returnPressed.connect(self._apply)
        self.f_from = QLineEdit(); self.f_from.setPlaceholderText("From  YYYY-MM-DD")
        self.f_to = QLineEdit(); self.f_to.setPlaceholderText("To  YYYY-MM-DD")
        self.f_origin = QComboBox(); self.f_origin.addItems(["any", "live", "carved"])
        self.f_amin = QLineEdit(); self.f_amin.setPlaceholderText("min ₹"); self.f_amin.setMaximumWidth(90)
        self.f_amax = QLineEdit(); self.f_amax.setPlaceholderText("max ₹"); self.f_amax.setMaximumWidth(90)
        self.f_dir = QComboBox(); self.f_dir.addItems(["", "credit", "debit"])
        self.f_src = QLineEdit(); self.f_src.setPlaceholderText("source file…")
        apply_btn = QPushButton("Apply"); apply_btn.setObjectName("primary"); apply_btn.clicked.connect(self._apply)
        clear_btn = QPushButton("Clear"); clear_btn.clicked.connect(self._clear)

        g.addWidget(self.f_text, 0, 0, 1, 4)
        g.addWidget(QLabel("Origin"), 0, 4); g.addWidget(self.f_origin, 0, 5)
        g.addWidget(apply_btn, 0, 6); g.addWidget(clear_btn, 0, 7)
        g.addWidget(QLabel("Date"), 1, 0); g.addWidget(self.f_from, 1, 1); g.addWidget(self.f_to, 1, 2)
        g.addWidget(QLabel("Amount"), 1, 3); g.addWidget(self.f_amin, 1, 4); g.addWidget(self.f_amax, 1, 5)
        g.addWidget(QLabel("Dir"), 1, 6); g.addWidget(self.f_dir, 1, 7)
        g.addWidget(self.f_src, 2, 0, 1, 8)
        return card

    # --------------------------------------------------------------- nav/data #
    def _populate_nav(self):
        counts = self.ds.domains()
        for group_title, domains in NAV_GROUPS:
            header = QListWidgetItem(group_title)
            header.setFlags(Qt.NoItemFlags)
            f = QFont("Segoe UI", 8); f.setBold(True)   # explicit size > 0
            header.setFont(f)
            header.setForeground(Qt.gray)
            self.nav.addItem(header)
            for dom in domains:
                if dom == "map":
                    if not (counts.get("location") or counts.get("diagnostic")):
                        continue
                elif dom != "dashboard" and dom not in counts:
                    continue
                glyph, color = DOMAIN_STYLE.get(dom, ("•", C["accent"]))
                if dom == "dashboard":
                    text = f"{glyph}   Dashboard"
                elif dom == "map":
                    text = f"{glyph}   Location map"
                else:
                    text = f"{glyph}   {DOMAIN_LABELS.get(dom, dom)}   ({counts[dom]})"
                it = QListWidgetItem(text)
                it.setData(Qt.UserRole, dom)
                self.nav.addItem(it)
        # select dashboard
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.UserRole) == "dashboard":
                self.nav.setCurrentRow(i); break

    def _select_domain(self, dom: str):
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.UserRole) == dom:
                self.nav.setCurrentRow(i); return

    def _on_nav(self, cur, _prev):
        if not cur:
            return
        dom = cur.data(Qt.UserRole)
        if not dom:
            return
        if dom == "dashboard":
            self.title.setText("Dashboard")
            self.stack.setCurrentIndex(0)
            self.statusBar().showMessage("Overview")
            return
        if dom == "map":
            self._show_map()
            return
        # build the table model regardless (export uses it); show chat view for messages
        self.title.setText(DOMAIN_LABELS.get(dom, dom))
        self._model = RecordTableModel(self.ds, dom)
        self.table.setModel(self._model)
        self.table.resizeColumnsToContents()
        if dom == "message":
            self._show_chat()
        elif dom == "timeline":
            self._show_timeline()
        else:
            self.stack.setCurrentIndex(1)
        self.detail.clear()
        self.statusBar().showMessage(f"{dom}: {self._model.rowCount()} rows")

    def _show_timeline(self):
        from .timelineview import TimelineView
        if getattr(self, "_timeline_page", None) is None:
            self._timeline_page = TimelineView(self.ds)
            self.stack.addWidget(self._timeline_page)
        self.stack.setCurrentWidget(self._timeline_page)

    def _show_chat(self):
        from .chatview import ChatView
        if self._chat_page is None:
            self._chat_page = ChatView(self.ds)
            self.stack.addWidget(self._chat_page)   # index 2
        self.stack.setCurrentWidget(self._chat_page)
        self.title.setText("Chats")

    def _toggle_theme(self):
        new = "light" if current_theme() == "dark" else "dark"
        set_theme(new)
        # the window carries its own stylesheet which overrides the app one — update both
        self.setStyleSheet(qss())
        app = QApplication.instance()
        if app:
            app.setStyleSheet(qss())
            apply_palette(app)          # keep selection/highlight colours correct
        self.theme_btn.setText("☀ Light" if new == "dark" else "🌙 Dark")
        # rebuild widgets that bake colors at construction time
        new_dash = Dashboard(self.case_dir, self.ds)
        new_dash.open_domain.connect(self._select_domain)
        self.stack.insertWidget(0, new_dash)
        self.stack.removeWidget(self.dashboard); self.dashboard.deleteLater()
        self.dashboard = new_dash
        for attr in ("_chat_page", "_map_page", "_timeline_page"):
            pg = getattr(self, attr, None)
            if pg is not None:
                self.stack.removeWidget(pg); pg.deleteLater(); setattr(self, attr, None)
        self._on_nav(self.nav.currentItem(), None)   # refresh current view

    def _show_map(self):
        from .mapview import MapView
        if getattr(self, "_map_page", None) is None:
            self._map_page = MapView(self.ds)
            self.stack.addWidget(self._map_page)
        self.stack.setCurrentWidget(self._map_page)
        self.title.setText("Location map")
        self._model = None
        self.statusBar().showMessage("Location map")

    # ------------------------------------------------------------- behaviour #
    def _current_spec(self) -> FilterSpec:
        def fnum(w):
            try:
                return float(w.text()) if w.text().strip() else None
            except ValueError:
                return None
        return FilterSpec(
            text=self.f_text.text().strip(),
            date_from=self.f_from.text().strip() or None,
            date_to=self.f_to.text().strip() or None,
            origin=self.f_origin.currentText(),
            amount_min=fnum(self.f_amin), amount_max=fnum(self.f_amax),
            direction=self.f_dir.currentText() or None,
            source_contains=self.f_src.text().strip(),
        )

    def _apply(self):
        if self._model:
            self._model.set_filter(self._current_spec())
            self.table.resizeColumnsToContents()
            self.statusBar().showMessage(f"{self._model.rowCount()} rows after filter")

    def _clear(self):
        for w in (self.f_text, self.f_from, self.f_to, self.f_amin, self.f_amax, self.f_src):
            w.clear()
        self.f_origin.setCurrentIndex(0); self.f_dir.setCurrentIndex(0)
        if self._model:
            self._model.set_filter(None)

    def _on_row_click(self, index):
        if not self._model:
            return
        rec = self._model.record_at(index.row())
        p = rec.get("provenance", {})
        head = (f"SOURCE:  {p.get('source_file')}\n"
                f"TABLE :  {p.get('source_table')}\n"
                f"ROWID :  {p.get('rowid')}    OFFSET: {p.get('byte_offset')}\n"
                f"ORIGIN:  {p.get('origin')}    CONFIDENCE: {p.get('confidence')}\n"
                f"SHA256:  {p.get('ingest_sha256')}\n" + "─" * 46 + "\n")
        body = json.dumps({k: v for k, v in rec.items() if k != "provenance"},
                          indent=2, ensure_ascii=False)
        self.detail.setPlainText(head + body)

    def _export(self, fmt: str):
        if not self._model:
            QMessageBox.information(self, "Export", "Open a data view first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, f"Export {fmt.upper()}",
                                              f"{self._model.domain}.{fmt}", f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        from ..report import exporters
        rows = [self._model.record_at(i) for i in range(self._model.rowCount())]
        exporters.export(rows, path, fmt)
        QMessageBox.information(self, "Export", f"Wrote {len(rows)} records to:\n{path}")

    def _global_search(self):
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
        text = self.search_box.text().strip()
        if not text:
            return
        results = self.ds.global_search(text)
        if self._search_page is None:
            self._search_page = QTableWidget()
            self._search_page.setColumnCount(4)
            self._search_page.setHorizontalHeaderLabels(["Domain", "Summary", "Source", "Origin"])
            self._search_page.setSelectionBehavior(QTableWidget.SelectRows)
            self._search_page.horizontalHeader().setStretchLastSection(True)
            self._search_page.cellClicked.connect(self._search_row_clicked)
            self.stack.addWidget(self._search_page)
        t = self._search_page
        self._search_results = results
        t.setRowCount(len(results))
        for i, (dom, summary, rec) in enumerate(results):
            p = rec.get("provenance", {})
            for j, val in enumerate([DOMAIN_LABELS.get(dom, dom), summary,
                                     p.get("source_file", ""), p.get("origin", "")]):
                t.setItem(i, j, QTableWidgetItem(str(val)))
        t.resizeColumnsToContents()
        self.stack.setCurrentWidget(t)
        self.title.setText(f"Search: “{text}”  ({len(results)} hits)")
        self.statusBar().showMessage(f"{len(results)} matches for '{text}'")

    def _search_row_clicked(self, row, _col):
        dom, _summary, rec = self._search_results[row]
        p = rec.get("provenance", {})
        head = (f"DOMAIN: {dom}\nSOURCE: {p.get('source_file')} :: {p.get('source_table')}\n"
                f"ORIGIN: {p.get('origin')}\n" + "─" * 46 + "\n")
        self.detail.setPlainText(head + json.dumps(
            {k: v for k, v in rec.items() if k != "provenance"}, indent=2, ensure_ascii=False))

    def _geo(self, fmt: str):
        ext = "kml" if fmt == "kml" else "geojson"
        path, _ = QFileDialog.getSaveFileName(self, f"Export locations ({fmt})",
                                              f"locations.{ext}", f"{fmt} (*.{ext})")
        if not path:
            return
        from ..report import geo
        n = geo.export(os.path.join(self.case_dir, "case.db"), path, fmt)
        QMessageBox.information(self, "Locations", f"Exported {n} GPS fixes to:\n{path}")

    def _report(self, fmt: str):
        path, _ = QFileDialog.getSaveFileName(self, f"Save {fmt.upper()} report",
                                              f"report.{fmt}", f"{fmt.upper()} (*.{fmt})")
        if not path:
            return
        from ..report import html as htmlrep
        try:
            digest = htmlrep.generate(self.case_dir, path, fmt=fmt)
            QMessageBox.information(self, "Report", f"Report written:\n{path}\n\nSHA-256: {digest[:32]}…")
        except Exception as e:
            QMessageBox.critical(self, "Report error", str(e))


def _silence_benign_qt_warnings():
    """Filter out the harmless 'QFont::setPointSize <= 0' noise from Qt's stylesheet
    font resolution (pixel-size fonts report pointSize -1 internally)."""
    from PySide6.QtCore import qInstallMessageHandler
    def handler(mode, ctx, msg):
        if "setPointSize" in msg or "Point size <= 0" in msg:
            return
        sys.stderr.write(msg + "\n")
    qInstallMessageHandler(handler)


def launch(case_dir: str):
    if QApplication.instance() is None:
        # WebEngine needs shared GL contexts; set before the QApplication exists
        QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication.instance() or QApplication(sys.argv)
    _silence_benign_qt_warnings()
    app.setStyleSheet(qss())
    apply_palette(app)
    win = MainWindow(case_dir)
    win.show()
    return app.exec()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m paytmforensics.gui.app <case_dir>")
        sys.exit(1)
    sys.exit(launch(sys.argv[1]))
