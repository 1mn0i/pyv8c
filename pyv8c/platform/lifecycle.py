"""Document lifecycle events and ready-state transitions for a Page."""

from ..web.dom.model import Event
from ..web.fetch.events import ErrorEvent


class DocumentLifecycle:
    """Own document lifecycle state; runs on the owning Page thread."""

    def __init__(self, page):
        self.page = page
        self.dom_content_loaded = False
        self.document_load_fired = False
        self.load_waiters = []

    # -- event dispatch ------------------------------------------------

    def dispatch_page_event(self, event_type, target, options=None):
        event = Event(event_type, options or {})
        event._trusted = True
        if event_type in ("pagehide", "pageshow"):
            event._persisted = bool((options or {}).get("persisted", False))
        target.fn_dispatchEvent(event)
        return event

    def dispatch_beforeunload(self):
        event = self.dispatch_page_event(
            "beforeunload", self.page.window, {"bubbles": False, "cancelable": True}
        )
        return not event.defaultPrevented and event.get_returnValue()

    def dispatch_script_error(self, node, error):
        if node is not None:
            self.dispatch_page_event("error", node, {"bubbles": False})
        event = ErrorEvent("error", {"message": str(error), "error": error})
        event._trusted = True
        self.page.window.fn_dispatchEvent(event)

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
        self.dispatch_page_event("load", page.window, {"bubbles": False})
        self.dispatch_page_event("pageshow", page.window, {"bubbles": False, "persisted": False})
        waiters, self.load_waiters = self.load_waiters, []
        for callback in waiters:
            callback(True)

    def when_loaded(self, callback):
        if self.document_load_fired:
            self.page.tasks.post(lambda: callback(True))
        else:
            self.load_waiters.append(callback)
