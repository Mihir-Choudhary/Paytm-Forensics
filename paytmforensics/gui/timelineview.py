"""Visual timeline: a horizontal activity ribbon (events coloured by domain) above the
timeline table."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFrame, QLabel, QTableView, QSplitter
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont

from .datasource import DataSource
from .models import RecordTableModel
from .theme import C, DOMAIN_STYLE


def _events(ds: DataSource):
    out = []
    for r in ds.load("timeline"):
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
        self.events = events
        self.setMinimumHeight(150)
        self.setMouseTracking(True)
        self._hover = -1
        if events:
            self.t0 = events[0][0].timestamp()
            self.t1 = events[-1][0].timestamp()
            if self.t1 <= self.t0:
                self.t1 = self.t0 + 1

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
        # year/month ticks
        p.setFont(QFont("Segoe UI", 7)); p.setPen(QColor(C["text_dim"]))
        for f in range(6):
            x = m + f / 5 * (w - 2 * m)
            ts = self.t0 + f / 5 * (self.t1 - self.t0)
            p.drawText(QPointF(x - 22, axis_y + 16), datetime.fromtimestamp(ts).strftime("%b %Y"))
        # event dots (stagger vertically by hash to reduce overlap)
        for i, (dt, dom, _s) in enumerate(self.events):
            x = self._x(dt.timestamp(), m, w)
            col = QColor(DOMAIN_STYLE.get(dom, ("•", C["accent"]))[1])
            yy = 20 + (hash(dom) % 5) * 16
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
        events = _events(ds)
        ribbon_card = QFrame(); ribbon_card.setObjectName("card")
        rl = QVBoxLayout(ribbon_card); rl.setContentsMargins(12, 10, 12, 10)
        rl.addWidget(QLabel(f"Activity timeline — {len(events)} events"))
        self.ribbon = TimelineRibbon(events); rl.addWidget(self.ribbon)
        v.addWidget(ribbon_card)
        self.table = QTableView()
        self.table.setModel(RecordTableModel(ds, "timeline"))
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)
