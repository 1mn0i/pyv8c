"""Events shared by Fetch, WebSocket, XHR, and Worker APIs."""
import pyv8
from pyv8 import AttributeSpec, IdlSpec

from ...engine.registry import interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    dictionary,
    dom_string,
    integer,
    is_nullish,
    missing_error,
    usv_string,
)
from ..dom.events import Event


@interface('MessageEvent', 'Event')
class MessageEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="data", getter="get_data", side_effect=1),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(name="source", getter="get_source", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        if event_type is MISSING:
            raise missing_error('MessageEvent', 'constructor', 1, 0, constructor=True)
        event_type = dom_string(event_type)
        if options is pyv8.Undefined:
            options = None
        if options is not pyv8.Undefined:
            if not is_nullish(options):
                options = dictionary(options)
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.data = options.get("data", pyv8.Null)
        self.origin = usv_string(options.get("origin", ""))
        self.source = options.get("source", pyv8.Null)

    def get_data(self): return self.data
    def get_origin(self): return self.origin
    def get_source(self): return self.source


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
        if event_type is MISSING:
            raise missing_error('CloseEvent', 'constructor', 1, 0, constructor=True)
        event_type = dom_string(event_type)
        if options is pyv8.Undefined:
            options = None
        if options is not pyv8.Undefined:
            if not is_nullish(options):
                options = dictionary(options)
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.code = integer(options.get("code", 0), bits=16)
        self.reason = usv_string(options.get("reason", ""))
        self.was_clean = boolean(options.get("wasClean", False))

    def get_code(self): return self.code
    def get_reason(self): return self.reason
    def get_wasClean(self): return self.was_clean


@interface('ErrorEvent', 'Event')
class ErrorEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="message", getter="get_message", side_effect=1),
            AttributeSpec(name="error", getter="get_error", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        if event_type is MISSING:
            raise missing_error('ErrorEvent', 'constructor', 1, 0, constructor=True)
        event_type = dom_string(event_type)
        if options is pyv8.Undefined:
            options = None
        if options is not pyv8.Undefined:
            if not is_nullish(options):
                options = dictionary(options)
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.error = options.get("error", pyv8.Null)
        self.message = dom_string(options.get("message", ""))

    def get_message(self): return self.message
    def get_error(self): return self.error


__all__ = ["MessageEvent", "CloseEvent", "ErrorEvent"]
