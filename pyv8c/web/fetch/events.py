"""Events shared by Fetch, WebSocket, XHR, and Worker APIs."""
from pyv8 import IdlSpec, AttributeSpec

import pyv8



from ...engine.registry import interface
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

    def __init__(self, event_type="message", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.data = options.get("data", pyv8.Null)
        self.origin = options.get("origin", "")
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

    def __init__(self, event_type="close", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.code = int(options.get("code", 1000))
        self.reason = str(options.get("reason", ""))
        self.was_clean = bool(options.get("wasClean", True))

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

    def __init__(self, event_type="error", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.message = str(options.get("message", ""))
        self.error = options.get("error", pyv8.Null)

    def get_message(self): return self.message
    def get_error(self): return self.error


__all__ = ["MessageEvent", "CloseEvent", "ErrorEvent"]
