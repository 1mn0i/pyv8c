"""Events shared by Fetch, WebSocket, XHR, and Worker APIs."""

import pyv8
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes
from ..dom.events import Event


@interface("MessageEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class MessageEvent(Event):
    __v8_attribute__ = js_attributes(
        ("data", "get_data", None, 0, 1), ("origin", "get_origin", None, 0, 1),
        ("source", "get_source", None, 0, 1),
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


@interface("CloseEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class CloseEvent(Event):
    __v8_attribute__ = js_attributes(
        ("code", "get_code", None, 0, 1), ("reason", "get_reason", None, 0, 1),
        ("wasClean", "get_wasClean", None, 0, 1),
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


@interface("ErrorEvent", "Event", constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class ErrorEvent(Event):
    __v8_attribute__ = js_attributes(
        ("message", "get_message", None, 0, 1), ("error", "get_error", None, 0, 1),
    )

    def __init__(self, event_type="error", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.message = str(options.get("message", ""))
        self.error = options.get("error", pyv8.Null)

    def get_message(self): return self.message
    def get_error(self): return self.error


__all__ = ["MessageEvent", "CloseEvent", "ErrorEvent"]
