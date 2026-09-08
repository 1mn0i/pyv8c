from pyv8 import IdlSpec, AttributeSpec, MethodSpec
from pyv8.flag import FlagLocation, FlagReceiverCheck, FlagCrossOriginCheck
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8



from ..dom.model import Event, EventTarget
from ...engine.registry import interface
from ..fetch.blob import Blob
from ...foundation.errors import SecurityError, V8URIError, V8TypeError, InvalidStateError
from ...foundation.environment import BrowserEnvironment
from ...foundation.urls import SPECIAL_SCHEMES


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
        target = urljoin(self.href, str(value))
        if self._services is not None:
            self._services.document_services.request_navigation(target)
        else:
            self.href = target
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
                    setter="set_href" if n == "href" else None,
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
    def fn_getItem(self, key): return self._data.get(str(key), None)
    def fn_setItem(self, key, value): self._data[str(key)] = str(value)
    def fn_removeItem(self, key): self._data.pop(str(key), None)
    def fn_clear(self): self._data.clear()
    def fn_key(self, index):
        try: return list(self._data)[int(index)]
        except (IndexError, ValueError): return None


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

    def __init__(self, init="", _url=None, **_):
        from urllib.parse import parse_qsl
        self._url = _url
        self._items = list(parse_qsl(str(init).lstrip("?"), keep_blank_values=True)) if not isinstance(init, dict) else list(init.items())

    def _refresh(self):
        if self._url is not None:
            from urllib.parse import parse_qsl
            self._items = list(parse_qsl(urlsplit(self._url.href).query, keep_blank_values=True))

    def _commit(self):
        if self._url is not None:
            from urllib.parse import urlencode
            self._url._set_search_from_params(urlencode(self._items))

    def fn_append(self, name, value):
        self._refresh(); self._items.append((str(name), str(value))); self._commit()

    def fn_delete(self, name):
        self._refresh(); name = str(name); self._items = [(k, v) for k, v in self._items if k != name]; self._commit()

    def fn_get(self, name):
        self._refresh(); name = str(name)
        for key, value in self._items:
            if key == name: return value
        return pyv8.Null

    def fn_getAll(self, name):
        self._refresh(); name = str(name); return [value for key, value in self._items if key == name]

    def fn_has(self, name):
        self._refresh(); name = str(name); return any(key == name for key, _ in self._items)

    def fn_set(self, name, value):
        self._refresh(); name, value = str(name), str(value)
        replaced = False; result = []
        for key, old_value in self._items:
            if key == name:
                if not replaced: result.append((name, value)); replaced = True
            else: result.append((key, old_value))
        if not replaced: result.append((name, value))
        self._items = result; self._commit()

    def fn_sort(self):
        self._refresh(); self._items.sort(key=lambda item: item[0]); self._commit()

    def fn_toString(self):
        from urllib.parse import urlencode
        self._refresh()
        return urlencode(self._items)


@interface('URL', None)
class URL:
    def __init__(self, url="", base=None, _realm=None, **_):
        self._realm = _realm
        self.href = self._resolve(url, base)
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
    def set_href(self, value): self.href = self._resolve(value, self.href)
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
    def set_protocol(self, value): self._replace(scheme=str(value).rstrip(":"))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value): self._set_credentials(username=str(value), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value): self._set_credentials(username=self.get_username(), password=str(value))
    def _set_credentials(self, *, username, password):
        parts = self._parts(); host = parts.hostname or ""
        if parts.port is not None: host += ":" + str(parts.port)
        credentials = (username + (":" + password if password else "") + "@") if username or password else ""
        self._replace(netloc=credentials + host)
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value): self._replace(netloc=str(value))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._replace(netloc=credentials + str(value) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        self._replace(netloc=credentials + (parts.hostname or "") + ((":" + str(value)) if str(value) else ""))
    def get_pathname(self):
        path = self._parts().path
        return path or ("/" if self._parts().netloc else "")
    def set_pathname(self, value): self._replace(path=str(value) or "/")
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value): self._replace(query=str(value).lstrip("?"))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value): self._replace(fragment=str(value).lstrip("#"))
    def get_searchParams(self):
        if self._search_params is None:
            self._search_params = URLSearchParams(self.get_search(), _url=self)
        return self._search_params
    def fn_toString(self): return self.href
    def fn_toJSON(self): return self.href

    @staticmethod
    def fn_canParse(url, base=None):
        try: URL(url, base)
        except (V8URIError, TypeError, ValueError): return False
        return True

    @staticmethod
    def fn_parse(url, base=None):
        try: return URL(url, base)
        except (V8URIError, TypeError, ValueError): return pyv8.Null

    @staticmethod
    def fn_createObjectURL(_realm, blob):
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        if not isinstance(blob, Blob): raise V8TypeError("The object is not a Blob")
        return services.context_services.create_blob_url(blob, realm=services.realm_services)

    @staticmethod
    def fn_revokeObjectURL(_realm, url):
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
    def __init__(self, services=None, **_): self._services, self._entries, self._index = services, [(getattr(getattr(services, "realm_services", None), "url", "about:blank"), None)], 0
    def get_length(self): return len(self._entries)
    def get_state(self): return self._entries[self._index][1]
    def _url(self, value): return urljoin(self._services.realm_services.url, str(value))
    def _validate_target(self, url):
        from ...foundation.urls import same_origin
        target = self._url(url) if url is not None else self._services.realm_services.url
        if not same_origin(self._services.realm_services.url, target): raise SecurityError("A history state URL must be same-origin")
        return target
    def fn_pushState(self, state, unused="", url=None):
        target = self._validate_target(url)
        self._entries = self._entries[:self._index + 1] + [(target, state)]; self._index += 1; self._services.document_services.window.location.href = target
    def fn_replaceState(self, state, unused="", url=None):
        target = self._validate_target(url)
        self._entries[self._index] = (target, state); self._services.document_services.window.location.href = target
    def fn_back(self): return self.fn_go(-1)
    def fn_forward(self): return self.fn_go(1)
    def fn_go(self, delta=0):
        target = max(0, min(len(self._entries) - 1, self._index + int(delta)))
        if target == self._index: return
        self._index = target; self._services.document_services.window.location.href = self._entries[target][0]; self._services.document_services.window.fn_dispatchEvent(Event("popstate", {"bubbles": False}))


# Compatibility re-exports; implementations live in window.py.
from .window import Window, WindowProperties, WindowProxy  # noqa: E402,F401
