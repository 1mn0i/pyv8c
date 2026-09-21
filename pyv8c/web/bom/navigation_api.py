"""The Navigation API.

A page that has not moved yet has exactly one entry, nothing behind or ahead of
it, and no transition; ``navigate`` and ``reload`` drive the same navigation the
location does, and the entry list is rebuilt from the next document.
"""

from urllib.parse import urljoin

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, missing_error
from ..dom.events import EventTarget, _install_handler_slots

HANDLERS = ("oncurrententrychange", "onnavigate", "onnavigateerror", "onnavigatesuccess")


@interface('NavigationHistoryEntry', 'EventTarget')
class NavigationHistoryEntry(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="url", getter="get_url", side_effect=1),
            AttributeSpec(name="key", getter="get_key", side_effect=1),
            AttributeSpec(name="id", getter="get_id", side_effect=1),
            AttributeSpec(name="index", getter="get_index", side_effect=1),
            AttributeSpec(name="sameDocument", getter="get_sameDocument", side_effect=1),
            AttributeSpec(name="ondispose", getter="get_ondispose", setter="set_ondispose"),
        ),
        methods=(MethodSpec(name="getState", callback="fn_getState", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="NavigationHistoryEntry",
        has_constructor=1,
    )

    def __init__(self, url="", index=0, state=None, **_):
        super().__init__(**_)
        self._url, self._index, self._state = str(url), int(index), state
        self._key = str(index)

    def get_url(self): return self._url
    def get_key(self): return self._key
    def get_id(self): return self._key
    def get_index(self): return self._index
    def get_sameDocument(self): return True
    def get_ondispose(self): return self._get_handler("ondispose")
    def set_ondispose(self, value): self._set_handler("ondispose", value)

    def fn_getState(self, *_extra):
        return pyv8.Undefined if self._state is None else self._state

    def set_state(self, state):
        self._state = state


@interface('NavigationActivation', None)
class NavigationActivation:
    """What the last activation says, from the entry the page is on."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="entry", getter="get_entry", side_effect=1),
            AttributeSpec(name="from", getter="get_from", side_effect=1),
            AttributeSpec(name="navigationType", getter="get_navigationType", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="NavigationActivation",
        has_constructor=1,
    )

    def __init__(self, entry=None, **_):
        self._entry = entry

    def get_entry(self):
        return pyv8.Null if self._entry is None else self._entry

    def get_from(self): return pyv8.Null
    def get_navigationType(self): return "push"


@interface('Navigation', 'EventTarget')
class Navigation(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="currentEntry", getter="get_currentEntry", side_effect=1),
            AttributeSpec(name="entries", getter="get_entries", side_effect=1),
            AttributeSpec(name="canGoBack", getter="get_canGoBack", side_effect=1),
            AttributeSpec(name="canGoForward", getter="get_canGoForward", side_effect=1),
            AttributeSpec(name="transition", getter="get_transition", side_effect=1),
            AttributeSpec(name="activation", getter="get_activation", side_effect=1),
        ),
        methods=(
            MethodSpec(name="navigate", callback="fn_navigate", length=1),
            MethodSpec(name="reload", callback="fn_reload", length=0),
            MethodSpec(name="back", callback="fn_back", length=0),
            MethodSpec(name="forward", callback="fn_forward", length=0),
            MethodSpec(name="traverseTo", callback="fn_traverseTo", length=1),
            MethodSpec(name="updateCurrentEntry", callback="fn_updateCurrentEntry", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="Navigation",
        has_constructor=1,
    )

    def __init__(self, window=None, **_):
        super().__init__(**_)
        self._window = window
        self._entry = None
        self._activation = None

    def _current(self):
        if self._entry is None:
            self._entry = NavigationHistoryEntry(
                self._window.location.get_href(), 0,
                None if not hasattr(self, "_state") else self._state)
        return self._entry

    def get_currentEntry(self): return self._current()
    #: A document set from markup records no entry, and nothing keeps a history.
    def get_entries(self): return []
    def get_canGoBack(self): return False
    def get_canGoForward(self): return False
    def get_transition(self): return pyv8.Null

    def get_activation(self):
        if self._activation is None:
            self._activation = NavigationActivation(self._current())
        return self._activation

    def fn_navigate(self, url=MISSING, options=None, *_extra):
        """Ask for the navigation, and answer with the two promises it reports through.

        The request replaces this realm, so both promises carry the entry this
        realm still has rather than the one the new document will build.
        """
        if url is MISSING:
            raise missing_error('Navigation', 'navigate', 1, 0, constructor=False)
        target = urljoin(self._window.location.get_href(), dom_string(url))
        return self._navigate_to(target)

    def fn_reload(self, options=None, *_extra):
        return self._navigate_to(self._window.location.get_href())

    def _navigate_to(self, target):
        services = self._window._services
        entry = self._current()
        committed, resolve_committed, _reject = services.realm_services.make_promise()
        finished, resolve_finished, _reject_again = services.realm_services.make_promise()
        services.document_services.request_navigation(target)
        resolve_committed(entry)
        resolve_finished(entry)
        return {"committed": committed, "finished": finished}

    #: There is no history to walk in either direction.
    def fn_back(self, *_extra): return None
    def fn_forward(self, *_extra): return None
    def fn_traverseTo(self, key=MISSING, *_extra): return None

    def fn_updateCurrentEntry(self, options=MISSING, *_extra):
        if options is MISSING:
            raise missing_error('Navigation', 'updateCurrentEntry', 1, 0, constructor=False)
        settings = dictionary(options)
        self._state = settings.get("state")
        self._current().set_state(self._state)


Navigation.__v8_idl_spec__ = Navigation.__v8_idl_spec__.replace(
    attributes=Navigation.__v8_idl_spec__.attributes + _install_handler_slots(Navigation, HANDLERS)
)


__all__ = ["Navigation", "NavigationHistoryEntry"]
