"""DOM event model: events and the target dispatch pipeline."""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

import time

import pyv8



from ...engine.registry import interface
from ...foundation.errors import V8TypeError


_DOCUMENT_NODE = 9  # DOM standard nodeType of Document; hardcoded to avoid a model import cycle.


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface('Event', None)
class Event:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="target", getter="get_target", side_effect=1),
            AttributeSpec(
                name="currentTarget", getter="get_currentTarget", side_effect=1
            ),
            AttributeSpec(name="bubbles", getter="get_bubbles", side_effect=1),
            AttributeSpec(name="cancelable", getter="get_cancelable", side_effect=1),
            AttributeSpec(
                name="defaultPrevented", getter="get_defaultPrevented", side_effect=1
            ),
            AttributeSpec(name="eventPhase", getter="get_eventPhase", side_effect=1),
            AttributeSpec(
                name="cancelBubble",
                getter="get_cancelBubble",
                setter="set_cancelBubble",
            ),
            AttributeSpec(name="timeStamp", getter="get_timeStamp", side_effect=1),
            AttributeSpec(name="isTrusted", getter="get_isTrusted", side_effect=1),
            AttributeSpec(name="composed", getter="get_composed", side_effect=1),
            AttributeSpec(
                name="returnValue", getter="get_returnValue", setter="set_returnValue"
            ),
            AttributeSpec(name="persisted", getter="get_persisted", side_effect=1),
        ),
        methods=(
            MethodSpec(name="preventDefault", callback="fn_preventDefault"),
            MethodSpec(name="stopPropagation", callback="fn_stopPropagation"),
            MethodSpec(
                name="stopImmediatePropagation", callback="fn_stopImmediatePropagation"
            ),
            MethodSpec(name="composedPath", callback="fn_composedPath", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="Event",
        has_constructor=1,
    )
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


@interface('UIEvent', 'Event')
class UIEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=1, length=1, to_string_tag="UIEvent", has_constructor=1
    )
    pass


@interface('MouseEvent', 'UIEvent')
class MouseEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=1, length=1, to_string_tag="MouseEvent", has_constructor=1
    )
    pass


@interface('KeyboardEvent', 'UIEvent')
class KeyboardEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=1,
        length=1,
        to_string_tag="KeyboardEvent",
        has_constructor=1,
    )
    pass


@interface('CustomEvent', 'Event')
class CustomEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="detail", getter="get_detail", side_effect=1),),
        constructor_behavior=1,
        length=1,
        to_string_tag="CustomEvent",
        has_constructor=1,
    )
    def __init__(self, event_type="", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.detail = options.get("detail", pyv8.Null)
    def get_detail(self): return self.detail


@interface('EventTarget', None)
class EventTarget:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onload", getter="get_onload", setter="set_onload"),
            AttributeSpec(name="onerror", getter="get_onerror", setter="set_onerror"),
            AttributeSpec(
                name="onreadystatechange",
                getter="get_onreadystatechange",
                setter="set_onreadystatechange",
            ),
            AttributeSpec(
                name="onpageshow", getter="get_onpageshow", setter="set_onpageshow"
            ),
            AttributeSpec(
                name="onpagehide", getter="get_onpagehide", setter="set_onpagehide"
            ),
            AttributeSpec(
                name="onunload", getter="get_onunload", setter="set_onunload"
            ),
            AttributeSpec(
                name="onbeforeunload",
                getter="get_onbeforeunload",
                setter="set_onbeforeunload",
            ),
        ),
        methods=(
            MethodSpec(
                name="addEventListener", callback="fn_addEventListener", length=2
            ),
            MethodSpec(
                name="removeEventListener", callback="fn_removeEventListener", length=2
            ),
            MethodSpec(name="dispatchEvent", callback="fn_dispatchEvent", length=1),
        ),
        constructor_behavior=1,
        has_constructor=1,
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
        # EventHandler slots may hold non-callable objects; Blink invokes
        # only their effective function, not the object itself.
        if not capture and callable(handler):
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
