"""``Location`` and ``History``: the realm's view of navigation.

Both drive ``document_services`` and keep the entry table in step with the
committed URL, and each calls the other: assigning ``location`` pushes or
replaces a history entry, while ``history.back()`` assigns an entry's URL back
into ``location``.  A same-document (fragment) change is committed without
rebuilding the document, so it fires ``hashchange`` and no load events.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, is_nullish, missing_error, usv_string
from ...foundation.errors import SecurityError
from ..dom.events import HashChangeEvent, PopStateEvent
from .utils import document_key

__all__ = ["Location", "History"]


#: Location components the implementation actually assigns; the baseline marks
#: protocol/host/hostname/port writable too, and those keep their stub setter.
_LOCATION_SETTERS = {
    "href": "set_href",
    "pathname": "set_pathname",
    "search": "set_search",
    "hash": "set_hash",
}


@interface('Location', None)
class Location:
    def __init__(self, href="about:blank", services=None, **_): self.href, self._services = href, services
    def _require_access(self):
        if self._services is not None:
            target = self._services.document_services.window
            if target is not None:
                target._require_same_origin()
    def _parts(self): self._require_access(); return urlsplit(self.href)
    def get_href(self): self._require_access(); return self.href
    def set_href(self, value):
        value = dom_string(value)
        target = urljoin(self.href, str(value))
        if self._services is None:
            self.href = target
            return
        if self._is_same_document(target):
            self._navigate_within_document(target, replace=False)
            return
        self._services.document_services.request_navigation(target)
    def _is_same_document(self, target):
        return target != self.href and document_key(target) == document_key(self.href)
    def _navigate_within_document(self, target, replace):
        """Commit a fragment change without rebuilding the document.

        Only the fragment may differ, so no ``beforeunload``/``pagehide``/
        ``unload`` runs and resources keep their state; ``hashchange`` fires
        because a new entry is created for the fragment.
        """
        window = self._services.document_services.window
        # Materialize the History (which starts at the *current* URL) before the
        # fragment change so entry 0 still describes the document we are on.
        history = getattr(window, "history", None)
        previous, self.href = str(self.href), target
        if history is not None:
            if replace:
                history.replace_same_document(target)
            else:
                history.push_same_document(target)
        if urlsplit(previous).fragment != urlsplit(target).fragment:
            self._services.realm_services.materialize_interface("HashChangeEvent")
            event = HashChangeEvent("hashchange", {"oldURL": previous, "newURL": target})
            event._trusted = True
            window.fn_dispatchEvent(event)
    def _set_component(self, **changes):
        self._require_access()
        self.set_href(urlunsplit(urlsplit(self.href)._replace(**changes)))
    def set_pathname(self, value): self._set_component(path=dom_string(value))
    def set_search(self, value): self._set_component(query=dom_string(value).lstrip("?"))
    def set_hash(self, value): self._set_component(fragment=dom_string(value).lstrip("#"))
    def fn_assign(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Location', 'assign', 1, 0)
        self.set_href(usv_string(value))
    def fn_replace(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Location', 'replace', 1, 0)
        target = urljoin(self.href, usv_string(value))
        if self._services is None:
            self.href = target
            return
        if self._is_same_document(target):
            self._navigate_within_document(target, replace=True)
            return
        self._services.document_services.request_navigation(target)
    def fn_reload(self, *_extra):
        if self._services is not None:
            self._services.document_services.request_navigation(self.href)
    def get_origin(self):
        p = self._parts(); return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else "null"
    def get_protocol(self): return self._parts().scheme + ":"
    def get_host(self): return self._parts().netloc
    def get_hostname(self): return self._parts().hostname or ""
    def get_port(self): return str(self._parts().port or "")
    def get_pathname(self): return self._parts().path or "/"
    def get_search(self): return ("?" + self._parts().query) if self._parts().query else ""
    def get_hash(self): return ("#" + self._parts().fragment) if self._parts().fragment else ""
    __v8_idl_spec__ = IdlSpec(
        attributes=tuple(

                AttributeSpec(name=n, getter="get_" + n, setter=_LOCATION_SETTERS.get(n))
                for n in (
                    "href",
                    "origin",
                    "protocol",
                    "host",
                    "hostname",
                    "port",
                    "pathname",
                    "search",
                    "hash",
                )

        ),
        methods=(
            MethodSpec(name="assign", callback="fn_assign", length=1),
            MethodSpec(name="replace", callback="fn_replace", length=1),
            MethodSpec(name="reload", callback="fn_reload"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )


@interface('History', None)
class History:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="length", getter="get_length", side_effect=1),
            AttributeSpec(name="state", getter="get_state", side_effect=1),
        ),
        methods=(
            MethodSpec(name="pushState", callback="fn_pushState", length=3),
            MethodSpec(name="replaceState", callback="fn_replaceState", length=3),
            MethodSpec(name="back", callback="fn_back"),
            MethodSpec(name="forward", callback="fn_forward"),
            MethodSpec(name="go", callback="fn_go", length=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, services=None, initial_url=None, **_):
        self._services = services
        self._entries = [(initial_url if initial_url is not None else getattr(getattr(services, "realm_services", None), "url", "about:blank"), None)]
        self._index = 0
    def get_length(self): return len(self._entries)
    def get_state(self):
        state = self._entries[self._index][1]
        return pyv8.Null if state is None else state
    def _url(self, value): return urljoin(self._services.realm_services.url, str(value))
    def _validate_target(self, url):
        from ...foundation.urls import same_origin
        target = self._url(url) if url is not None else self._services.realm_services.url
        if not same_origin(self._services.realm_services.url, target): raise SecurityError("A history state URL must be same-origin")
        return target
    def fn_pushState(self, state=MISSING, unused=MISSING, url=None, *_extra):
        if state is MISSING:
            raise missing_error('History', 'pushState', 2, 0, constructor=False)
        if unused is MISSING:
            raise missing_error('History', 'pushState', 2, 1, constructor=False)
        unused = dom_string(unused)
        if url is pyv8.Undefined:
            url = None
        url = None if is_nullish(url) else usv_string(url)
        target = self._validate_target(url)
        self._entries = self._entries[:self._index + 1] + [(target, state)]; self._index += 1; self._services.document_services.window.location.href = target
    def fn_replaceState(self, state=MISSING, unused=MISSING, url=None, *_extra):
        if state is MISSING:
            raise missing_error('History', 'replaceState', 2, 0, constructor=False)
        if unused is MISSING:
            raise missing_error('History', 'replaceState', 2, 1, constructor=False)
        unused = dom_string(unused)
        if url is pyv8.Undefined:
            url = None
        url = None if is_nullish(url) else usv_string(url)
        target = self._validate_target(url)
        self._entries[self._index] = (target, state); self._services.document_services.window.location.href = target
    def push_same_document(self, url, state=None):
        """Record a same-document entry without resolving the URL again."""
        self._entries = self._entries[:self._index + 1] + [(str(url), state)]
        self._index += 1
    def replace_same_document(self, url, state=None):
        self._entries[self._index] = (str(url), state)
    def fn_back(self, *_extra):
        return self.fn_go(-1)
    def fn_forward(self, *_extra):
        return self.fn_go(1)
    def fn_go(self, delta=0, *_extra):
        if delta is pyv8.Undefined:
            delta = 0
        if delta is not pyv8.Undefined:
            delta = integer(delta, signed=True)
        target = max(0, min(len(self._entries) - 1, self._index + int(delta)))
        if target == self._index: return
        window = self._services.document_services.window
        previous = str(window.location.href)
        self._index = target
        entry = self._entries[target]
        window.location.href = entry[0]
        self._services.realm_services.materialize_interface("PopStateEvent")
        event = PopStateEvent("popstate", {"state": entry[1]})
        event._trusted = True
        window.fn_dispatchEvent(event)
        # Crossing entries whose fragments differ is also a fragment navigation,
        # so ``hashchange`` follows ``popstate`` for that step.
        if urlsplit(previous).fragment != urlsplit(str(entry[0])).fragment:
            self._services.realm_services.materialize_interface("HashChangeEvent")
            change = HashChangeEvent("hashchange", {"oldURL": previous, "newURL": str(entry[0])})
            change._trusted = True
            window.fn_dispatchEvent(change)
