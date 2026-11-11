"""HTML table elements."""
from ...idl import interface
from ..dom.collections import HTMLCollection
from .elements import HTMLElement


def _owning_table(row):
    """The nearest ``table`` ancestor of *row*, which is the table that counts it."""
    parent = getattr(row, "parentElement", None)
    while parent is not None:
        if isinstance(parent, HTMLTableElement):
            return parent
        parent = getattr(parent, "parentElement", None)
    return None


@interface('HTMLTableElement', 'HTMLElement')
class HTMLTableElement(HTMLElement):
    """``<table>``: the rows it owns -- those in its sections, in document order --
    but not the rows of a table nested inside it, which belong to that table."""

    def get_rows(self):
        view = getattr(self, "_rows_view", None)
        if view is None:
            view = self._rows_view = HTMLCollection(owner=self, getter=self._rows)
        return view

    def _rows(self):
        return [node for node in self._descendants()
                if isinstance(node, HTMLTableRowElement) and _owning_table(node) is self]


@interface('HTMLTableRowElement', 'HTMLElement')
class HTMLTableRowElement(HTMLElement):
    """``<tr>``: the cells it holds and where the row sits in its table."""

    def get_cells(self):
        view = getattr(self, "_cells_view", None)
        if view is None:
            view = self._cells_view = HTMLCollection(owner=self, getter=self._cells)
        return view

    def _cells(self):
        """A row's own cells: its ``td`` and ``th`` children, in document order."""
        return [child for child in self._children if isinstance(child, HTMLTableCellElement)]

    def get_rowIndex(self):
        """The row's position in its table's ``rows``, or -1 when no table owns it."""
        table = _owning_table(self)
        if table is None:
            return -1
        return next((index for index, row in enumerate(table._rows()) if row is self), -1)


@interface('HTMLTableCellElement', 'HTMLElement')
class HTMLTableCellElement(HTMLElement):
    """``<td>`` and ``<th>``: where the cell sits in its row."""

    def get_cellIndex(self):
        """The cell's position in its row's ``cells``, or -1 when no row holds it."""
        row = getattr(self, "parentElement", None)
        if not isinstance(row, HTMLTableRowElement):
            return -1
        return next((index for index, cell in enumerate(row._cells()) if cell is self), -1)


