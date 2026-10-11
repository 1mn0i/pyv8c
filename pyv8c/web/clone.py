"""Structured-clone support for the message and storage channels.

The clone is the one a browser has: the value is **serialized where it is sent** and
**deserialized where it is read**, over V8's own wire format (``pyv8.Serializer``).  Chromium's
``structuredClone`` is exactly that round trip -- ``SerializeMessageByMove`` and then
``Deserialize``, in ``UniversalGlobalScope::structuredClone`` -- so what crosses is what a
browser crosses: objects, arrays, ``Map``/``Set``/``Date``/``RegExp``, an ``Error`` with its
name, message, cause and stack, buffers and views, cycles, and transferred buffers whose bytes
the sender gives up.

What V8 cannot know is the host.  A Blob, a File or a DOMException is written as its own bytes
by :func:`_write_host` and built back in the reading realm by :func:`_read_host` -- Blink's
``WriteDOMObject``/``ReadDOMObject`` pair -- and a host object this pair cannot write is refused
with ``DataCloneError``, as a browser refuses it.
"""
import weakref

import pyv8

from ..foundation.arguments import dictionary, is_nullish, materialize, sequence
from ..foundation.logging import logger
from .page_errors import DataCloneError, DOMException

__all__ = ["Wire", "js_helper", "message_transfer", "rebuild_clone", "structured_clone",
           "transfer_ports", "unread"]

#: What a reader has not been handed yet.  A message and a stored value hold this until the
#: realm reading them asks once, so what that realm was handed is what it keeps.
unread = object()


class Wire(bytes):
    """A payload, told apart from bytes that are just data: Blink's ``SerializedScriptValue``."""




#: The tags the host's own objects are written with.  The letters are Blink's
#: (``serialization_tag.h``: 'b' Blob, 'f' File, 'x' DOMException); what follows each tag is
#: this pair of callbacks' own layout, since only they read it.
_TAG_BLOB = 0x62
_TAG_FILE = 0x66
_TAG_DOM_EXCEPTION = 0x78

#: What V8 writes by itself, named by constructor.  A value whose constructor is none of these
#: is the host's -- the question Blink's ``V8ScriptValueSerializer::IsHostObject`` asks of its
#: wrappables -- so a DOMException or a Blob reaches the callbacks below instead of being
#: written as an object with no properties.
_V8_VALUE_NAMES = frozenset({
    "Object", "Array", "Map", "Set", "Date", "RegExp", "Error", "EvalError", "RangeError",
    "ReferenceError", "SyntaxError", "TypeError", "URIError", "AggregateError", "ArrayBuffer",
    "SharedArrayBuffer", "DataView", "Boolean", "Number", "String", "BigInt", "Symbol",
    "Uint8Array", "Int8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array", "Int32Array",
    "Uint32Array", "Float16Array", "Float32Array", "Float64Array", "BigInt64Array",
    "BigUint64Array",
})


def _is_host(value):
    """Whether V8 should hand this object to the host rather than write it itself."""
    try:
        name = value._get("constructor")._get("name")
    except BaseException as exc:  # a value with no readable constructor is not a plain one
        logger.debug("a value could not be asked what it is: {}", exc)
        return False
    return str(name) not in _V8_VALUE_NAMES


def _write_text(writer, text):
    data = text.encode("utf-8")
    writer.write_uint32(len(data))
    writer.write_raw_bytes(data)


def _read_text(reader):
    return reader.read_raw_bytes(reader.read_uint32()).decode("utf-8")


def _write_bytes(writer, data):
    writer.write_uint32(len(data))
    writer.write_raw_bytes(data)


def _read_bytes(reader):
    return reader.read_raw_bytes(reader.read_uint32())


def _write_host(value, writer):
    """A host object the serializer met, written as its own bytes (Blink's ``WriteDOMObject``)."""
    from .bom.filesystem import File
    from .fileapi.blob import Blob

    item = materialize(value)
    if isinstance(item, File):
        #: A ``File`` is a ``Blob`` that remembers its name and timestamp, so it crosses as one
        #: instead of as the blob it is made of.
        writer.write_tag(_TAG_FILE)
        _write_text(writer, item.get_name())
        _write_text(writer, item.get_type())
        writer.write_double(float(item.get_lastModified()))
        _write_bytes(writer, bytes(item._data))
        return True
    if isinstance(item, Blob):
        writer.write_tag(_TAG_BLOB)
        _write_text(writer, item.get_type())
        _write_bytes(writer, bytes(item._data))
        return True
    if isinstance(item, DOMException):
        writer.write_tag(_TAG_DOM_EXCEPTION)
        _write_text(writer, item.get_name())
        _write_text(writer, item.get_message())
        #: Blink writes the stack field and never fills it, so its scheme can grow one.
        _write_text(writer, "")
        return True
    raise DataCloneError(f"The object of type {type(item).__name__!r} could not be cloned")


def _read_host(reader):
    """The host object a stream describes, as the host's value for the realm reading it.

    A Python object, not a JavaScript one: this callback runs *inside* V8's deserializer, so
    entering the realm here (``exec_js``) would take the isolate a second time and never
    return -- measured once, as a test that sat in ``run_until_idle`` for good.  What the host
    answers is handed to the realm by the binding, as every other crossing is.
    """
    from .bom.filesystem import File
    from .fileapi.blob import Blob

    tag = reader.read_tag()
    if tag == _TAG_BLOB:
        type_ = _read_text(reader)
        return Blob([_read_bytes(reader)], {"type": type_})
    if tag == _TAG_FILE:
        name = _read_text(reader)
        type_ = _read_text(reader)
        modified = int(reader.read_double())
        return File([_read_bytes(reader)], name, {"type": type_, "lastModified": modified})
    if tag == _TAG_DOM_EXCEPTION:
        name = _read_text(reader)
        message = _read_text(reader)
        _read_text(reader)                      # the stack field, empty: dropped, as Blink does
        exception = DOMException(message, name)
        exception.__v8_error_stack__ = False
        return exception
    #: The wording Blink's deserializer uses for data it cannot build.
    raise DataCloneError("Unable to deserialize cloned data.")


def _refusal(error):
    """V8's refusal in the page's own words.

    Only the type changes: the message is the one Chrome shows (``... could not be cloned.``),
    while a page expects a ``DataCloneError``.  A refusal the host raised itself never comes
    through here -- it is a Python error, and it is raised as it is.
    """
    original = getattr(error, "original", None)
    message = ""
    try:
        if original is not None:
            message = str(original._get("message"))
    except BaseException as exc:  # noqa: BLE001
        logger.debug("a refusal could not be read back: {}", exc)
    return message or str(error).split(" (", 1)[0]


def _host_copy(value, active):
    """The host's own copy of one value: the shapes a realm-less caller can hold.

    A callable, an opaque object or a cycle is what a browser refuses, and a view crosses as
    the bytes it looks at rather than as the elements it indexes.
    """
    from .bom.filesystem import File
    from .fileapi.blob import Blob

    item = materialize(value)
    if item is None or item is pyv8.Null or item is pyv8.Undefined:
        return item
    if isinstance(item, (bool, int, float, str, bytes, bytearray, memoryview)):
        return item
    if isinstance(item, Blob):
        return Blob([bytes(item._data)], {"type": item.get_type()})
    if isinstance(item, File):
        return File([bytes(item._data)], item.get_name(),
                    {"type": item.get_type(), "lastModified": int(item.get_lastModified())})
    if type(item).__name__ in ("TypedArray", "JSTypeArray"):
        return bytes(item._bytes())
    if isinstance(item, pyv8.JSObject):
        return item
    if isinstance(item, (dict, list, tuple)):
        marker = id(item)
        if marker in active:
            raise DataCloneError("Cyclic message data is not supported")
        active.add(marker)
        try:
            if isinstance(item, dict):
                return {str(key): _host_copy(child, active) for key, child in item.items()}
            return [_host_copy(child, active) for child in item]
        finally:
            active.discard(marker)
    raise DataCloneError(f"The object of type {type(item).__name__!r} could not be cloned")


def structured_clone(value, context=None):
    """The wire a message carries: *value* serialized where it is.

    Without a realm there is nothing to serialize in, so the host's own copy is what comes out
    and the reader is handed that unchanged.
    """
    if context is None:
        return _host_copy(value, set())
    try:
        return Wire(pyv8.Serializer(context).serialize(
            value, host_check=_is_host, host_writer=_write_host))
    except pyv8.JSException as error:
        raise DataCloneError(_refusal(error)) from None


def rebuild_clone(value, context=None):
    """Hand the realm reading *value* what it describes.

    A message's ``data``, a request's ``result`` and a stored value are the wire
    :func:`structured_clone` produced, so the realm reading one is handed its own objects: its
    own buffers, its own ``Map``, its own ``Error``.  Without a realm to build in, the wire is
    the bytes it is -- which is what a host reading it through Python has.
    """
    if not isinstance(value, Wire):
        return value
    if context is None:
        return bytes(value)
    try:
        return pyv8.Serializer(context).deserialize(bytes(value), host_reader=_read_host)
    except pyv8.JSException as error:
        raise DataCloneError(_refusal(error)) from None




#: The helpers the transfer list asks a realm to run, one compilation per realm: a realm that
#: cannot be referenced is compiled per call, which costs time and nothing else.
_HELPERS = weakref.WeakKeyDictionary()


def js_helper(context, source):
    """*source* compiled in *context*, built once per realm."""
    try:
        helpers = _HELPERS[context]
    except TypeError:
        return context.exec_js(source)
    except KeyError:
        helpers = _HELPERS[context] = {}
    compiled = helpers.get(source)
    if compiled is None:
        compiled = helpers[source] = context.exec_js(source)
    return compiled


#: What a transfer list may name besides a port, asked of the realm that holds the value: a
#: detached buffer has nothing left in it, and the realm's own ``detached`` is what tells that
#: from a buffer that was always empty.
_BINARY_KIND = """value => {
  if (Object.prototype.toString.call(value) === '[object ArrayBuffer]')
    return value.detached ? 'detached' : 'buffer';
  if (ArrayBuffer.isView(value)) return 'view';
  return null;
}"""

#: Taking the bytes away from the sender: ``transfer()`` empties the buffer it is called on,
#: which is how a page hands one over.
_DETACH = """buffer => {
  if (typeof buffer.transfer === 'function') buffer.transfer();
  else structuredClone(buffer, {transfer: [buffer]});
}"""


def _binary_kind(value, context):
    """``'buffer'``/``'view'``/``'detached'`` for a value shaped like a transferable."""
    if context is None:
        return None
    try:
        answer = js_helper(context, _BINARY_KIND).call(None, value)
    except BaseException as exc:  # a value the realm cannot describe is simply not one
        logger.debug("a transfer list value could not be classified: {}", exc)
        return None
    #: The realm answers in a string, and answers ``null`` for everything else.
    return answer if isinstance(answer, str) else None


def _detach_buffer(value, context):
    if context is not None:
        js_helper(context, _DETACH).call(None, value)


def message_transfer(value):
    """The transfer list a ``postMessage`` second argument names, in either of its shapes."""
    if is_nullish(value):
        return ()
    iterator = value._iterator_method() if isinstance(value, pyv8.JSObject) else None
    if isinstance(value, (list, tuple)) or (iterator is not None and not is_nullish(iterator)):
        return sequence(value, iterator_method=iterator)
    options = dictionary(value, name="PostMessageOptions")
    return sequence(options.get("transfer", ()))


def transfer_ports(transfer=(), services=None, caller="Window"):
    """The ports a transfer list moves, once everything it named has been checked.

    A ``MessagePort`` moves: the reference left behind is neutered while the port itself
    arrives in the message event's ``ports``.  An ``ArrayBuffer`` is handed over as well -- its
    bytes travelled in the wire already, and taking them away now is what leaves the sender
    with nothing.  A view of a buffer is not transferable, and neither is a buffer that was
    already detached; Chrome names the index it refuses, so the wording says which index and
    which way it failed.
    """
    from .bom.messaging import MessagePort

    if is_nullish(transfer):
        return []
    listed = list(transfer)
    context = pyv8.current_context()
    planned = []
    for index, raw in enumerate(listed):
        kind = _binary_kind(raw, context)
        if kind == "buffer":
            planned.append(("buffer", raw))
            continue
        if kind == "detached":
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" ArrayBuffer at index {index} is already detached.")
        if kind == "view":
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Value at index {index} does not have a transferable type.")
        item = materialize(raw)
        if not isinstance(item, MessagePort):
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Value at index {index} does not have a transferable type.")
        if item._detached:
            raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                 f" Port at index {index} is already neutered.")
        planned.append(("port", item))

    moved = []
    for kind, item in planned:
        if kind == "port":
            moved.append(item._moved_view(services))
        else:
            _detach_buffer(item, context)
    return moved
