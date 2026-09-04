"""DOM event model: events and the target dispatch pipeline."""

import time

import pyv8
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagExposed

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.errors import V8TypeError


_DOCUMENT_NODE = 9  # DOM standard nodeType of Document; hardcoded to avoid a model import cycle.


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface("Event", None, constructible=True, length=1, tag="Event")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Event:
    __v8_attribute__ = js_attributes(
        ("type", "get_type", None, 0, 1), ("target", "get_target", None, 0, 1),
        ("currentTarget", "get_currentTarget", None, 0, 1), ("bubbles", "get_bubbles", None, 0, 1),
        ("cancelable", "get_cancelable", None, 0, 1), ("defaultPrevented", "get_defaultPrevented", None, 0, 1),
        ("eventPhase", "get_eventPhase", None, 0, 1), ("cancelBubble", "get_cancelBubble", "set_cancelBubble", 0, 0),
        ("timeStamp", "get_timeStamp", None, 0, 1), ("isTrusted", "get_isTrusted", None, 0, 1),
        ("composed", "get_composed", None, 0, 1), ("returnValue", "get_returnValue", "set_returnValue", 0, 0),
        ("persisted", "get_persisted", None, 0, 1),
    )
    __v8_method__ = js_methods(("preventDefault", "fn_preventDefault", 0, 0), ("stopPropagation", "fn_stopPropagation", 0, 0), ("stopImmediatePropagation", "fn_stopImmediatePropagation", 0, 0), ("composedPath", "fn_composedPath", 0, 1))
    def __init__(self, event_type="", options=None, **_):
        options = options if isinstance(options, dict) else {}
        self.type, self.bubbles, self.cancelable = str(event_type), bool(options.get("bubbles", False)), bool(options.get("cancelable", False))
        self.target = self.currentTarget = None; self.eventPhase = 0; self.defaultPrevented = False
        self._stop = self._immediate = False; self._path = []; self._passive = False
        self._time_stamp = time.time() * 1000.0; self._trusted = bool(options.get("isTrusted", False))
        self._composed = bool(options.get("composed", False)); self._persisted = bool(options.get("persisted", False))
    def get_type(self): return self.type
    def get_target(self): return self.target or pyv8.Null
    def get_currentTarget(self): return self.currentTarget or pyv8.Null
    def get_bubbles(self): return self.bubbles
    def get_cancelable(self): return self.cancelable
    def get_defaultPrevented(self): return self.defaultPrevented
    def get_eventPhase(self): return self.eventPhase
    def get_cancelBubble(self): return self._stop
    def set_cancelBubble(self, value): self._stop = bool(value)
    def get_timeStamp(self): return self._time_stamp
    def get_isTrusted(self): return self._trusted
    def get_composed(self): return self._composed
    def get_persisted(self): return self._persisted
    def get_returnValue(self): return not self.defaultPrevented
    def set_returnValue(self, value):
        if not value and self.cancelable: self.defaultPrevented = True
    def fn_preventDefault(self):
        if self.cancelable and not self._passive: self.defaultPrevented = True
    def fn_stopPropagation(self): self._stop = True
    def fn_stopImmediatePropagation(self): self._stop = self._immediate = True
    def fn_composedPath(self): return list(self._path)


@interface("UIEvent", "Event", constructible=True, length=1, tag="UIEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class UIEvent(Event):
    pass


@interface("MouseEvent", "UIEvent", constructible=True, length=1, tag="MouseEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class MouseEvent(UIEvent):
    pass


@interface("KeyboardEvent", "UIEvent", constructible=True, length=1, tag="KeyboardEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class KeyboardEvent(UIEvent):
    pass


@interface("CustomEvent", "Event", constructible=True, length=1, tag="CustomEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class CustomEvent(Event):
    __v8_attribute__ = js_attributes(("detail", "get_detail", None, 0, 1))
    def __init__(self, event_type="", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.detail = options.get("detail", pyv8.Null)
    def get_detail(self): return self.detail


@interface("EventTarget", None, constructible=True)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class EventTarget:
    __v8_attribute__ = js_attributes(
        ("onload", "get_onload", "set_onload", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
        ("onreadystatechange", "get_onreadystatechange", "set_onreadystatechange", 0, 0),
        ("onpageshow", "get_onpageshow", "set_onpageshow", 0, 0),
        ("onpagehide", "get_onpagehide", "set_onpagehide", 0, 0),
        ("onunload", "get_onunload", "set_onunload", 0, 0),
        ("onbeforeunload", "get_onbeforeunload", "set_onbeforeunload", 0, 0),
    )
    __v8_method__ = js_methods(
        ("addEventListener", "fn_addEventListener", 2, 0),
        ("removeEventListener", "fn_removeEventListener", 2, 0),
        ("dispatchEvent", "fn_dispatchEvent", 1, 0),
    )

    def __init__(self, services=None, **_):
        self._services = services
        self._listeners = {}
        self._listener_order = 0
        self._handlers = {}

    def _get_handler(self, name): return self._handlers.get(name, pyv8.Null)
    def _set_handler(self, name, value): self._handlers[name] = value
    def get_onload(self): return self._get_handler("onload")
    def set_onload(self, value): self._set_handler("onload", value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value): self._set_handler("onerror", value)
    def get_onreadystatechange(self): return self._get_handler("onreadystatechange")
    def set_onreadystatechange(self, value): self._set_handler("onreadystatechange", value)
    def get_onpageshow(self): return self._get_handler("onpageshow")
    def set_onpageshow(self, value): self._set_handler("onpageshow", value)
    def get_onpagehide(self): return self._get_handler("onpagehide")
    def set_onpagehide(self, value): self._set_handler("onpagehide", value)
    def get_onunload(self): return self._get_handler("onunload")
    def set_onunload(self, value): self._set_handler("onunload", value)
    def get_onbeforeunload(self): return self._get_handler("onbeforeunload")
    def set_onbeforeunload(self, value): self._set_handler("onbeforeunload", value)

    def fn_addEventListener(self, event_type, listener, options=False):
        capture = bool(options.get("capture", False)) if isinstance(options, dict) else bool(options)
        once = bool(options.get("once", False)) if isinstance(options, dict) else False
        passive = bool(options.get("passive", False)) if isinstance(options, dict) else False
        signal = options.get("signal") if isinstance(options, dict) else None
        bucket = self._listeners.setdefault(str(event_type), [])
        if not any(item[0] is listener and item[1] == capture for item in bucket):
            self._listener_order += 1; bucket.append((listener, capture, once, passive, signal, self._listener_order))

    def fn_removeEventListener(self, event_type, listener, options=False):
        capture = bool(options.get("capture", False)) if isinstance(options, dict) else bool(options)
        bucket = self._listeners.get(str(event_type), [])
        self._listeners[str(event_type)] = [x for x in bucket if not (x[0] is listener and x[1] == capture)]

    def fn_dispatchEvent(self, event):
        if not isinstance(event, Event):
            raise V8TypeError("Failed to execute 'dispatchEvent' on 'EventTarget': parameter 1 is not of type 'Event'.")
        event.target = event.target or self
        path = []
        node = self
        while node is not None:
            path.append(node)
            node = getattr(node, "parentNode", None)
            if node is None and getattr(path[-1], "nodeType", None) == _DOCUMENT_NODE:
                node = getattr(getattr(getattr(path[-1], "_services", None), "document_services", None), "window", None)
        event._path = list(path)
        for target in reversed(path[1:]):
            event.eventPhase, event.currentTarget = 1, target
            target._invoke(event, capture=True)
            if event._stop:
                return not event.defaultPrevented
        event.eventPhase, event.currentTarget = 2, self
        self._invoke(event, capture=True)
        if not event._immediate: self._invoke(event, capture=False)
        if event.bubbles and not event._stop:
            for target in path[1:]:
                event.eventPhase, event.currentTarget = 3, target
                target._invoke(event, capture=False)
                if event._stop: break
        event.eventPhase, event.currentTarget = 0, None
        return not event.defaultPrevented

    def _invoke(self, event, capture):
        for listener, registered_capture, once, passive, signal, _order in list(self._listeners.get(event.type, ())):
            if registered_capture != capture:
                continue
            if signal is not None and getattr(signal, "aborted", False): continue
            event._passive = passive; _js_call(listener, self, event); event._passive = False
            if once: self._listeners[event.type] = [x for x in self._listeners.get(event.type, ()) if x[0] is not listener or x[1] != registered_capture]
            if event._immediate:
                return
        handler = self._handlers.get("on" + event.type)
        if not capture and handler not in (None, pyv8.Null, pyv8.Undefined):
            if event.type == "error" and self.__class__.__name__ == "Window":
                _js_call(handler, self, getattr(event, "message", ""), "", 0, 0, getattr(event, "error", pyv8.Null))
            else:
                result = _js_call(handler, self, event)
                if event.type == "beforeunload" and result not in (None, pyv8.Null, pyv8.Undefined, ""):
                    event.defaultPrevented = True

    def __getattr__(self, name):
        if name.startswith("on"):
            return self._handlers.get(name, pyv8.Null)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("on") and name != "one" and "_handlers" in self.__dict__:
            self._handlers[name] = value
            return
        object.__setattr__(self, name, value)
