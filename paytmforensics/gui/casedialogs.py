"""Case-level dialogs: audit-log viewer (with chain verification) and the
evidence-verification result. Chain-of-custody state was previously CLI-only
(--verify); these make it visible to GUI analysts."""
from __future__ import annotations

import json
import os

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QPushButton,
    QHBoxLayout, QMessageBox,
)
from PySide6.QtCore import Qt

from ..core.audit import read_entries, verify_chain
from .theme import C


class AuditLogDialog(QDialog):
    def __init__(self, case_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audit log — hash-chained tool actions")
        self.resize(960, 560)
        v = QVBoxLayout(self)

        path = os.path.join(case_dir, "audit.log")
        if not os.path.exists(path):
            v.addWidget(QLabel("No audit.log found in this case directory."))
            return
        chain = verify_chain(path)
        if chain["ok"]:
            txt = (f"✓  Hash chain VERIFIED — {chain['entries']} entries, "
                   f"append-only and tamper-evident")
            color = C["green"]
        else:
            txt = (f"✗  Hash chain BROKEN at entry {chain['first_bad']} "
                   f"of {chain['entries']} — the log has been altered")
            color = C["red"]
        banner = QLabel(txt)
        banner.setStyleSheet(f"color:{color}; font-weight:700; font-size:14px;")
        v.addWidget(banner)

        t = QTableWidget()
        t.setColumnCount(3)
        t.setHorizontalHeaderLabels(["Time (UTC)", "Action", "Detail"])
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        entries = read_entries(path)
        t.setRowCount(len(entries))
        for i, e in enumerate(entries):
            detail = json.dumps(e.get("detail") or {}, ensure_ascii=False)
            for j, val in enumerate([
                    (e.get("ts") or "")[:19].replace("T", " "),
                    e.get("action") or "",
                    detail if len(detail) <= 160 else detail[:160] + "…"]):
                t.setItem(i, j, QTableWidgetItem(val))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        v.addWidget(t, 1)

        h = QHBoxLayout(); h.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.accept)
        h.addWidget(close)
        v.addLayout(h)


def show_verify_result(parent, report: dict, root: str) -> None:
    """Present an integrity.verify_against() report."""
    if report.get("ok"):
        QMessageBox.information(
            parent, "Evidence verified",
            f"Source is byte-identical to the ingest manifest.\n\n"
            f"Extraction: {root}\nNo files changed, missing, or added.")
        return
    def fmt(label, items):
        if not items:
            return ""
        shown = "\n  ".join(items[:12])
        more = f"\n  … and {len(items) - 12} more" if len(items) > 12 else ""
        return f"\n{label} ({len(items)}):\n  {shown}{more}"
    QMessageBox.critical(
        parent, "Evidence verification FAILED",
        "Source does NOT match the ingest manifest."
        + fmt("Changed", report.get("changed") or [])
        + fmt("Missing", report.get("missing") or [])
        + fmt("Added", report.get("added") or []))
