"""Document lifecycle events and ready-state transitions for a Page."""

import pyv8

from ..foundation.errors import NotFoundError
from ..web.dom.events import Event

#: Event types the host builds as a richer class than ``Event``.  Types that are
#: missing here fall back to ``Event``; the interface itself is materialized on
#: demand so lazy events never inflate realm startup.
EVENT_CLASS_BY_TYPE = {
    # Mouse family
    "click": "MouseEvent", "dblclick": "MouseEvent", "auxclick": "MouseEvent",
    "contextmenu": "MouseEvent", "mousedown": "MouseEvent", "mouseup": "MouseEvent",
    "mousemove": "MouseEvent", "mouseover": "MouseEvent", "mouseout": "MouseEvent",
    "mouseenter": "MouseEvent", "mouseleave": "MouseEvent",
    # Focus, text input and keyboard
    "focus": "FocusEvent", "blur": "FocusEvent", "focusin": "FocusEvent", "focusout": "FocusEvent",
    "input": "InputEvent", "beforeinput": "InputEvent",
    "keydown": "KeyboardEvent", "keyup": "KeyboardEvent", "keypress": "KeyboardEvent",
    # Pointer, wheel and composition
    "pointerdown": "PointerEvent", "pointerup": "PointerEvent", "pointermove": "PointerEvent",
    "pointerover": "PointerEvent", "pointerout": "PointerEvent", "pointerenter": "PointerEvent",
    "pointerleave": "PointerEvent", "pointercancel": "PointerEvent",
    "gotpointercapture": "PointerEvent", "lostpointercapture": "PointerEvent",
    "wheel": "WheelEvent",
    "compositionstart": "CompositionEvent", "compositionupdate": "CompositionEvent",
    "compositionend": "CompositionEvent",
    # Navigation, messaging and page visibility
    "popstate": "PopStateEvent", "hashchange": "HashChangeEvent", "storage": "StorageEvent",
    "message": "MessageEvent", "messageerror": "MessageEvent",
    "unhandledrejection": "PromiseRejectionEvent", "rejectionhandled": "PromiseRejectionEvent",
    "pageshow": "PageTransitionEvent", "pagehide": "PageTransitionEvent",
    "beforeunload": "BeforeUnloadEvent",
    # Animation and transition
    "animationstart": "AnimationEvent", "animationend": "AnimationEvent",
    "animationiteration": "AnimationEvent", "animationcancel": "AnimationEvent",
    "transitionrun": "TransitionEvent", "transitionstart": "TransitionEvent",
    "transitionend": "TransitionEvent", "transitioncancel": "TransitionEvent",
    # Load progress
    "error": "ErrorEvent", "progress": "ProgressEvent",
    "loadstart": "ProgressEvent", "loadend": "ProgressEvent",
}

#: Host-dispatched types that bubble by default, mirroring Blink's defaults, so
#: ``page.dispatch_event("click", ...)`` reaches document-level delegates without
#: the caller spelling ``bubbles`` out.
BUBBLING_EVENT_TYPES = frozenset((
    "click", "dblclick", "auxclick", "contextmenu",
    "mousedown", "mouseup", "mousemove", "mouseover", "mouseout",
    "keydown", "keyup", "keypress",
    "input", "beforeinput", "change", "select", "invalid", "submit", "reset",
    "wheel", "pointerdown", "pointerup", "pointermove", "pointerover", "pointerout",
    "pointercancel", "gotpointercapture", "lostpointercapture",
    "touchstart", "touchend", "touchmove", "touchcancel",
    "compositionstart", "compositionupdate", "compositionend",
    "animationstart", "animationend", "animationiteration", "animationcancel",
    "transitionrun", "transitionstart", "transitionend", "transitioncancel",
    "visibilitychange",
))

#: Host-dispatched types that are cancelable by default.
CANCELABLE_EVENT_TYPES = frozenset((
    "click", "dblclick", "auxclick", "contextmenu", "mousedown", "mouseup",
    "keydown", "keypress", "beforeinput", "submit", "reset", "wheel",
    "pointerdown", "pointerup", "touchstart", "touchend", "touchmove",
    "beforeunload",
))


class DocumentLifecycle:
    """Own document lifecycle state; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.dom_content_loaded = False
        self.document_load_fired = False
        self.load_waiters = []

    # -- event dispatch ------------------------------------------------

    def materialize_event_class(self, event_class):
        """Resolve ``event_class`` (an interface name or a class) for this realm.

        The native side needs a committed class config before a Python-built
        event can become a JS object, so a named interface is materialized
        through this realm's binding session; the second call is a no-op.
        """
        name = event_class if isinstance(event_class, str) else getattr(
            getattr(event_class, "__idl_spec__", None), "name", None
        )
        if name is None:
            return event_class
        session = self.page.binding_session
        if name not in session.interface_names("Window"):
            raise TypeError(f"unknown event interface {name!r}")
        cls = session.materialize(name)
        if not issubclass(cls, Event):
            raise TypeError(f"{name!r} is not an Event interface")
        return cls

    def event_class(self, event_type, event_class=None):
        """The implementation class used to build one dispatched event.

        ``event_class`` names the interface to build; when omitted the event
        type picks a default from :data:`EVENT_CLASS_BY_TYPE`, falling back to
        ``Event``.
        """
        return self.materialize_event_class(
            event_class or EVENT_CLASS_BY_TYPE.get(str(event_type), "Event")
        )

    def dispatch_page_event(self, event_type, target, options=None, *, event_class=None,
                            propagates=True):
        """Dispatch one trusted event at *target* and return it.

        *propagates* is ``False`` for the enter/leave pair, whose propagation
        path is the target alone -- Chromium dispatches ``mouseenter`` and its
        relatives that way, so no ancestor capture listener sees them.
        """
        cls = Event if event_class is None else self.materialize_event_class(event_class)
        event = cls(event_type, options or {})
        event._trusted = True
        event._non_propagating = not propagates
        if event_type in ("pagehide", "pageshow"):
            event._persisted = bool((options or {}).get("persisted", False))
        target.fn_dispatchEvent(event)
        return event

    def dispatch_event(self, event_type, *, target="window", selector=None, event_class=None, init=None):
        """Build a trusted event of the type's default class and dispatch it.

        Returns ``True`` when the event was not cancelled.  ``target`` is
        ``"window"``, ``"document"`` or a DOM node; ``selector`` resolves the
        target from the current document instead.
        """
        node = self.event_target(target, selector)
        data = dict(init or {})
        data.setdefault("bubbles", str(event_type) in BUBBLING_EVENT_TYPES)
        data.setdefault("cancelable", str(event_type) in CANCELABLE_EVENT_TYPES)
        event = self.dispatch_page_event(
            event_type, node, data, event_class=self.event_class(event_type, event_class),
        )
        return not event.defaultPrevented

    def event_target(self, target="window", selector=None):
        """Resolve a host-side dispatch target to a live EventTarget."""
        page = self.page
        if selector is not None:
            node = page.document.fn_querySelector(str(selector))
            if node is pyv8.Null or node is None:
                raise NotFoundError(f"No element matches the selector {selector!r}")
            return node
        if target == "window" or target is page.window:
            return page.window
        if target == "document" or target is page.document:
            return page.document
        if isinstance(target, str):
            raise ValueError("target must be 'window', 'document' or a DOM node")
        if not callable(getattr(target, "fn_dispatchEvent", None)):
            raise TypeError("target must be an EventTarget")
        return target

    def dispatch_beforeunload(self):
        event = self.dispatch_page_event(
            "beforeunload", self.page.window,
            {"bubbles": False, "cancelable": True}, event_class="BeforeUnloadEvent",
        )
        return not event.defaultPrevented and not event.get_returnValue()

    def dispatch_page_show(self, persisted=False):
        return self.dispatch_page_event(
            "pageshow", self.page.window, {"bubbles": False, "persisted": bool(persisted)},
            event_class="PageTransitionEvent",
        )

    def dispatch_page_hide(self, persisted=False):
        return self.dispatch_page_event(
            "pagehide", self.page.window, {"bubbles": False, "persisted": bool(persisted)},
            event_class="PageTransitionEvent",
        )

    def dispatch_unload(self):
        return self.dispatch_page_event("unload", self.page.window, {"bubbles": False})

    def dispatch_progress_event(self, target, event_type, loaded=0, total=0, length_computable=False):
        """Fire one element ``loadstart``/``progress``/``loadend`` event."""
        return self.dispatch_page_event(
            event_type, target,
            {
                "bubbles": False,
                "lengthComputable": bool(length_computable),
                "loaded": int(loaded),
                "total": int(total),
            },
            event_class="ProgressEvent",
        )

    def dispatch_script_error(self, node, error, *, message=None, filename="", line=0,
                              column=0, value=None):
        """Fire ``error`` for *error*, or for the pieces V8 reported directly.

        An exception the embedder caught arrives as a Python object and carries
        its own text and, usually, the JavaScript value behind it.  One V8
        raised on its own -- inside a microtask job -- never comes back to
        Python as an exception, so the isolate reports the message, position and
        thrown value instead; both spellings build the same ``ErrorEvent``.
        """
        if node is not None:
            self.dispatch_page_event("error", node, {"bubbles": False})
        if message is None:
            # A browser hands page handlers the original JavaScript error (the
            # fifth ``onerror`` argument), so a wrapper from the binding layer is
            # replaced by the JS value it stands for when there is one.
            original = getattr(error, "original", None)
            message = str(error)
            value = error if original is None else original
        event = self.materialize_event_class("ErrorEvent")(
            "error", {
                "message": message,
                "filename": filename,
                "lineno": line,
                "colno": column,
                "error": value,
            },
        )
        event._trusted = True
        self.page.window.fn_dispatchEvent(event)
        return event

    def dispatch_rejection(self, event_type, promise, reason):
        """Fire ``unhandledrejection`` or ``rejectionhandled`` on the window.

        The environment can see a rejected promise but not the handlers attached
        to it, so the event is built from what V8 named.  Its revocation
        notification carries no value, which leaves ``reason`` null on a
        ``rejectionhandled``; the promise member still reaches it.
        """
        event = self.materialize_event_class(EVENT_CLASS_BY_TYPE[event_type])(
            event_type, {"promise": promise, "reason": reason},
        )
        event._trusted = True
        self.page.window.fn_dispatchEvent(event)
        return event

    # -- page visibility -----------------------------------------------

    def set_visibility(self, hidden):
        """Flip the document visibility state and fire the lifecycle events.

        Returns ``True`` when the state actually changed; a redundant call is a
        no-op, matching the browser's "only on transition" contract.
        """
        page = self.page
        hidden = bool(hidden)
        if bool(page.document.hidden) == hidden:
            return False
        page.document.hidden = hidden
        self.dispatch_page_event("visibilitychange", page.document, {"bubbles": True})
        self.dispatch_page_event("freeze" if hidden else "resume", page.document, {"bubbles": False})
        return True

    # -- ready-state transitions ---------------------------------------

    def set_ready_state(self, value):
        if self.page.document.ready_state == value:
            return
        self.page.document.ready_state = value
        self.dispatch_page_event("readystatechange", self.page.document, {"bubbles": False})

    def maybe_fire_dom_content_loaded(self):
        page = self.page
        if (
            page.closed
            or not page.navigation.parser_done
            or self.dom_content_loaded
            or page.scripts.dom_blocking_scripts
        ):
            return
        self.dom_content_loaded = True
        self.set_ready_state("interactive")
        self.dispatch_page_event("DOMContentLoaded", page.document, {"bubbles": True})
        self.maybe_fire_document_load()

    def maybe_fire_document_load(self):
        page = self.page
        if page.closed or not self.dom_content_loaded or page.resources.resources or self.document_load_fired:
            return
        self.document_load_fired = True
        self.set_ready_state("complete")
        # A window's load event is fired at the window with the document as its
        # target, which is what a page -- and a script watching for it -- reads.
        event = self.event_class("load")("load", {"bubbles": False})
        event._trusted = True
        event.target = page.document
        page.window.fn_dispatchEvent(event)
        self.dispatch_page_show(False)
        waiters, self.load_waiters = self.load_waiters, []
        for callback in waiters:
            callback(True)

    def when_loaded(self, callback):
        if self.document_load_fired:
            self.page.tasks.post(lambda: callback(True))
        else:
            self.load_waiters.append(callback)
