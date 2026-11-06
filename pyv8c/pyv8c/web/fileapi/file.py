"""``File``: the ``Blob`` a navigator, a store or an ``<input>`` hands back.

Chromium keeps it beside ``blob.py`` in ``core/fileapi``, which is why it lives here
rather than in the deprecated FileSystem module that makes it reachable as a global.
"""
import time

from pyv8c import pyv8

from ...foundation.arguments import dictionary, dom_string, integer, is_nullish, missing_error
from ...idl import interface
from .blob import Blob


@interface("File", "Blob")
class File(Blob):
    """The ``File`` global and the ``FileEntry.file()`` view; ``size``/``type``
    come from :class:`Blob`.
    """

    __idl_overrides__ = {
        "size": {
            "side_effect": 1
        },
        "type": {
            "side_effect": 1
        }
    }

    def __init__(self, file_bits=pyv8.Undefined, file_name=pyv8.Undefined, options=None, *_extra, **kw):
        if file_bits is pyv8.Undefined:
            raise missing_error("File", "constructor", 2, 0, constructor=True)
        if file_name is pyv8.Undefined:
            raise missing_error("File", "constructor", 2, 1, constructor=True)
        init = {} if is_nullish(options) else dictionary(options, name="FilePropertyBag")
        super().__init__(file_bits, init, *_extra, **kw)
        self._name = dom_string(file_name)
        last_modified = init.get("lastModified")
        self._last_modified = (int(time.time() * 1000) if last_modified is None
                               else int(integer(last_modified, bits=64)))

    def get_name(self): return self._name
    def get_lastModified(self): return self._last_modified
