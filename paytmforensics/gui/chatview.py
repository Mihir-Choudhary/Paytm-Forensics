"""Conversation (chat) view: left = conversation list, right = message bubbles.

Messages are grouped by channelUrl. The subject's own messages (senderId == subject
Sendbird id) are right-aligned ("sent"); others are left-aligned ("received"). Payment
messages show the amount and RRN.
"""
from __future__ import annotations

from collections import defaultdict

from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem, QLabel,
    QScrollArea, QFrame, QSplitter
)
from PySide6.QtCore import Qt

from .datasource import DataSource
from .theme import C


def _ts(rec):
    t = rec.get("timestamp") or {}
    return t.get("utc_iso") or ""


class ChatView(QWidget):
    def __init__(self, ds: DataSource):
        super().__init__()
        self.ds = ds
        self.subject_sb = self._subject_sb()
        self.convos = self._group()
        self._build()

    def _subject_sb(self):
        for p in self.ds.load("person"):
            if p.get("is_subject"):
                return p.get("sendbird_id")
        return None

    def _group(self) -> dict:
        msgs = [m for m in self.ds.load("message") if m.get("channel_url")]
        by_chan = defaultdict(list)
        for m in msgs:
            by_chan[m["channel_url"]].append(m)
        convos = {}
        for chan, items in by_chan.items():
            items.sort(key=_ts)
            # prefer the resolved counterparty from channel membership
            name = next((m.get("chat_with") for m in items if m.get("chat_with")), None)
            if not name:
                # else first sender that is not the subject
                name = next((m.get("sender_name") for m in items
                             if m.get("sender_id") != self.subject_sb and m.get("sender_name")), None)
            if not name:
                name = "Outgoing (merchant)"
            convos[chan] = {"name": name, "messages": items, "count": len(items)}
        return convos

    def _build(self):
        lay = QHBoxLayout(self); lay.setContentsMargins(20, 16, 20, 16); lay.setSpacing(12)
        split = QSplitter(Qt.Horizontal)

        # left: conversation list
        left = QFrame(); left.setObjectName("card"); left.setMaximumWidth(300)
        lv = QVBoxLayout(left); lv.setContentsMargins(10, 10, 10, 10)
        title = QLabel("Conversations"); title.setObjectName("cardTitle"); lv.addWidget(title)
        self.clist = QListWidget(); self.clist.setObjectName("nav")
        order = sorted(self.convos.items(),
                       key=lambda kv: (kv[1]["messages"][-1].get("timestamp") or {}).get("utc_iso") or "",
                       reverse=True)
        for chan, c in order:
            it = QListWidgetItem(f"☺  {c['name']}   ({c['count']})")
            it.setData(Qt.UserRole, chan)
            self.clist.addItem(it)
        self.clist.currentItemChanged.connect(self._show_convo)
        lv.addWidget(self.clist)
        split.addWidget(left)

        # right: message thread
        right = QFrame(); right.setObjectName("card")
        rv = QVBoxLayout(right); rv.setContentsMargins(10, 10, 10, 10)
        self.header = QLabel("Select a conversation"); self.header.setObjectName("cardTitle")
        rv.addWidget(self.header)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True)
        self.thread_host = QWidget()
        self.thread = QVBoxLayout(self.thread_host)
        self.thread.setContentsMargins(6, 6, 6, 6); self.thread.setSpacing(8)
        self.thread.addStretch()
        self.scroll.setWidget(self.thread_host)
        rv.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setSizes([280, 900])
        lay.addWidget(split)

        if self.clist.count():
            self.clist.setCurrentRow(0)

    def _clear_thread(self):
        while self.thread.count() > 1:    # keep the trailing stretch
            item = self.thread.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _show_convo(self, cur, _prev):
        if not cur:
            return
        chan = cur.data(Qt.UserRole)
        c = self.convos.get(chan)
        if not c:
            return
        self.header.setText(f"☺  {c['name']}   ·   {c['count']} messages")
        self._clear_thread()
        last_date = None
        for m in c["messages"]:
            day = (_ts(m) or "")[:10]
            if day and day != last_date:
                self.thread.insertWidget(self.thread.count() - 1, self._date_sep(day))
                last_date = day
            outgoing = (m.get("sender_id") == self.subject_sb)
            self.thread.insertWidget(self.thread.count() - 1, self._bubble(m, outgoing))

    def _date_sep(self, day: str) -> QWidget:
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(0, 6, 0, 6)
        lab = QLabel(day)
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet(
            f"background:{C['chip']}; color:{C['text_muted']}; border:1px solid {C['border']};"
            f"border-radius:10px; padding:2px 12px; font-size:11px;")
        h.addStretch(); h.addWidget(lab); h.addStretch()
        return row

    @staticmethod
    def _status(m: dict):
        """Return (glyph, color, label) describing the payment/message status."""
        mt = (m.get("msg_type") or "").upper()
        content = (m.get("content") or "").lower()
        if "FAIL" in mt or "fail" in content:
            return ("✗", C["red"], "failed")
        if "declined" in content or "DECLINE" in mt:
            return ("✗", C["red"], "declined")
        if "REQUEST" in mt and "RESPONSE" not in mt:
            return ("⏳", C["amber"], "requested")
        if "TRANSFER" in mt or "approved" in content or "RESPONSE" in mt:
            return ("✓", C["green"], "success")
        return ("", C["text_dim"], "")

    def _bubble(self, m: dict, outgoing: bool) -> QWidget:
        row = QWidget(); h = QHBoxLayout(row); h.setContentsMargins(2, 0, 2, 0)
        bubble = QFrame()
        bg = C["accent"] if outgoing else C["bg_card_hover"]
        fg = "white" if outgoing else C["text"]
        bubble.setStyleSheet(
            f"background:{bg}; border-radius:12px; border:1px solid "
            f"{C['accent'] if outgoing else C['border']};")
        bubble.setMaximumWidth(460)
        bl = QVBoxLayout(bubble); bl.setContentsMargins(12, 8, 12, 8); bl.setSpacing(2)

        content = m.get("content") or f"({m.get('msg_type') or 'message'})"
        amt = m.get("amount")
        if amt:
            mt = (m.get("msg_type") or "").upper()
            verb = "Paid" if outgoing and "REQUEST" not in mt else \
                   ("Requested" if "REQUEST" in mt else "Received")
            head = QLabel(f"₹{amt:g}  ·  {verb}")
            head.setStyleSheet(f"color:{fg}; font-weight:700; font-size:14px;")
            bl.addWidget(head)
        body = QLabel(content); body.setWordWrap(True)
        body.setStyleSheet(f"color:{fg}; font-size:13px;")
        bl.addWidget(body)
        # meta line: status icon + RRN + time
        glyph, scol, slabel = self._status(m)
        meta_bits = []
        if glyph:
            meta_bits.append(f"{glyph} {slabel}")
        if m.get("rrn"):
            meta_bits.append(f"RRN {m['rrn']}")
        ts = _ts(m)
        if ts:
            meta_bits.append(ts[11:19])
        meta = QLabel("   ".join(meta_bits))
        # status colour stands out even on the blue outgoing bubble
        mcol = scol if not outgoing else "#eaf2ff"
        meta.setStyleSheet(f"color:{mcol}; font-size:10px;")
        bl.addWidget(meta)

        if outgoing:
            h.addStretch(); h.addWidget(bubble)
        else:
            h.addWidget(bubble); h.addStretch()
        return row
