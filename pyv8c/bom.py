from urllib.parse import urljoin, urlsplit, urlunsplit
import time
from .dom import EventTarget
from .interfaces import interface, js_attributes, js_methods
from pyv8.tools import ConstructorConfig
from pyv8.flag import FlagExposed
from .network import Request, Response, current_page
from .error import SecurityError, V8URIError, as_js_error


@interface("Location", None, constructible=False)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Location:
    def __init__(self, href="about:blank", page=None, **_): self.href, self._page = href, page
    def _parts(self): return urlsplit(self.href)
    def get_href(self): return self.href
    def set_href(self, value): self.href = urljoin(self.href, str(value))
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


@interface("URLSearchParams", None, constructible=True)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class URLSearchParams:
    def __init__(self, init="", **_):
        from urllib.parse import parse_qsl
        self._items = list(parse_qsl(str(init).lstrip("?"), keep_blank_values=True)) if not isinstance(init, dict) else list(init.items())
    def fn_toString(self):
        from urllib.parse import urlencode
        return urlencode(self._items)


@interface("URL", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class URL:
    def __init__(self, url="", base=None, **_):
        value = urljoin(str(base or "about:blank"), str(url))
        if not value or any(ch.isspace() for ch in value): raise V8URIError("Invalid URL")
        self.href = value
    def get_href(self): return self.href
    __v8_attribute__ = js_attributes(("href", "get_href", "set_href", 0, 0))
    def set_href(self, value): self.href = str(value)


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
        from .network_backend import same_origin
        if not same_origin(self._page.url, target): raise SecurityError("A history state URL must be same-origin")
        self._entries = self._entries[:self._index + 1] + [(target, state)]; self._index += 1; self._page.window.location.href = target
    def fn_replaceState(self, state, unused="", url=None):
        target = self._url(url) if url is not None else self._page.url
        from .network_backend import same_origin
        if not same_origin(self._page.url, target): raise SecurityError("A history state URL must be same-origin")
        self._entries[self._index] = (target, state); self._page.window.location.href = target
    def fn_back(self): return self.fn_go(-1)
    def fn_forward(self): return self.fn_go(1)
    def fn_go(self, delta=0):
        target = max(0, min(len(self._entries) - 1, self._index + int(delta)))
        if target == self._index: return
        self._index = target; self._page.window.location.href = self._entries[target][0]; self._page.window.fn_dispatchEvent(__import__("pyv8c.dom", fromlist=["Event"]).Event("popstate", {"bubbles": False}))


@interface("WindowProperties", "EventTarget", constructible=False, tag="WindowProperties")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class WindowProperties(EventTarget):
    pass


@interface("Window", "WindowProperties", constructible=False, tag="Window")
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
        req = request if isinstance(request, Request) else Request(request, options, page=self._page)
        promise, resolve, reject = self._page._make_promise()
        self._page.load_resource_async(req.get_url(), {
            "method": req.get_method(), "headers": req.get_headers(), "body": req._body,
            "timeout": req._timeout, "credentials": req._credentials, "mode": req._mode, "redirect": req._redirect,
        }, lambda response, error: reject(as_js_error(error)) if error else resolve(response))
        return promise
