"""Qt table model wrapping a domain's records (thin layer over DataSource)."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex

from .datasource import DataSource
from .filters import FilterSpec
from .sorting import sort_records


class RecordTableModel(QAbstractTableModel):
    def __init__(self, ds: DataSource, domain: str):
        super().__init__()
        self.ds = ds
        self.domain = domain
        self._cols = ds.columns(domain)
        self._all = ds.load(domain)
        self._rows = list(self._all)
        self._sort_state: tuple[int, Qt.SortOrder] | None = None

    # --- filtering --------------------------------------------------------- #
    def set_filter(self, spec: FilterSpec | None):
        self.beginResetModel()
        from .filters import apply_filter
        self._rows = apply_filter(self._all, spec) if spec else list(self._all)
        if self._sort_state:
            self._apply_sort(*self._sort_state)
        self.endResetModel()

    def record_at(self, row: int) -> dict:
        return self._rows[row]

    # --- sorting (triggered by header clicks via QTableView) ---------------- #
    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder):
        if not 0 <= column < len(self._cols):
            return
        self.beginResetModel()
        self._sort_state = (column, order)
        self._apply_sort(column, order)
        self.endResetModel()

    def _apply_sort(self, column: int, order: Qt.SortOrder):
        self._rows = sort_records(self._rows, self._cols[column],
                                  descending=(order == Qt.DescendingOrder))

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

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._cols[section]
        return section + 1
