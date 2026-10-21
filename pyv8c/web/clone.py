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
           "TransferPlan", "clone_value", "unread"]

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
_TAG_MESSAGE_PORT = 0x4d

def _is_host(value):
    """Native wrapper identity; never run page getters to classify a value."""
    return not isinstance(value._host_value(), pyv8.JSObject)


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


def _write_host(value, writer, transfer=None):
    """A host object the serializer met, written as its own bytes (Blink's ``WriteDOMObject``)."""
    from .bom.filesystem import File
    from .fileapi.blob import Blob

    item = value._host_value() if isinstance(value, pyv8.JSObject) else value
    if transfer is not None:
        for index, (port, _view) in enumerate(transfer.ports):
            if item is port:
                writer.write_tag(_TAG_MESSAGE_PORT)
                writer.write_uint32(index)
                return True
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
    raise DataCloneError(f"{type(item).__name__} object could not be cloned.")


def _read_host(reader, ports=()):
    """The host object a stream describes, as the host's value for the realm reading it.

    The callback returns the host object for the binding to wrap in the reading
    realm. Native stream primitives are valid only during this callback; retain
    the decoded data, never the reader itself.
    """
    from .bom.filesystem import File
    from .fileapi.blob import Blob

    tag = reader.read_tag()
    if tag == _TAG_MESSAGE_PORT:
        index = reader.read_uint32()
        if index >= len(ports):
            raise DataCloneError("Invalid transferred MessagePort index.")
        return ports[index]
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
    raise DataCloneError(f"{type(item).__name__} object could not be cloned.")


def structured_clone(value, context=None, *, caller=None, transfer=None):
    """The wire a message carries: *value* serialized where it is.

    Without a realm there is nothing to serialize in, so the host's own copy is what comes out
    and the reader is handed that unchanged.
    """
    if context is None:
        return _host_copy(value, set())
    try:
        result = Wire(pyv8.Serializer(context).serialize(
            value, host_check=_is_host,
            host_writer=lambda value, writer: _write_host(value, writer, transfer)))
        result.ports = tuple(view for _port, view in transfer.ports) if transfer else ()
        return result
    except pyv8.JSException as error:
        raise DataCloneError(_refusal(error)) from None
    except DOMException as error:
        if caller is None or error.get_name() != "DataCloneError":
            raise
        raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}': {error.get_message()}") from None


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
        return pyv8.Serializer(context).deserialize(
            bytes(value), host_reader=lambda reader: _read_host(reader, getattr(value, "ports", ())))
    except pyv8.JSException as error:
        raise DataCloneError(_refusal(error)) from None




#: Shared JS helpers, compiled once per realm: a realm that
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


def _binary_kind(value, context):
    return value._buffer_kind() if isinstance(value, pyv8.JSObject) else None


def message_transfer(value):
    """The transfer list a ``postMessage`` second argument names, in either of its shapes."""
    if is_nullish(value):
        return ()
    iterator = value._iterator_method() if isinstance(value, pyv8.JSObject) else None
    if isinstance(value, (list, tuple)) or (iterator is not None and not is_nullish(iterator)):
        return sequence(value, iterator_method=iterator)
    options = dictionary(value, name="PostMessageOptions")
    return sequence(options.get("transfer", ()))


class TransferPlan:
    """Validate once before serialization; mutate ownership only on commit."""
    __slots__ = ('items', 'ports', 'context', 'committed')

    def __init__(self, transfer=(), *, caller="Window"):
        from .bom.messaging import MessagePort
        self.context = pyv8.current_context()
        self.items = []
        self.ports = []
        self.committed = False
        listed = [] if is_nullish(transfer) else list(transfer)
        seen = []
        for index, raw in enumerate(listed):
            item = raw._host_value() if isinstance(raw, pyv8.JSObject) else raw
            if any(raw is previous or (isinstance(raw, pyv8.JSObject)
                   and isinstance(previous, pyv8.JSObject) and raw == previous)
                   or item is (previous._host_value() if isinstance(previous, pyv8.JSObject) else previous)
                   for previous in seen):
                raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                     " Transfer list contains a duplicate object.")
            seen.append(raw)
            kind = _binary_kind(raw, self.context)
            if kind == 'buffer':
                self.items.append(('buffer', raw))
            elif isinstance(item, MessagePort) and not item._detached:
                self.items.append(('port', item))
                self.ports.append((item, MessagePort()))
            else:
                if kind == 'detached':
                    reason = f"ArrayBuffer at index {index} is already detached."
                elif isinstance(item, MessagePort):
                    reason = f"Port at index {index} is already neutered."
                else:
                    reason = f"Value at index {index} does not have a transferable type."
                raise DataCloneError(f"Failed to execute 'postMessage' on '{caller}':"
                                     f" {reason}")

    def commit(self, services=None):
        if self.committed:
            raise RuntimeError('transfer already committed')
        for kind, item in self.items:
            if (kind == 'buffer' and item._buffer_kind() != 'buffer') or (kind == 'port' and item._detached):
                raise DataCloneError('A transferable was detached during serialization.')
        self.committed = True
        for port, view in self.ports:
            view._services = services
            twin = port._detach()
            view._pending, port._pending = port._pending, []
            view._outbox, port._outbox = port._outbox, []
            view._twin = twin
            if twin is not None:
                twin._twin = view
        for kind, item in self.items:
            if kind == 'buffer':
                item._detach_buffer()
        return [view for _port, view in self.ports]


def clone_value(services, interface, value, options):
    from ..foundation.arguments import MISSING, missing_error
    if value is MISSING:
        raise missing_error(interface, 'structuredClone', 1, 0, constructor=False)
    plan = TransferPlan(message_transfer(options), caller=interface)
    context = pyv8.current_context()
    data = structured_clone(value, context=context, transfer=plan)
    result = rebuild_clone(data, context=context)
    plan.commit(services)
    return result
