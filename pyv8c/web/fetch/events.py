"""Events shared by Fetch, WebSocket, XHR, and Worker APIs."""
import v8n
from v8n import AttributeSpec, IdlSpec

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    dom_string,
    event_constructor_arguments,
    integer,
    is_nullish,
    sequence,
    usv_string,
)
from ..dom.events import Event


@interface('MessageEvent', 'Event')
class MessageEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="data", getter="get_data", side_effect=1),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(name="lastEventId", getter="get_lastEventId", side_effect=1),
            AttributeSpec(name="source", getter="get_source", side_effect=1),
            AttributeSpec(name="ports", getter="get_ports", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, init = event_constructor_arguments('MessageEvent', event_type, options)
        super().__init__(event_type, init, **kw)
        self.data = init.get("data", v8n.Null)
        self.origin = usv_string(init.get("origin", ""))
        self.lastEventId = dom_string(init.get("lastEventId", ""))
        self.source = init.get("source", v8n.Null)
        ports = init.get("ports", v8n.Undefined)
        self.ports = [] if is_nullish(ports) else sequence(ports)

    def get_data(self): return self.data
    def get_origin(self): return self.origin
    def get_lastEventId(self): return self.lastEventId
    def get_source(self): return self.source
    def get_ports(self): return self.ports


@interface('CloseEvent', 'Event')
class CloseEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="code", getter="get_code", side_effect=1),
            AttributeSpec(name="reason", getter="get_reason", side_effect=1),
            AttributeSpec(name="wasClean", getter="get_wasClean", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, init = event_constructor_arguments('CloseEvent', event_type, options)
        super().__init__(event_type, init, **kw)
        self.code = integer(init.get("code", 0), bits=16)
        self.reason = usv_string(init.get("reason", ""))
        self.was_clean = boolean(init.get("wasClean", False))

    def get_code(self): return self.code
    def get_reason(self): return self.reason
    def get_wasClean(self): return self.was_clean


@interface('ErrorEvent', 'Event')
class ErrorEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="message", getter="get_message", side_effect=1),
            AttributeSpec(name="filename", getter="get_filename", side_effect=1),
            AttributeSpec(name="lineno", getter="get_lineno", side_effect=1),
            AttributeSpec(name="colno", getter="get_colno", side_effect=1),
            AttributeSpec(name="error", getter="get_error", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, init = event_constructor_arguments('ErrorEvent', event_type, options)
        super().__init__(event_type, init, **kw)
        self.error = init.get("error", v8n.Null)
        self.message = dom_string(init.get("message", ""))
        self.filename = usv_string(init.get("filename", ""))
        self.lineno = integer(init.get("lineno", 0), bits=32)
        self.colno = integer(init.get("colno", 0), bits=32)

    def get_message(self): return self.message
    def get_filename(self): return self.filename
    def get_lineno(self): return self.lineno
    def get_colno(self): return self.colno
    def get_error(self): return self.error


@interface('ProgressEvent', 'Event')
class ProgressEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="lengthComputable", getter="get_lengthComputable", side_effect=1),
            AttributeSpec(name="loaded", getter="get_loaded", side_effect=1),
            AttributeSpec(name="total", getter="get_total", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, init = event_constructor_arguments('ProgressEvent', event_type, options)
        super().__init__(event_type, init, **kw)
        self.length_computable = boolean(init.get("lengthComputable", False))
        self.loaded = integer(init.get("loaded", 0), bits=64)
        self.total = integer(init.get("total", 0), bits=64)

    def get_lengthComputable(self): return self.length_computable
    def get_loaded(self): return self.loaded
    def get_total(self): return self.total


__all__ = ["MessageEvent", "CloseEvent", "ErrorEvent", "ProgressEvent"]
