"""Qt table model wrapping a domain's records (thin layer over DataSource)."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, Qt, QModelIndex

from .datasource import DataSource
from .filters import FilterSpec
from .sorting import sort_records


class RecordTableModel(QAbstractTableModel):
    def __init__(self, ds: DataSource, domain: str, records: list[dict] | None = None,
                 annotations=None):
        """`records` overrides the row set (e.g. an entity pivot's pre-matched
        subset); `annotations` (an AnnotationStore) decorates flagged rows."""
        super().__init__()
        self.ds = ds
        self.domain = domain
        self.annotations = annotations
        self._cols = ds.columns(domain)
        self._all = list(records) if records is not None else ds.load(domain)
        self._rows = list(self._all)
        self._sort_state: tuple[int, Qt.SortOrder] | None = None

    # --- filtering --------------------------------------------------------- #
    def set_filter(self, spec: FilterSpec | None, predicate=None):
        """Apply spec, then an optional record predicate (e.g. flagged-only)."""
        self.beginResetModel()
        from .filters import apply_filter
        rows = apply_filter(self._all, spec) if spec else list(self._all)
        if predicate is not None:
            rows = [r for r in rows if predicate(r)]
        self._rows = rows
        if self._sort_state:
            self._apply_sort(*self._sort_state)
        self.endResetModel()

    def record_at(self, row: int) -> dict:
        return self._rows[row]

    def refresh_row(self, row: int):
        """Repaint one row (e.g. after its flag/note changed)."""
        if 0 <= row < len(self._rows):
            self.dataChanged.emit(self.index(row, 0),
                                  self.index(row, len(self._cols) - 1))

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
            text = self.ds.cell(rec, self._cols[index.column()])
            if (index.column() == 0 and self.annotations is not None
                    and self.annotations.is_flagged(rec)):
                return f"★ {text}"
            return text
        if role == Qt.TextAlignmentRole:
            v = self._rows[index.row()].get(self._cols[index.column()])
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(Qt.AlignRight | Qt.AlignVCenter)   # amounts scan vertically
            return None
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
