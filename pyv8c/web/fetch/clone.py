"""Structured-clone support for the message and storage channels.

The Window, Worker and IndexedDB channels all move JavaScript values across a
realm boundary, so all three share this one implementation.  It sits beside
:class:`~pyv8c.web.fetch.blob.Blob`, the web type it knows how to deep-copy.
"""

import pyv8

from ...foundation.arguments import MISSING, dictionary, is_nullish, materialize, sequence
from ...foundation.errors import DataCloneError


def message_transfer(value):
    if is_nullish(value):
        return ()
    iterator = value._iterator_method() if isinstance(value, pyv8.JSObject) else MISSING
    if isinstance(value, (list, tuple)) or (iterator is not MISSING and not is_nullish(iterator)):
        return sequence(value, iterator_method=iterator)
    options = dictionary(value, name="PostMessageOptions")
    return sequence(options.get("transfer", ()))


def structured_clone(value, transfer=(), context=None):
    if transfer not in (None, (), [], pyv8.Undefined, pyv8.Null):
        raise DataCloneError("Transfer lists are not supported by this runtime")
    active = set()

    def clone(item):
        from .blob import Blob

        item = materialize(item)

        if item is None or item is pyv8.Null or item is pyv8.Undefined:
            return item
        if isinstance(item, (bool, int, float, str)):
            return item
        if isinstance(item, Blob):
            return Blob([bytes(item._data)], {"type": item.get_type()})
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item)
        if type(item).__name__ in ("TypedArray", "JSTypeArray"):
            try:
                return bytes(int(item[index]) & 255 for index in range(int(item.length)))
            except Exception as exc:
                raise DataCloneError("The TypedArray could not be cloned") from exc
        if type(item).__name__ == "JSObject":
            if context is not None:
                try:
                    converter = context.exec_js("buffer => Array.from(new Uint8Array(buffer))")
                    return bytes(converter.call(None, item))
                except (AttributeError, TypeError, ValueError):
                    pass
            marker = id(item)
            if marker in active:
                raise DataCloneError("Cyclic message data is not supported")
            active.add(marker)
            try:
                return {str(key): clone(item[key]) for key in list(item)}
            except BaseException as exc:
                raise DataCloneError("The JavaScript object could not be cloned") from exc
            finally:
                active.remove(marker)
        if isinstance(item, (dict, list, tuple)):
            marker = id(item)
            if marker in active:
                raise DataCloneError("Cyclic message data is not supported")
            active.add(marker)
            try:
                if isinstance(item, dict):
                    return {str(key): clone(child) for key, child in item.items()}
                return [clone(child) for child in item]
            finally:
                active.remove(marker)
        raise DataCloneError(f"The object of type {type(item).__name__!r} could not be cloned")

    return clone(value)
