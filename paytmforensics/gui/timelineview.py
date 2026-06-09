"""Visual timeline: a horizontal activity ribbon (events coloured by domain) above the
timeline table, with date-range / text filtering that drives both."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QTableView, QLineEdit,
    QPushButton,
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from .datasource import DataSource
from .filters import FilterSpec, parse_user_date
from .models import RecordTableModel
from .theme import C, DOMAIN_STYLE

# stable lane per domain — str hash() is randomized per process and would
# re-shuffle the ribbon between runs of the same case
_LANE = {dom: i % 5 for i, dom in enumerate(DOMAIN_STYLE)}


def _to_events(records) -> list[tuple]:
    """[(datetime, ref_domain, summary)] sorted chronologically."""
    out = []
    for r in records:
        utc = r.get("utc_iso")
        if not utc:
            continue
        try:
            dt = datetime.fromisoformat(utc)
        except ValueError:
            continue
        out.append((dt, r.get("ref_domain") or "timeline", r.get("summary") or ""))
    out.sort(key=lambda e: e[0])
    return out


class TimelineRibbon(QWidget):
    def __init__(self, events):
        super().__init__()
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self.set_events(events)

    def set_events(self, events):
        self.events = events
        self._hover = -1
        if events:
            self.t0 = events[0][0].timestamp()
            self.t1 = events[-1][0].timestamp()
            if self.t1 <= self.t0:
                self.t1 = self.t0 + 1
        self.update()

    def _x(self, ts, m, w):
        return m + (ts - self.t0) / (self.t1 - self.t0) * (w - 2 * m)

    def paintEvent(self, _e):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height(); m = 40
        p.fillRect(self.rect(), QColor(C["bg_input"]))
        if not self.events:
            p.setPen(QColor(C["text_muted"])); p.drawText(self.rect(), Qt.AlignCenter, "No timeline events"); return
        axis_y = h - 34
        p.setPen(QPen(QColor(C["border"]), 1)); p.drawLine(m, axis_y, w - m, axis_y)
        # year/month ticks (UTC — event times are UTC, so labels must be too)
        p.setFont(QFont("Segoe UI", 7)); p.setPen(QColor(C["text_dim"]))
        for f in range(6):
            x = m + f / 5 * (w - 2 * m)
            ts = self.t0 + f / 5 * (self.t1 - self.t0)
            p.drawText(QPointF(x - 22, axis_y + 16),
                       datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %Y"))
        # event dots (stagger vertically by domain lane to reduce overlap)
        for i, (dt, dom, _s) in enumerate(self.events):
            x = self._x(dt.timestamp(), m, w)
            col = QColor(DOMAIN_STYLE.get(dom, ("•", C["accent"]))[1])
            yy = 20 + _LANE.get(dom, 0) * 16
            r = 7 if i == self._hover else 4
            p.setPen(QPen(QColor("white") if i == self._hover else col.darker(150), 1))
            p.setBrush(col); p.drawEllipse(QPointF(x, yy), r, r)
            p.setPen(QPen(QColor(col), 1)); p.drawLine(QPointF(x, yy + r), QPointF(x, axis_y))
        if 0 <= self._hover < len(self.events):
            dt, dom, s = self.events[self._hover]
            txt = f"{dt.strftime('%d %b %Y %H:%M')}  [{dom}]  {s}"
            p.setFont(QFont("Segoe UI", 8)); p.setPen(QColor(C["text"]))
            p.drawText(QRectF(m, 2, w - 2 * m, 16), Qt.AlignLeft, txt[:120])

    def mouseMoveEvent(self, e):
        w = self.width(); m = 40
        best, bd = -1, 1e9
        for i, (dt, _d, _s) in enumerate(self.events):
            x = self._x(dt.timestamp(), m, w)
            d = abs(x - e.position().x())
            if d < bd:
                bd, best = d, i
        self._hover = best if bd < 12 else -1
        self.update()


class TimelineView(QWidget):
    def __init__(self, ds: DataSource):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(12)

        self.model = RecordTableModel(ds, "timeline")

        ribbon_card = QFrame(); ribbon_card.setObjectName("card")
        rl = QVBoxLayout(ribbon_card); rl.setContentsMargins(12, 10, 12, 10)
        self.head = QLabel(); rl.addWidget(self.head)
        self.ribbon = TimelineRibbon(_to_events(self._records())); rl.addWidget(self.ribbon)
        v.addWidget(ribbon_card)

        # filter row: the master timeline is the view analysts date-bound most
        fr = QHBoxLayout(); fr.setSpacing(8)
        self.f_from = QLineEdit(); self.f_from.setPlaceholderText("From  YYYY-MM-DD")
        self.f_to = QLineEdit(); self.f_to.setPlaceholderText("To  YYYY-MM-DD")
        self.f_text = QLineEdit(); self.f_text.setPlaceholderText("🔍  contains…")
        for wdg in (self.f_from, self.f_to):
            wdg.setMaximumWidth(160); wdg.returnPressed.connect(self._apply)
        self.f_text.returnPressed.connect(self._apply)
        apply_btn = QPushButton("Apply"); apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._apply)
        clear_btn = QPushButton("Clear"); clear_btn.clicked.connect(self._clear)
        self.msg = QLabel(""); self.msg.setObjectName("kvKey")
        self.msg.setStyleSheet(f"color:{C['amber']};")
        fr.addWidget(self.f_from); fr.addWidget(self.f_to); fr.addWidget(self.f_text, 1)
        fr.addWidget(apply_btn); fr.addWidget(clear_btn); fr.addWidget(self.msg)
        v.addLayout(fr)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setResizeContentsPrecision(200)
        v.addWidget(self.table, 1)
        self._refresh_head()

    def _records(self) -> list[dict]:
        return [self.model.record_at(i) for i in range(self.model.rowCount())]

    def _refresh_head(self):
        self.head.setText(f"Activity timeline — {self.model.rowCount()} events"
                          f"  ·  times in UTC")

    def _apply(self):
        ok_f, date_from = parse_user_date(self.f_from.text())
        ok_t, date_to = parse_user_date(self.f_to.text())
        if not (ok_f and ok_t):
            self.msg.setText("dates must be YYYY-MM-DD — filter NOT applied")
            return
        self.msg.setText("")
        spec = FilterSpec(text=self.f_text.text().strip(),
                          date_from=date_from, date_to=date_to)
        self.model.set_filter(spec)
        self.ribbon.set_events(_to_events(self._records()))
        self._refresh_head()

    def _clear(self):
        for wdg in (self.f_from, self.f_to, self.f_text):
            wdg.clear()
        self.msg.setText("")
        self.model.set_filter(None)
        self.ribbon.set_events(_to_events(self._records()))
        self._refresh_head()
