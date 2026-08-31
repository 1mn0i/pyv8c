from urllib.parse import urljoin, urlsplit, urlunsplit
from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ..dom.model import EventTarget
from ..bindings.registry import interface, js_attributes, js_methods, js_interface_methods
from ..network.api import Blob, Request, Response
from ..core.errors import SecurityError, V8URIError, V8TypeError, InvalidStateError


@interface("Location", None, constructible=False)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Location:
    def __init__(self, href="about:blank", page=None, **_): self.href, self._page = href, page
    def _parts(self): return urlsplit(self.href)
    def get_href(self): return self.href
    def set_href(self, value):
        target = urljoin(self.href, str(value))
        if self._page is not None:
            self._page.request_navigation(target)
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
    __v8_attribute__ = js_attributes(*[(n, "get_" + n, "set_href" if n == "href" else None, 0, 0) for n in ("href", "origin", "protocol", "host", "hostname", "port", "pathname", "search", "hash")])


@interface("Navigator", None)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Navigator:
    __v8_attribute__ = js_attributes(("userAgent", "get_userAgent", None, 0, 1), ("platform", "get_platform", None, 0, 1), ("language", "get_language", None, 0, 1), ("languages", "get_languages", None, 0, 1), ("onLine", "get_onLine", None, 0, 1))
    def __init__(self, user_agent="Mozilla/5.0 Chrome/118.0.0.0 Safari/537.36", **_): self.user_agent = user_agent
    def get_userAgent(self): return self.user_agent
    def get_platform(self): return "Win32"
    def get_language(self): return "en-US"
    def get_languages(self): return ["en-US", "en"]
    def get_onLine(self): return True


@interface("Screen", "EventTarget")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Screen(EventTarget):
    __v8_attribute__ = js_attributes(*[(n, "get_" + n, None, 0, 1) for n in ("width", "height", "availWidth", "availHeight", "colorDepth", "pixelDepth")])
    def __init__(self, **_):
        super().__init__(**_); self.width = self.availWidth = 1920; self.height = self.availHeight = 1080; self.colorDepth = self.pixelDepth = 24
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_availWidth(self): return self.availWidth
    def get_availHeight(self): return self.availHeight
    def get_colorDepth(self): return self.colorDepth
    def get_pixelDepth(self): return self.pixelDepth


@interface("Storage", None)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Storage:
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("getItem", "fn_getItem", 1, 1), ("setItem", "fn_setItem", 2, 0), ("removeItem", "fn_removeItem", 1, 0), ("clear", "fn_clear", 0, 0), ("key", "fn_key", 1, 1))
    def __init__(self, **_): self._data = {}
    def get_length(self): return len(self._data)
    def fn_getItem(self, key): return self._data.get(str(key), None)
    def fn_setItem(self, key, value): self._data[str(key)] = str(value)
    def fn_removeItem(self, key): self._data.pop(str(key), None)
    def fn_clear(self): self._data.clear()
    def fn_key(self, index):
        try: return list(self._data)[int(index)]
        except (IndexError, ValueError): return None


@interface("URLSearchParams", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class URLSearchParams:
    __v8_method__ = js_methods(
        ("append", "fn_append", 2, 0), ("delete", "fn_delete", 1, 0),
        ("get", "fn_get", 1, 1), ("getAll", "fn_getAll", 1, 1),
        ("has", "fn_has", 1, 1), ("set", "fn_set", 2, 0),
        ("sort", "fn_sort", 0, 0), ("toString", "fn_toString", 0, 1),
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


@interface("URL", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
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
            if not parts.scheme or (parts.scheme in ("http", "https", "ws", "wss", "ftp") and not parts.netloc):
                raise V8URIError("Invalid URL")
            parts.port
        except (ValueError, V8URIError) as exc:
            if isinstance(exc, V8TypeError): raise
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
        if parts.scheme not in ("http", "https", "ws", "wss", "ftp") or not parts.netloc:
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
        page = _realm
        if page is None or page.closed: raise InvalidStateError("The execution context is no longer active")
        if not isinstance(blob, Blob): raise V8TypeError("The object is not a Blob")
        return page.context_owner.create_blob_url(blob, page=page)

    @staticmethod
    def fn_revokeObjectURL(_realm, url):
        page = _realm
        if page is None or page.closed: raise InvalidStateError("The execution context is no longer active")
        page.context_owner.revoke_blob_url(str(url))

    __v8_attribute__ = js_attributes(
        ("href", "get_href", "set_href", 0, 0), ("origin", "get_origin", None, 0, 1),
        ("protocol", "get_protocol", "set_protocol", 0, 0), ("username", "get_username", "set_username", 0, 0),
        ("password", "get_password", "set_password", 0, 0), ("host", "get_host", "set_host", 0, 0),
        ("hostname", "get_hostname", "set_hostname", 0, 0), ("port", "get_port", "set_port", 0, 0),
        ("pathname", "get_pathname", "set_pathname", 0, 0), ("search", "get_search", "set_search", 0, 0),
        ("searchParams", "get_searchParams", None, 0, 1), ("hash", "get_hash", "set_hash", 0, 0),
    )
    __v8_method__ = js_methods(("toString", "fn_toString", 0, 1), ("toJSON", "fn_toJSON", 0, 1)) + js_interface_methods(
        ("canParse", "fn_canParse", 1, 1), ("parse", "fn_parse", 1, 1),
        ("createObjectURL", "fn_createObjectURL", 1, 0),
        ("revokeObjectURL", "fn_revokeObjectURL", 1, 0),
    )


@interface("History", None, constructible=False)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class History:
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1), ("state", "get_state", None, 0, 1))
    __v8_method__ = js_methods(("pushState", "fn_pushState", 3, 0), ("replaceState", "fn_replaceState", 3, 0), ("back", "fn_back", 0, 0), ("forward", "fn_forward", 0, 0), ("go", "fn_go", 1, 0))
    def __init__(self, page=None, **_): self._page, self._entries, self._index = page, [(getattr(page, "url", "about:blank"), None)], 0
    def get_length(self): return len(self._entries)
    def get_state(self): return self._entries[self._index][1]
    def _url(self, value): return urljoin(self._page.url, str(value))
    def fn_pushState(self, state, unused="", url=None):
        target = self._url(url) if url is not None else self._page.url
        from ..network.backend import same_origin
        if not same_origin(self._page.url, target): raise SecurityError("A history state URL must be same-origin")
        self._entries = self._entries[:self._index + 1] + [(target, state)]; self._index += 1; self._page.window.location.href = target
    def fn_replaceState(self, state, unused="", url=None):
        target = self._url(url) if url is not None else self._page.url
        from ..network.backend import same_origin
        if not same_origin(self._page.url, target): raise SecurityError("A history state URL must be same-origin")
        self._entries[self._index] = (target, state); self._page.window.location.href = target
    def fn_back(self): return self.fn_go(-1)
    def fn_forward(self): return self.fn_go(1)
    def fn_go(self, delta=0):
        target = max(0, min(len(self._entries) - 1, self._index + int(delta)))
        if target == self._index: return
        self._index = target; self._page.window.location.href = self._entries[target][0]; self._page.window.fn_dispatchEvent(__import__("pyv8c.dom", fromlist=["Event"]).Event("popstate", {"bubbles": False}))


@interface("WindowProperties", "EventTarget", constructible=False, tag="WindowProperties", exposed_in=())
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class WindowProperties(EventTarget):
    pass


@interface("Window", "WindowProperties", constructible=False, tag="Window", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Window(WindowProperties):
    __v8_global_this__ = True
    __v8_attribute__ = js_attributes(
        ("window", "get_window", None, 0, 1), ("self", "get_self", None, 0, 1),
        ("document", "get_document", None, 0, 1), ("location", "get_location", "set_location", 0, 0),
        ("navigator", "get_navigator", None, 0, 1), ("screen", "get_screen", None, 0, 1),
        ("localStorage", "get_localStorage", None, 0, 1), ("sessionStorage", "get_sessionStorage", None, 0, 1),
        ("history", "get_history", None, 0, 1),
        ("innerWidth", "get_innerWidth", None, 0, 1), ("innerHeight", "get_innerHeight", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("setTimeout", "fn_setTimeout", 1, 0), ("clearTimeout", "fn_clearTimeout", 1, 0),
        ("setInterval", "fn_setInterval", 1, 0), ("clearInterval", "fn_clearInterval", 1, 0),
        ("queueMicrotask", "fn_queueMicrotask", 1, 0), ("requestAnimationFrame", "fn_requestAnimationFrame", 1, 0),
        ("fetch", "fn_fetch", 1, 0),
    )
    def __init__(self, page=None, **kw):
        super().__init__(page=page, **kw); self._page = page
        self.location = Location(page=page); self.navigator = Navigator(); self.screen = Screen()
        self.localStorage, self.sessionStorage = Storage(), Storage(); self.innerWidth, self.innerHeight = 1280, 720
        self.history = History(page=page)
    def get_window(self): return self
    def get_self(self): return self
    def get_document(self): return self._page.document
    def get_location(self): return self.location
    def set_location(self, value): self.location.set_href(value)
    def get_navigator(self): return self.navigator
    def get_screen(self): return self.screen
    def get_localStorage(self): return self.localStorage
    def get_sessionStorage(self): return self.sessionStorage
    def get_history(self): return self.history
    def get_innerWidth(self): return self.innerWidth
    def get_innerHeight(self): return self.innerHeight
    def fn_setTimeout(self, callback, delay=0): return self._page.schedule(callback, float(delay) / 1000.0, repeat=False)
    def fn_clearTimeout(self, ident): self._page.cancel(int(ident))
    def fn_setInterval(self, callback, delay=0): return self._page.schedule(callback, float(delay) / 1000.0, repeat=True)
    def fn_clearInterval(self, ident): self._page.cancel(int(ident))
    def fn_queueMicrotask(self, callback): self._page.microtasks.append(callback)
    def fn_requestAnimationFrame(self, callback): return self._page.schedule(callback, 0, repeat=False)
    def fn_fetch(self, request, options=None):
        promise, resolve, reject = self._page.make_promise()
        try:
            req = request if isinstance(request, Request) else Request(request, options, page=self._page)
        except BaseException as exc:
            self._page.post_task(lambda exc=exc: reject(self._page.js_error_value(exc)))
            return promise
        self._page.load_resource_async(req.get_url(), {
            "method": req.get_method(), "headers": req.get_headers(), "body": req._body,
            "timeout": req._timeout, "credentials": req._credentials, "mode": req._mode, "redirect": req._redirect,
        }, lambda response, error: reject(self._page.js_error_value(error)) if error else resolve(response))
        return promise
