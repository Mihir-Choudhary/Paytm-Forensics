"""Entity drill-down pivot: everything the case knows about one counterparty.

Opened by double-clicking a row in the Entities table. Shows the resolved
identifiers plus the entity's transactions and chat messages side by side —
the cross-domain re-filtering an analyst otherwise does by hand.
"""
from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QSplitter,
    QTableView, QTextEdit,
)
from PySide6.QtCore import Qt, Signal

from .datasource import DataSource
from .entitylogic import related_records, entity_header_rows
from .models import RecordTableModel
from .theme import C
from .widgets.cards import kv_row


class EntityView(QWidget):
    back = Signal()

    def __init__(self, ds: DataSource, entity: dict):
        super().__init__()
        self.ds = ds
        self.entity = entity
        txns, msgs = related_records(entity, ds.load("transaction"), ds.load("message"))
        self.txns, self.msgs = txns, msgs

        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 16); v.setSpacing(12)

        # --- header card: identifiers + totals + back button ---
        head = QFrame(); head.setObjectName("card")
        hv = QVBoxLayout(head); hv.setContentsMargins(18, 14, 18, 14); hv.setSpacing(4)
        top = QHBoxLayout()
        name = (entity.get("names") or ["(unnamed counterparty)"])[0]
        title = QLabel(f"☺  {name}"); title.setObjectName("pageTitle")
        back_btn = QPushButton("←  All entities")
        back_btn.clicked.connect(self.back.emit)
        top.addWidget(title); top.addStretch(); top.addWidget(back_btn)
        hv.addLayout(top)
        if entity.get("is_subject"):
            warn = QLabel("This is the device owner (subject).")
            warn.setStyleSheet(f"color:{C['amber']};")
            hv.addWidget(warn)
        for k, val in entity_header_rows(entity):
            hv.addWidget(kv_row(k, val))
        v.addWidget(head)

        # --- two tables: transactions | messages ---
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self._table_card(
            f"Transactions ({len(txns)})", "transaction", txns))
        split.addWidget(self._table_card(
            f"Chat messages ({len(msgs)})", "message", msgs))
        split.setSizes([640, 560])
        v.addWidget(split, 2)

        # --- shared detail pane ---
        self.detail = QTextEdit(); self.detail.setObjectName("detail")
        self.detail.setReadOnly(True); self.detail.setMaximumHeight(170)
        self.detail.setPlaceholderText(
            "Select a transaction or message to view full fields and provenance.")
        v.addWidget(self.detail, 1)

    def _table_card(self, title: str, domain: str, records: list[dict]) -> QWidget:
        card = QFrame(); card.setObjectName("card")
        cv = QVBoxLayout(card); cv.setContentsMargins(12, 10, 12, 10); cv.setSpacing(6)
        lab = QLabel(title); lab.setObjectName("cardTitle"); cv.addWidget(lab)
        table = QTableView()
        model = RecordTableModel(self.ds, domain, records=records)
        table.setModel(model)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.setSelectionBehavior(QTableView.SelectRows)
        table.setSelectionMode(QTableView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setResizeContentsPrecision(200)
        table.resizeColumnsToContents()
        table.selectionModel().currentRowChanged.connect(
            lambda cur, _prev, m=model: self._show_detail(m, cur))
        cv.addWidget(table)
        return card

    def _show_detail(self, model: RecordTableModel, cur):
        if not cur.isValid():
            return
        rec = model.record_at(cur.row())
        p = rec.get("provenance", {})
        head = (f"SOURCE: {p.get('source_file')} :: {p.get('source_table')}   "
                f"ROWID: {p.get('rowid')}   ORIGIN: {p.get('origin')}\n" + "─" * 60 + "\n")
        self.detail.setPlainText(head + json.dumps(
            {k: v for k, v in rec.items() if k not in ("provenance", "raw")},
            indent=2, ensure_ascii=False))
