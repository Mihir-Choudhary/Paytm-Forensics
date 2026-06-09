"""Overview dashboard: subject card, stat tiles, financial summary, recent timeline."""
from __future__ import annotations

import json
import os

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal

from .datasource import DataSource, DOMAIN_LABELS
from .theme import C, DOMAIN_STYLE
from .widgets.cards import StatCard, InfoCard, kv_row, BarRow

# stat tiles to show, in order
TILE_DOMAINS = ["transaction", "entity", "message", "location",
                "carved", "consent", "job", "notification"]


class Dashboard(QScrollArea):
    open_domain = Signal(str)

    def __init__(self, case_dir: str, ds: DataSource):
        super().__init__()
        self.setWidgetResizable(True)
        self.ds = ds
        self.case_dir = case_dir
        self.meta = self._load(os.path.join(case_dir, "case_meta.json"))
        root = QWidget(); root.setObjectName("root")
        self.col = QVBoxLayout(root)
        self.col.setContentsMargins(24, 20, 24, 24); self.col.setSpacing(18)
        self._build()
        self.setWidget(root)

    @staticmethod
    def _load(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}

    def _build(self):
        counts = self.ds.domains()

        # ---- stat tiles row ----
        grid = QGridLayout(); grid.setSpacing(14)
        col = 0
        for dom in TILE_DOMAINS:
            if dom not in counts:
                continue
            glyph, color = DOMAIN_STYLE.get(dom, ("•", C["accent"]))
            card = StatCard(dom, DOMAIN_LABELS.get(dom, dom), counts[dom], glyph, color)
            card.clicked.connect(self.open_domain.emit)
            grid.addWidget(card, 0, col)
            col += 1
        self.col.addLayout(grid)

        # ---- two-column: subject + financial summary ----
        row = QHBoxLayout(); row.setSpacing(16)
        row.addWidget(self._subject_card(), 1)
        row.addWidget(self._financial_card(), 1)
        self.col.addLayout(row)

        # ---- recent timeline ----
        self.col.addWidget(self._timeline_card())
        self.col.addStretch()

    def _subject_card(self) -> QWidget:
        card = InfoCard("Subject / Device owner")
        subj = next((p for p in self.ds.load("person") if p.get("is_subject")), None)
        if not subj:
            card.add(QLabel("No subject identified"))
            return card
        rows = [
            ("Name", subj.get("name")),
            ("Customer ID", subj.get("customer_id")),
            ("Phone", subj.get("phone")),
            ("Bank", subj.get("bank_name")),
            ("Sendbird ID", subj.get("sendbird_id")),
        ]
        # add KYC / device from prefs
        for p in self.ds.load("pref"):
            if p.get("key") == "kyc_state":
                rows.append(("KYC state", p.get("value")))
            if p.get("key") == "ppb_bank_type":
                rows.append(("Bank type", p.get("value")))
        for k, v in rows:
            card.add(kv_row(k, v))
        # case meta chips
        meta = QLabel(f"Case: {self.meta.get('case_id','?')}   •   "
                      f"Examiner: {self.meta.get('examiner','?')}   •   "
                      f"Evidence: {self.meta.get('evidence_number','?')}")
        meta.setObjectName("kvKey"); meta.setStyleSheet(f"color:{C['text_dim']}; padding-top:6px;")
        card.add(meta)
        return card

    def _financial_card(self) -> QWidget:
        card = InfoCard("Financial summary")
        txns = self.ds.load("transaction")
        # only settled (completed) movements count toward money totals
        credit = sum(t.get("amount") or 0 for t in txns
                     if t.get("direction") == "credit" and t.get("settled"))
        debit = sum(t.get("amount") or 0 for t in txns
                    if t.get("direction") == "debit" and t.get("settled"))
        total = max(credit, debit, 1)
        n_pb = sum(1 for t in txns if t.get("txn_source") == "passbook")
        n_chat = sum(1 for t in txns if t.get("txn_source") == "chat")
        card.add(kv_row("Transactions", f"{len(txns)}  ({n_pb} passbook + {n_chat} chat)"))
        card.add(BarRow("Received", round(credit, 2), total, C["green"]))
        card.add(BarRow("Paid", round(debit, 2), total, C["red"]))
        # top counterparties by txn_count
        ents = sorted(self.ds.load("entity"), key=lambda e: e.get("txn_count", 0), reverse=True)
        top = [e for e in ents if e.get("txn_count", 0) > 0 and not e.get("is_subject")][:5]
        if top:
            lab = QLabel("Top counterparties"); lab.setObjectName("kvKey")
            lab.setStyleSheet(f"color:{C['text_dim']}; padding-top:8px;")
            card.add(lab)
            for e in top:
                name = (e.get("names") or ["?"])[0]
                card.add(kv_row(name[:28], f"{e.get('txn_count')} txns  "
                                          f"↓{e.get('total_received',0):g} ↑{e.get('total_paid',0):g}"))
        return card

    def _timeline_card(self) -> QWidget:
        card = InfoCard("Recent user activity (latest 12)  ·  click a row to open its domain")
        # focus on meaningful user events; exclude device telemetry & push bookkeeping
        skip = {"diagnostic", "notification"}
        tl = [t for t in self.ds.load("timeline")
              if t.get("utc_iso") and t.get("ref_domain") not in skip]
        tl.sort(key=lambda t: t["utc_iso"], reverse=True)
        for ev in tl[:12]:
            w = _ClickRow(ev.get("ref_domain"), self.open_domain)
            h = QHBoxLayout(w); h.setContentsMargins(0, 2, 0, 2)
            glyph, color = DOMAIN_STYLE.get(ev.get("ref_domain"), ("•", C["text_muted"]))
            ic = QLabel(glyph); ic.setStyleSheet(f"color:{color};"); ic.setFixedWidth(18)
            ts = QLabel(ev["utc_iso"][:19].replace("T", " ")); ts.setObjectName("kvKey")
            ts.setFixedWidth(150)
            sm = QLabel(ev.get("summary") or ev.get("event_type") or "")
            sm.setObjectName("kvVal"); sm.setWordWrap(False)
            h.addWidget(ic); h.addWidget(ts); h.addWidget(sm, 1)
            card.add(w)
        return card


class _ClickRow(QWidget):
    """A dashboard row that opens its domain view when clicked."""
    def __init__(self, domain: str | None, open_domain_signal):
        super().__init__()
        self._dom = domain
        self._sig = open_domain_signal
        if domain:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(f"Open {domain}")

    def mousePressEvent(self, _e):
        if self._dom:
            self._sig.emit(self._dom)
