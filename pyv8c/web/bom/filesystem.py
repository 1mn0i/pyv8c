"""Legacy Chromium FileSystem API used by ``webkitRequestFileSystem``.

The API is deprecated in Chromium, but remains observable by older pages.  This
implementation deliberately keeps the old callback surface while using a
per-page temporary directory as its backing store.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, callback_value, dom_string, integer, missing_error
from ...foundation.errors import V8TypeError
from ..fetch.blob import Blob
from ..dom.events import EventTarget

__all__ = ["FileSystem", "Entry", "DirectoryEntry", "FileEntry", "DirectoryReader", "FileWriter", "File"]


def _post(services, callback, *args):
    if callback is None:
        return
    task = getattr(services, "task_services", None) if services is not None else None
    if task is None:
        callback(*args)
    else:
        task.post_task(lambda: callback(*args))


def _callback(value, name, position, optional=False):
    if value is pyv8.Undefined or value is None or value is pyv8.Null:
        if optional:
            return None
        raise V8TypeError(f"Failed to execute '{name}' on 'Entry': parameter {position} is not a function.")
    return callback_value(value)


@interface("File", "Blob", exposed_in=("Window",))
class File(Blob):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="name", getter="get_name", side_effect=1),
                    AttributeSpec(name="lastModified", getter="get_lastModified", side_effect=1),
                    AttributeSpec(name="size", getter="get_size", side_effect=1),
                    AttributeSpec(name="type", getter="get_type", side_effect=1)),
        constructor_behavior=0, has_constructor=1, to_string_tag="File",
    )

    def __init__(self, path, **_):
        self._path = Path(path)
        self._data = self._path.read_bytes() if self._path.exists() else b""
        self._type = ""
    def get_name(self): return self._path.name
    def get_lastModified(self): return int(self._path.stat().st_mtime * 1000) if self._path.exists() else 0
    def get_size(self): return self._path.stat().st_size if self._path.exists() else 0
    def get_type(self): return ""


@interface("FileWriter", "EventTarget", exposed_in=("Window",))
class FileWriter(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="position", getter="get_position"), AttributeSpec(name="length", getter="get_length")),
        methods=(MethodSpec(name="write", callback="fn_write", length=1), MethodSpec(name="seek", callback="fn_seek", length=1), MethodSpec(name="truncate", callback="fn_truncate", length=1)),
        constructor_behavior=0, has_constructor=1, to_string_tag="FileWriter",
    )
    def __init__(self, path, services=None, **_):
        super().__init__(services=services)
        self._path, self._position, self._services = Path(path), 0, services
    def get_position(self): return self._position
    def get_length(self): return self._path.stat().st_size if self._path.exists() else 0
    def fn_seek(self, offset=MISSING, *_extra):
        if offset is MISSING: raise missing_error("FileWriter", "seek", 1, 0)
        self._position = max(0, integer(offset, signed=True))
    def fn_truncate(self, size=MISSING, *_extra):
        if size is MISSING: raise missing_error("FileWriter", "truncate", 1, 0)
        size = max(0, integer(size, bits=64))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("r+b" if self._path.exists() else "wb") as fh: fh.truncate(size)
        self._position = min(self._position, size)
    def fn_write(self, data=MISSING, *_extra):
        if data is MISSING: raise missing_error("FileWriter", "write", 1, 0)
        raw = dom_string(data).encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        mode = "r+b" if self._path.exists() else "wb"
        with self._path.open(mode) as fh:
            fh.seek(self._position); fh.write(raw); self._position += len(raw)


@interface("Entry", None, exposed_in=("Window",))
class Entry:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="isFile", getter="get_isFile", side_effect=1), AttributeSpec(name="isDirectory", getter="get_isDirectory", side_effect=1), AttributeSpec(name="name", getter="get_name", side_effect=1), AttributeSpec(name="fullPath", getter="get_fullPath", side_effect=1), AttributeSpec(name="filesystem", getter="get_filesystem", side_effect=1)),
        methods=(MethodSpec(name="getMetadata", callback="fn_getMetadata", length=2), MethodSpec(name="remove", callback="fn_remove", length=2), MethodSpec(name="getParent", callback="fn_getParent", length=2), MethodSpec(name="toURL", callback="fn_toURL")),
        constructor_behavior=0, has_constructor=1, to_string_tag="Entry",
    )
    def __init__(self, fs, path, **_): self._fs, self._path = fs, Path(path)
    def get_isFile(self): return self._path.is_file()
    def get_isDirectory(self): return self._path.is_dir()
    def get_name(self): return "" if self._path == self._fs._root else self._path.name
    def get_fullPath(self): return "/" + str(self._path.relative_to(self._fs._root)).replace(os.sep, "/") if self._path != self._fs._root else "/"
    def get_filesystem(self): return self._fs
    def fn_toURL(self, *_extra): return "filesystem:" + self._fs._origin + "/temporary" + self.get_fullPath()
    def fn_getMetadata(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "getMetadata", 1, 0)
        _post(self._fs._services, _callback(success, "getMetadata", 1), {"modificationTime": self._path.stat().st_mtime if self._path.exists() else 0, "size": self._path.stat().st_size if self._path.is_file() else 0})
    def fn_getParent(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "getParent", 1, 0)
        _post(self._fs._services, _callback(success, "getParent", 1), Entry(self._fs, self._path.parent))
    def fn_remove(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("Entry", "remove", 1, 0)
        cb = _callback(success, "remove", 1)
        try:
            if self._path.is_dir(): self._path.rmdir()
            elif self._path.exists(): self._path.unlink()
            _post(self._fs._services, cb)
        except Exception as exc: _post(self._fs._services, _callback(error, "remove", 2, True), exc)


@interface("FileEntry", "Entry", exposed_in=("Window",))
class FileEntry(Entry):
    __v8_idl_spec__ = IdlSpec(methods=(MethodSpec(name="file", callback="fn_file", length=2), MethodSpec(name="createWriter", callback="fn_createWriter", length=2)), constructor_behavior=0, has_constructor=1, to_string_tag="FileEntry")
    def fn_file(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("FileEntry", "file", 1, 0)
        _post(self._fs._services, _callback(success, "file", 1), File(self._path))
    def fn_createWriter(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("FileEntry", "createWriter", 1, 0)
        _post(self._fs._services, _callback(success, "createWriter", 1), FileWriter(self._path, self._fs._services))


@interface("DirectoryReader", None, exposed_in=("Window",))
class DirectoryReader:
    __v8_idl_spec__ = IdlSpec(methods=(MethodSpec(name="readEntries", callback="fn_readEntries", length=1),), constructor_behavior=0, has_constructor=1, to_string_tag="DirectoryReader")
    def __init__(self, fs, path, **_): self._fs, self._path, self._done = fs, Path(path), False
    def fn_readEntries(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("DirectoryReader", "readEntries", 1, 0)
        result = [] if self._done else [self._fs._entry(p) for p in sorted(self._path.iterdir()) if p.exists()]
        self._done = True
        _post(self._fs._services, _callback(success, "readEntries", 1), result)


@interface("DirectoryEntry", "Entry", exposed_in=("Window",))
class DirectoryEntry(Entry):
    __v8_idl_spec__ = IdlSpec(methods=(MethodSpec(name="createReader", callback="fn_createReader"), MethodSpec(name="getFile", callback="fn_getFile", length=3), MethodSpec(name="getDirectory", callback="fn_getDirectory", length=3), MethodSpec(name="removeRecursively", callback="fn_removeRecursively", length=2)), constructor_behavior=0, has_constructor=1, to_string_tag="DirectoryEntry")
    def fn_createReader(self, *_extra): return DirectoryReader(self._fs, self._path)
    def _child(self, name):
        name = dom_string(name)
        if not name or "/" in str(name) or str(name) in (".", ".."): raise V8TypeError("Invalid path")
        return self._path / str(name)
    def fn_getFile(self, path=MISSING, options=None, success=MISSING, error=None, *_extra):
        if path is MISSING: raise missing_error("DirectoryEntry", "getFile", 1, 0)
        if success is MISSING and callable(options): success, options = options, None
        target = self._child(path); target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists(): target.touch()
        _post(self._fs._services, _callback(success, "getFile", 3), FileEntry(self._fs, target))
    def fn_getDirectory(self, path=MISSING, options=None, success=MISSING, error=None, *_extra):
        if path is MISSING: raise missing_error("DirectoryEntry", "getDirectory", 1, 0)
        if success is MISSING and callable(options): success, options = options, None
        target = self._child(path); target.mkdir(parents=True, exist_ok=True)
        _post(self._fs._services, _callback(success, "getDirectory", 3), DirectoryEntry(self._fs, target))
    def fn_removeRecursively(self, success=MISSING, error=None, *_extra):
        if success is MISSING: raise missing_error("DirectoryEntry", "removeRecursively", 1, 0)
        cb = _callback(success, "removeRecursively", 1)
        try: shutil.rmtree(self._path); _post(self._fs._services, cb)
        except Exception as exc: _post(self._fs._services, _callback(error, "removeRecursively", 2, True), exc)


@interface("DOMFileSystem", None, exposed_in=("Window",))
class DOMFileSystem:
    __v8_idl_spec__ = IdlSpec(attributes=(AttributeSpec(name="name", getter="get_name", side_effect=1), AttributeSpec(name="root", getter="get_root", side_effect=1)), constructor_behavior=0, has_constructor=1, to_string_tag="FileSystem")
    def __init__(self, name, root, services=None, origin="null", **_): self._name, self._root, self._services, self._origin = name, Path(root), services, origin; self._root.mkdir(parents=True, exist_ok=True)
    def get_name(self): return self._name
    def get_root(self): return DirectoryEntry(self, self._root)
    def _entry(self, path): return DirectoryEntry(self, path) if Path(path).is_dir() else FileEntry(self, path)


FileSystem = DOMFileSystem


def request_file_system(window, kind, size, success, error):
    kind = integer(kind, bits=16)
    if kind not in (0, 1): raise V8TypeError("Invalid file system type")
    size = integer(size, bits=64)
    if size < 0: raise V8TypeError("Invalid file system size")
    success = callback_value(success)
    if error is not pyv8.Undefined and error is not None and error is not pyv8.Null: error = callback_value(error)
    root = tempfile.mkdtemp(prefix="pyv8-fs-")
    origin = getattr(getattr(window._services, "realm_services", None), "url", "null")
    fs = FileSystem("temporary" if kind == 0 else "persistent", root, services=window._services, origin=origin)
    _post(window._services, success, fs)
