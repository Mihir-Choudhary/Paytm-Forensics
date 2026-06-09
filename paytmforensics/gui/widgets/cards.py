"""Reusable card widgets: stat tiles, info cards, key/value rows."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QWidget, QSizePolicy
)
from PySide6.QtCore import Qt, Signal

from ..theme import C


class StatCard(QFrame):
    """A clickable stat tile: big number + label + colored glyph."""
    clicked = Signal(str)

    def __init__(self, key: str, label: str, value, glyph: str, color: str):
        super().__init__()
        self.setObjectName("statCard")
        self.key = key
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        lay = QVBoxLayout(self); lay.setContentsMargins(16, 14, 16, 14); lay.setSpacing(6)
        top = QHBoxLayout()
        icon = QLabel(glyph); icon.setObjectName("statIcon")
        icon.setStyleSheet(f"color:{color}; font-size:20px;")
        top.addWidget(icon); top.addStretch()
        lay.addLayout(top)
        self.value = QLabel(str(value)); self.value.setObjectName("statValue")
        self.value.setStyleSheet(f"color:{color};")
        lay.addWidget(self.value)
        lab = QLabel(label); lab.setObjectName("statLabel")
        lay.addWidget(lab)

    def mousePressEvent(self, e):
        self.clicked.emit(self.key)


class InfoCard(QFrame):
    """A titled card holding arbitrary content."""
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(18, 16, 18, 16); self._lay.setSpacing(8)
        t = QLabel(title); t.setObjectName("cardTitle")
        self._lay.addWidget(t)

    def add(self, w):
        self._lay.addWidget(w)

    def add_layout(self, l):
        self._lay.addLayout(l)


def kv_row(key: str, value: str) -> QWidget:
    w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 2, 0, 2)
    k = QLabel(key); k.setObjectName("kvKey"); k.setMinimumWidth(130)
    v = QLabel(str(value if value not in (None, "") else "—")); v.setObjectName("kvVal")
    v.setWordWrap(True); v.setTextInteractionFlags(Qt.TextSelectableByMouse)
    h.addWidget(k); h.addWidget(v, 1)
    return w


class BarRow(QWidget):
    """A simple labelled proportion bar (no chart deps)."""
    def __init__(self, label: str, value: float, total: float, color: str):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 2, 0, 2); h.setSpacing(8)
        lab = QLabel(label); lab.setObjectName("kvKey"); lab.setMinimumWidth(90)
        bar = QFrame(); bar.setFixedHeight(14)
        # zero stays zero — a 2px sliver would imply a nonzero amount
        pct = 0 if total <= 0 or value <= 0 else max(2, int(220 * value / total))
        bar.setFixedWidth(pct)
        bar.setStyleSheet(f"background:{color}; border-radius:7px;")
        val = QLabel(f"{value:g}"); val.setObjectName("kvVal")
        h.addWidget(lab); h.addWidget(bar); h.addWidget(val); h.addStretch()
