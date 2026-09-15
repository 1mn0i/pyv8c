from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    dom_string,
    integer,
    interface_value,
    is_nullish,
    missing_error,
    sequence,
    usv_string,
)
from ...foundation.environment import BrowserEnvironment
from ...foundation.errors import InvalidStateError, SecurityError, V8TypeError, V8URIError
from ...foundation.urls import SPECIAL_SCHEMES
from ..dom.events import EventTarget, HashChangeEvent, PopStateEvent
from ..fetch.blob import Blob

__all__ = ["Location", "Screen", "Storage", "URLSearchParams", "URL", "History"]


def _document_key(url):
    """The URL parts a same-document (fragment) navigation cannot change."""
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc, parts.path, parts.query)


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
        return target != self.href and _document_key(target) == _document_key(self.href)
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
            (
                AttributeSpec(
                    name=n,
                    getter="get_" + n,
                    setter=_LOCATION_SETTERS.get(n),
                )
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


@interface('Screen', 'EventTarget')
class Screen(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=tuple(
            (
                AttributeSpec(name=n, getter="get_" + n, side_effect=1)
                for n in (
                    "width",
                    "height",
                    "availWidth",
                    "availHeight",
                    "colorDepth",
                    "pixelDepth",
                )
            )
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, environment=None, **_):
        super().__init__(**_)
        config = BrowserEnvironment.from_value(environment).screen
        self.width, self.height = config.width, config.height
        self.availWidth, self.availHeight = config.avail_width, config.avail_height
        self.colorDepth, self.pixelDepth = config.color_depth, config.pixel_depth
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_availWidth(self): return self.availWidth
    def get_availHeight(self): return self.availHeight
    def get_colorDepth(self): return self.colorDepth
    def get_pixelDepth(self): return self.pixelDepth


@interface('Storage', None)
class Storage:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="getItem", callback="fn_getItem", length=1, side_effect=1),
            MethodSpec(name="setItem", callback="fn_setItem", length=2),
            MethodSpec(name="removeItem", callback="fn_removeItem", length=1),
            MethodSpec(name="clear", callback="fn_clear"),
            MethodSpec(name="key", callback="fn_key", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, **_): self._data = {}
    def get_length(self): return len(self._data)
    def fn_getItem(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'getItem', 1, 0, constructor=False)
        key = dom_string(key)
        return self._data.get(str(key), pyv8.Null)
    def fn_setItem(self, key=MISSING, value=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'setItem', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('Storage', 'setItem', 2, 1, constructor=False)
        key = dom_string(key)
        value = dom_string(value)
        self._data[str(key)] = str(value)
    def fn_removeItem(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('Storage', 'removeItem', 1, 0, constructor=False)
        key = dom_string(key)
        self._data.pop(str(key), None)
    def fn_clear(self, *_extra):
        self._data.clear()
    def fn_key(self, index=MISSING, *_extra):
        if index is MISSING:
            raise missing_error('Storage', 'key', 1, 0, constructor=False)
        index = integer(index)
        return list(self._data)[index] if index < len(self._data) else pyv8.Null


@interface('URLSearchParams', None)
class URLSearchParams:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="append", callback="fn_append", length=2),
            MethodSpec(name="delete", callback="fn_delete", length=1),
            MethodSpec(name="get", callback="fn_get", length=1, side_effect=1),
            MethodSpec(name="getAll", callback="fn_getAll", length=1, side_effect=1),
            MethodSpec(name="has", callback="fn_has", length=1, side_effect=1),
            MethodSpec(name="set", callback="fn_set", length=2),
            MethodSpec(name="sort", callback="fn_sort"),
            MethodSpec(name="toString", callback="fn_toString", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, init='', *_extra, _url=None, **_):
        if init is pyv8.Undefined:
            init = ''
        from urllib.parse import parse_qsl
        self._url = _url
        iterable = isinstance(init, (list, tuple))
        iterator = MISSING
        if isinstance(init, pyv8.JSObject):
            iterator = init._iterator_method()
            iterable = not is_nullish(iterator)
        if iterable:
            def convert_pair(entry):
                pair = sequence(entry, usv_string)
                if len(pair) != 2:
                    raise V8TypeError("Failed to construct 'URLSearchParams': Sequence initializer must only contain pair elements.")
                return tuple(pair)
            self._items = sequence(init, convert_pair, iterator_method=iterator)
        elif isinstance(init, (dict, pyv8.JSObject)):
            self._items = [(usv_string(key), usv_string(value)) for key, value in dictionary(init).items()]
        else:
            self._items = list(parse_qsl(usv_string(init).lstrip("?"), keep_blank_values=True))

    def _refresh(self):
        if self._url is not None:
            from urllib.parse import parse_qsl
            self._items = list(parse_qsl(urlsplit(self._url.href).query, keep_blank_values=True))

    def _commit(self):
        if self._url is not None:
            from urllib.parse import urlencode
            self._url._set_search_from_params(urlencode(self._items))

    def fn_append(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); self._items.append((str(name), str(value))); self._commit()

    def fn_delete(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'delete', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); self._items = [(k, v) for k, v in self._items if k != name]; self._commit()

    def fn_get(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'get', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name)
        for key, value in self._items:
            if key == name: return value
        return pyv8.Null

    def fn_getAll(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'getAll', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); return [value for key, value in self._items if key == name]

    def fn_has(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'has', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); return any(key == name for key, _ in self._items)

    def fn_set(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); name, value = str(name), str(value)
        replaced = False; result = []
        for key, old_value in self._items:
            if key == name:
                if not replaced: result.append((name, value)); replaced = True
            else: result.append((key, old_value))
        if not replaced: result.append((name, value))
        self._items = result; self._commit()

    def fn_sort(self, *_extra):
        self._refresh(); self._items.sort(key=lambda item: item[0]); self._commit()

    def fn_toString(self, *_extra):
        from urllib.parse import urlencode
        self._refresh()
        return urlencode(self._items)


@interface('URL', None)
class URL:
    def __init__(self, url=MISSING, base=pyv8.Undefined, *_extra, _realm=None, **_):
        if url is MISSING:
            raise missing_error('URL', 'constructor', 1, 0, constructor=True)
        url = usv_string(url)
        base = None if base is pyv8.Undefined else usv_string(base)
        self._realm = _realm
        try:
            if base is not None:
                base = self._resolve(base)
            self.href = self._resolve(url, base)
        except V8TypeError as exc:
            raise V8TypeError("Failed to construct 'URL': Invalid URL") from exc
        self._search_params = None

    @staticmethod
    def _resolve(url, base=None):
        raw = str(url)
        if not raw or any(ch.isspace() for ch in raw):
            raise V8TypeError("Invalid URL")
        value = raw if base is None else urljoin(str(base), raw)
        try:
            parts = urlsplit(value)
            if not parts.scheme or (parts.scheme in SPECIAL_SCHEMES and not parts.netloc):
                raise V8URIError("Invalid URL")
            parts.port
        except (ValueError, V8URIError) as exc:
            raise V8TypeError("Invalid URL") from exc
        return urlunsplit(parts)

    def _parts(self): return urlsplit(self.href)
    def _replace(self, *, scheme=None, netloc=None, path=None, query=None, fragment=None):
        parts = self._parts()
        self.href = urlunsplit((scheme if scheme is not None else parts.scheme,
                                netloc if netloc is not None else parts.netloc,
                                path if path is not None else parts.path,
                                query if query is not None else parts.query,
                                fragment if fragment is not None else parts.fragment))

    def _set_search_from_params(self, query): self._replace(query=str(query))
    def get_href(self): return self.href
    def set_href(self, value):
        value = dom_string(value)
        self.href = self._resolve(value, self.href)
    def get_origin(self):
        parts = self._parts()
        if parts.scheme not in SPECIAL_SCHEMES or not parts.netloc:
            return "null"
        host = parts.hostname or ""
        if parts.port is not None:
            host += ":" + str(parts.port)
        return f"{parts.scheme}://{host}"
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ""
    def set_protocol(self, value):
        value = dom_string(value)
        self._replace(scheme=str(value).rstrip(':'))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        value = dom_string(value)
        self._set_credentials(username=str(value), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        value = dom_string(value)
        self._set_credentials(username=self.get_username(), password=str(value))
    def _set_credentials(self, *, username, password):
        parts = self._parts(); host = parts.hostname or ""
        if parts.port is not None: host += ":" + str(parts.port)
        credentials = (username + (":" + password if password else "") + "@") if username or password else ""
        self._replace(netloc=credentials + host)
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value):
        value = dom_string(value)
        self._replace(netloc=str(value))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._replace(netloc=credentials + str(value) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        self._replace(netloc=credentials + (parts.hostname or "") + ((":" + str(value)) if str(value) else ""))
    def get_pathname(self):
        path = self._parts().path
        return path or ("/" if self._parts().netloc else "")
    def set_pathname(self, value):
        value = dom_string(value)
        self._replace(path=str(value) or '/')
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value):
        value = dom_string(value)
        self._replace(query=str(value).lstrip('?'))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value):
        value = dom_string(value)
        self._replace(fragment=str(value).lstrip('#'))
    def get_searchParams(self):
        if self._search_params is None:
            self._search_params = URLSearchParams(self.get_search(), _url=self)
        return self._search_params
    def fn_toString(self, *_extra):
        return self.href
    def fn_toJSON(self, *_extra):
        return self.href

    @staticmethod
    def fn_canParse(url=MISSING, base=pyv8.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'canParse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not pyv8.Undefined:
            base = dom_string(base)
        try: URL(url, base)
        except (V8URIError, TypeError, ValueError): return False
        return True

    @staticmethod
    def fn_parse(url=MISSING, base=pyv8.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'parse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not pyv8.Undefined:
            base = dom_string(base)
        try: return URL(url, base)
        except (V8URIError, TypeError, ValueError): return pyv8.Null

    @staticmethod
    def fn_createObjectURL(_realm, blob=MISSING, *_extra):
        if blob is MISSING:
            raise missing_error('URL', 'createObjectURL', 1, 0, constructor=False)
        blob = interface_value(blob, 'Blob', 'URL', 'createObjectURL', 1, nullable=False)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        if not isinstance(blob, Blob): raise V8TypeError("The object is not a Blob")
        return services.context_services.create_blob_url(blob, realm=services.realm_services)

    @staticmethod
    def fn_revokeObjectURL(_realm, url=MISSING, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'revokeObjectURL', 1, 0, constructor=False)
        url = dom_string(url)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        services.context_services.revoke_blob_url(str(url))

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="href", getter="get_href", setter="set_href"),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(
                name="protocol", getter="get_protocol", setter="set_protocol"
            ),
            AttributeSpec(
                name="username", getter="get_username", setter="set_username"
            ),
            AttributeSpec(
                name="password", getter="get_password", setter="set_password"
            ),
            AttributeSpec(name="host", getter="get_host", setter="set_host"),
            AttributeSpec(
                name="hostname", getter="get_hostname", setter="set_hostname"
            ),
            AttributeSpec(name="port", getter="get_port", setter="set_port"),
            AttributeSpec(
                name="pathname", getter="get_pathname", setter="set_pathname"
            ),
            AttributeSpec(name="search", getter="get_search", setter="set_search"),
            AttributeSpec(
                name="searchParams", getter="get_searchParams", side_effect=1
            ),
            AttributeSpec(name="hash", getter="get_hash", setter="set_hash"),
        ),
        methods=(
            MethodSpec(name="toString", callback="fn_toString", side_effect=1),
            MethodSpec(name="toJSON", callback="fn_toJSON", side_effect=1),
        )
        + (
            MethodSpec(
                name="canParse",
                callback="fn_canParse",
                length=1,
                side_effect=1,
                location=FlagLocation.kInterface,
                receiver_check=FlagReceiverCheck.kDoNotCheck,
                cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
            ),
            MethodSpec(
                name="parse",
                callback="fn_parse",
                length=1,
                side_effect=1,
                location=FlagLocation.kInterface,
                receiver_check=FlagReceiverCheck.kDoNotCheck,
                cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
            ),
            MethodSpec(
                name="createObjectURL",
                callback="fn_createObjectURL",
                length=1,
                location=FlagLocation.kInterface,
                receiver_check=FlagReceiverCheck.kDoNotCheck,
                cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
            ),
            MethodSpec(
                name="revokeObjectURL",
                callback="fn_revokeObjectURL",
                length=1,
                location=FlagLocation.kInterface,
                receiver_check=FlagReceiverCheck.kDoNotCheck,
                cross_origin_check=FlagCrossOriginCheck.kDoNotCheck,
            ),
        ),
        constructor_behavior=1,
        length=1,
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
    def get_state(self): return self._entries[self._index][1]
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
