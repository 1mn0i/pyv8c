"""DataTransfer's in-memory text and file store.

A script-constructed DataTransfer has no active drag operation. Its data store is
read/write, while dropEffect and effectAllowed stay at their initial ``none``.
"""

import json
import pyv8

from ...engine.features import FeatureImplementation
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from ...idl import interface
from ..page_errors import NotSupportedError
from .mixins import ReadOnlyIndexedPropertyMixin


def _format(value):
    name = dom_string(value).strip().lower()
    return {'text': 'text/plain', 'url': 'text/uri-list'}.get(name, name)


@interface('DataTransferItem', None)
class DataTransferItem:
    def __init__(self, record=None, **_):
        self._record = record or {'kind': 'string', 'type': '', 'value': ''}

    def get_kind(self): return self._record['kind']
    def get_type(self): return self._record['type']
    def fn_getAsFile(self, *_extra):
        return self._record['value'] if self._record['kind'] == 'file' else pyv8.Null


@interface('DataTransferItemList', None)
class DataTransferItemList(ReadOnlyIndexedPropertyMixin):
    def __init__(self, owner=None, **_):
        self._owner = owner

    def _records(self):
        return self._owner._records if self._owner is not None else []

    def _indexed_items(self):
        # Chromium makes a fresh item wrapper on indexed reads. The underlying
        # record stays live even when setData replaces its value.
        return [DataTransferItem(record) for record in self._records()]

    def get_length(self): return len(self._records())

    def fn_add(self, data=MISSING, type=MISSING, *_extra):
        if data is MISSING:
            raise missing_error('DataTransferItemList', 'add', 1, 0, constructor=False)
        from ..fileapi.file import File
        if isinstance(data, File):
            record = {'kind': 'file', 'type': data.get_type(), 'value': data}
        else:
            if type is MISSING:
                raise missing_error('DataTransferItemList', 'add', 2, 1, constructor=False)
            name = _format(type)
            if any(item['kind'] == 'string' and item['type'] == name
                   for item in self._records()):
                raise NotSupportedError('An item already exists for this type.')
            record = {'kind': 'string', 'type': name, 'value': dom_string(data)}
        self._records().append(record)
        self._owner._changed()
        return DataTransferItem(record)

    def fn_clear(self, *_extra):
        self._records().clear()
        self._owner._changed()

    def fn_remove(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('DataTransferItemList', 'remove', 1, 0, constructor=False)
        position = integer(index)
        if 0 <= position < len(self._records()):
            del self._records()[position]
            self._owner._changed()


@interface('DataTransfer', None)
class DataTransfer:
    def __init__(self, *_extra, **_):
        self._records = []
        self._files = None
        self._types = None

    def _changed(self):
        self._types = None
        if self._files is not None:
            self._files._values = tuple(record['value'] for record in self._records
                                        if record['kind'] == 'file')

    def get_dropEffect(self): return 'none'
    def set_dropEffect(self, _value): pass
    def get_effectAllowed(self): return 'none'
    def set_effectAllowed(self, _value): pass
    def get_items(self): return DataTransferItemList(self)
    def get_types(self):
        if self._types is None:
            types = [record['type'] for record in self._records if record['kind'] == 'string']
            if any(record['kind'] == 'file' for record in self._records):
                types.append('Files')
            self._types = pyv8.current_context().exec_js(
                'Object.freeze(' + json.dumps(types) + ')')
        return self._types
    def get_files(self):
        from ..fileapi.file_list import FileList
        if self._files is None:
            self._files = FileList([record['value'] for record in self._records
                                    if record['kind'] == 'file'])
        return self._files

    def fn_getData(self, format=MISSING, *_extra):
        if format is MISSING:
            raise missing_error('DataTransfer', 'getData', 1, 0, constructor=False)
        name = _format(format)
        return next((record['value'] for record in self._records
                     if record['kind'] == 'string' and record['type'] == name), '')

    def fn_setData(self, format=MISSING, data=MISSING, *_extra):
        for index, value in enumerate((format, data)):
            if value is MISSING:
                raise missing_error('DataTransfer', 'setData', 2, index, constructor=False)
        name = _format(format)
        self._records[:] = [record for record in self._records
                            if not (record['kind'] == 'string' and record['type'] == name)]
        self._records.append({'kind': 'string', 'type': name, 'value': dom_string(data)})
        self._changed()

    def fn_clearData(self, format=MISSING, *_extra):
        if format is MISSING:
            self._records[:] = [record for record in self._records if record['kind'] == 'file']
            self._changed()
            return
        name = _format(format)
        self._records[:] = [record for record in self._records
                            if record['kind'] == 'file' or record['type'] != name]
        self._changed()


FEATURE = FeatureImplementation({
    'DataTransfer': DataTransfer,
    'DataTransferItem': DataTransferItem,
    'DataTransferItemList': DataTransferItemList,
})
