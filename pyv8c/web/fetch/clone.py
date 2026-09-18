"""Structured-clone support for the message and storage channels.

The Window, Worker and IndexedDB channels all move JavaScript values across a
realm boundary, so all three share this one implementation.  It sits beside
:class:`~pyv8c.web.fetch.blob.Blob`, the web type it knows how to deep-copy.
"""

import v8n

from ...foundation.arguments import MISSING, dictionary, is_nullish, materialize, sequence
from ...foundation.errors import DataCloneError

#: A binary value's bytes, whatever its element type; ``null`` for anything that
#: is not one, so "not binary" is distinguishable from "an empty one".  Both
#: shapes are here because a Python ``bytes`` arrives in JavaScript as an
#: ArrayBuffer and a value that travelled out and back is one, while a typed
#: array is a view over a buffer and has to be sliced out of it.
_BINARY_TO_BYTES = (
    "x => ArrayBuffer.isView(x)"
    " ? Array.from(new Uint8Array(x.buffer, x.byteOffset, x.byteLength))"
    " : (x instanceof ArrayBuffer ? Array.from(new Uint8Array(x)) : null)"
)


def message_transfer(value):
    if is_nullish(value):
        return ()
    iterator = value.iterator() if isinstance(value, v8n.Value) else MISSING
    if isinstance(value, (list, tuple)) or (iterator is not MISSING and not is_nullish(iterator)):
        return sequence(value, iterator_method=iterator)
    options = dictionary(value, name="PostMessageOptions")
    return sequence(options.get("transfer", ()))


def structured_clone(value, transfer=(), context=None):
    if transfer not in (None, (), [], v8n.Undefined, v8n.Null):
        raise DataCloneError("Transfer lists are not supported by this runtime")
    active = set()

    def clone(item):
        from .blob import Blob

        item = materialize(item)

        if item is None or item is v8n.Null or item is v8n.Undefined:
            return item
        if isinstance(item, (bool, int, float, str)):
            return item
        if isinstance(item, Blob):
            return Blob([bytes(item._data)], {"type": item.get_type()})
        if isinstance(item, (bytes, bytearray, memoryview)):
            return bytes(item)
        # A handle: a typed array is bytes, and anything else is a JavaScript
        # object this layer does not know, so it is cloned member by member.
        if isinstance(item, v8n.Value):
            if context is not None and item.is_valid:
                try:
                    view = context.evaluate(_BINARY_TO_BYTES)(item)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    view = None
                if view is not None:
                    return bytes(view.to_python())
            marker = id(item)
            if marker in active:
                raise DataCloneError("Cyclic message data is not supported")
            active.add(marker)
            try:
                return {str(key): clone(item.get(key)) for key in item.keys()}
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
