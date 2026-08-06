"""Qt table model wrapping a domain's records (thin layer over DataSource)."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex

from .datasource import DataSource
from .filters import FilterSpec


class RecordTableModel(QAbstractTableModel):
    def __init__(self, ds: DataSource, domain: str):
        super().__init__()
        self.ds = ds
        self.domain = domain
        self._cols = ds.columns(domain)
        self._all = ds.load(domain)
        self._rows = list(self._all)

    # --- filtering --------------------------------------------------------- #
    def set_filter(self, spec: FilterSpec | None):
        self.beginResetModel()
        from .filters import apply_filter
        self._rows = apply_filter(self._all, spec) if spec else list(self._all)
        self.endResetModel()

    def record_at(self, row: int) -> dict:
        return self._rows[row]

    # --- Qt API ------------------------------------------------------------ #
    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self._cols)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.DisplayRole:
            rec = self._rows[index.row()]
            return self.ds.cell(rec, self._cols[index.column()])
        if role == Qt.ToolTipRole:
            rec = self._rows[index.row()]
            p = rec.get("provenance", {})
            return f"{p.get('source_file')} :: {p.get('source_table')} "\
                   f"[{p.get('origin')}] conf={p.get('confidence')}"
        return None

    def sort(self, column: int, order=Qt.AscendingOrder):
        """Sort by a display column. Grids were previously inert on header click."""
        if not (0 <= column < len(self._cols)):
            return
        col = self._cols[column]

        def key(rec):
            v = rec.get(col)
            if isinstance(v, dict):                 # timestamp-like -> sort by real instant
                v = v.get("utc_iso") or v.get("raw")
            if isinstance(v, bool):
                return (1, "", float(v))
            if isinstance(v, (int, float)):
                return (1, "", float(v))
            if v is None or v == "":
                return (0, "", 0.0)                 # empties group together, first
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v)
            return (1, str(v).lower(), 0.0)

        self.layoutAboutToBeChanged.emit()
        self._rows.sort(key=key, reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._cols[section]
        return section + 1
