"""``FileList``: the list ``<input type=file>`` exposes as ``files``.

Chromium keeps it beside ``blob.py`` and ``file.py`` in ``core/fileapi``; only the empty
list is modelled, since no file picker exists here, but the shape is Chrome's.
"""
from pyv8c import pyv8

from ...foundation.arguments import MISSING, missing_error
from ...idl import interface
from ..dom.mixins import ReadOnlyIndexedPropertyMixin


@interface('FileList', None)
class FileList(ReadOnlyIndexedPropertyMixin):
    """The empty file list of an ``<input type=file>`` with no picked files: no file
    picker is modelled, but ``files`` keeps Chrome's shape."""

    def __init__(self, values=None, **_):
        self._values = tuple(values or ())
    def _indexed_items(self): return self._values
    def get_length(self): return len(self._values)
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('FileList', 'item', 1, 0, constructor=False)
        items = self._values
        index = int(index)
        return items[index] if 0 <= index < len(items) else pyv8.Null
