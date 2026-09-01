"""Thread-safe HTTP/WebSocket bridge for the optional cycronet package."""
from __future__ import annotations

from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from threading import RLock
from email.utils import parsedate_to_datetime
from datetime import timezone
from urllib.parse import urlsplit
import importlib
import inspect
import time
from loguru import logger
from ..core.errors import InvalidStateError

_INVALID_COOKIE_NAME_CHARS = frozenset("()<>@,;:") | {"\\", "[", "]", "?", "=", "{", "}", " ", "\t"}


class NetworkUnavailableError(RuntimeError):
    pass


@dataclass
class NetworkRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: object = None
    timeout: float | None = None
    credentials: str = "same-origin"
    mode: str = "cors"
    redirect: str = "follow"
    origin: str | None = None


@dataclass
class NetworkResponse:
    url: str
    status: int = 200
    status_text: str = "OK"
    headers: dict[str, str] = field(default_factory=dict)
    body: object = ""
    redirected: bool = False
    type: str = "basic"


@dataclass
class Cookie:
    name: str
    value: str
    domain: str
    path: str = "/"
    secure: bool = False
    host_only: bool = True
    http_only: bool = False
    same_site: str = ""
    expires: float | None = None
    creation_index: int = 0


class CookieJar:
    def __init__(self):
        self._cookies: dict[tuple[str, str, str], Cookie] = {}
        self._lock = RLock()
        self._creation_index = 0

    def add_from_response(self, url, header):
        if not header:
            return
        if isinstance(header, (list, tuple)):
            for value in header:
                self.add_from_response(url, value)
            return
        parsed_url = urlsplit(url)
        cookie = SimpleCookie()
        try:
            cookie.load(header)
        except Exception:
            return
        for morsel in cookie.values():
            domain = (morsel["domain"] or parsed_url.hostname or "").lower().lstrip(".")
            path = morsel["path"] if morsel["path"].startswith("/") else self._default_path(parsed_url.path)
            expires = self._parse_expires(morsel["expires"])
            max_age = self._parse_max_age(morsel["max-age"])
            self._store(
                parsed_url,
                morsel.key,
                morsel.value,
                domain=domain,
                path=path,
                secure=bool(morsel["secure"]),
                host_only=not bool(morsel["domain"]),
                http_only=bool(morsel["httponly"]),
                same_site=str(morsel["samesite"] or "").lower(),
                expires=expires,
                max_age=max_age,
                source="network",
            )

    @staticmethod
    def _default_path(path):
        path = str(path or "/")
        if not path.startswith("/") or path.count("/") <= 1:
            return "/"
        return path[:path.rfind("/")] or "/"

    @staticmethod
    def _cookie_url(parsed_url):
        return parsed_url.scheme in ("http", "https") and bool(parsed_url.hostname)

    @staticmethod
    def _parse_expires(value):
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _parse_max_age(value):
        if value in (None, ""):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _domain_matches(host, domain, host_only):
        if not domain:
            return not host
        return host == domain if host_only else host == domain or host.endswith("." + domain)

    @staticmethod
    def _path_matches(request_path, cookie_path):
        return request_path == cookie_path or (
            request_path.startswith(cookie_path)
            and (cookie_path.endswith("/") or request_path[len(cookie_path):].startswith("/"))
        )

    def _expired(self, cookie, now=None):
        return cookie.expires is not None and cookie.expires <= (time.time() if now is None else now)

    def _purge_expired(self):
        now = time.time()
        for key, cookie in list(self._cookies.items()):
            if self._expired(cookie, now):
                del self._cookies[key]

    def _store(self, parsed_url, name, value, *, domain, path, secure, host_only,
               http_only=False, same_site="", expires=None, max_age=None, source="script"):
        if not self._cookie_url(parsed_url):
            return False
        host = (parsed_url.hostname or "").lower()
        if domain and not self._domain_matches(host, domain, False):
            return False
        if secure and parsed_url.scheme not in ("https", "wss") and source == "script":
            return False
        if max_age is not None:
            expires = time.time() + max_age if max_age > 0 else 0
        key = (domain, path, name)
        with self._lock:
            self._purge_expired()
            previous = self._cookies.get(key)
            if source == "script" and previous is not None and previous.http_only:
                return False
            if expires is not None and expires <= time.time():
                changed = previous is not None
                self._cookies.pop(key, None)
            else:
                self._creation_index += 1
                item = Cookie(
                    name, value, domain, path, secure, host_only, http_only,
                    same_site, expires,
                    previous.creation_index if previous else self._creation_index,
                )
                changed = previous != item
                self._cookies[key] = item
        if changed:
            logger.debug("cookie changed: {}", self.document_for(parsed_url.geturl()))
        return changed

    def set_from_document(self, url, assignment):
        """Apply one ``document.cookie`` assignment and return whether it changed state."""
        parsed_url = urlsplit(str(url))
        if not self._cookie_url(parsed_url):
            return False
        raw = str(assignment)
        pair, *attributes = raw.split(";")
        if "=" not in pair:
            return False
        name, value = (part.strip() for part in pair.split("=", 1))
        if not name or any(char in _INVALID_COOKIE_NAME_CHARS for char in name):
            return False
        # Cookie-octet is intentionally left permissive here, matching Blink's
        # preservation of most script-assigned values while trimming whitespace.
        options = {"domain": (parsed_url.hostname or "").lower(), "path": self._default_path(parsed_url.path),
                   "secure": False, "host_only": True, "http_only": False,
                   "same_site": "", "expires": None, "max_age": None}
        for attribute in attributes:
            key, separator, raw_value = attribute.strip().partition("=")
            key = key.lower()
            raw_value = raw_value.strip()
            if key == "domain" and separator:
                domain = raw_value.lower().lstrip(".")
                if not domain or not self._domain_matches((parsed_url.hostname or "").lower(), domain, False):
                    return False
                options["domain"], options["host_only"] = domain, False
            elif key == "path" and separator and raw_value.startswith("/"):
                options["path"] = raw_value
            elif key == "secure":
                options["secure"] = True
            elif key == "httponly":
                # Scripts cannot create or alter HttpOnly cookies.
                return False
            elif key == "samesite" and separator:
                options["same_site"] = raw_value.lower()
            elif key == "expires" and separator:
                options["expires"] = self._parse_expires(raw_value)
            elif key == "max-age" and separator:
                options["max_age"] = self._parse_max_age(raw_value)
        changed = self._store(parsed_url, name, value, **options)
        return changed

    def header_for(self, url):
        return self._for_url(url, include_http_only=True)

    def document_for(self, url):
        return self._for_url(url, include_http_only=False)

    def _for_url(self, url, *, include_http_only):
        parsed = urlsplit(url)
        if not self._cookie_url(parsed):
            return ""
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        secure = parsed.scheme in ("https", "wss")
        values = []
        with self._lock:
            self._purge_expired()
            matched = [cookie for cookie in self._cookies.values()
                       if (include_http_only or not cookie.http_only)
                       and self._domain_matches(host, cookie.domain, cookie.host_only)
                       and self._path_matches(path, cookie.path)
                       and (not cookie.secure or secure)]
            matched.sort(key=lambda cookie: (-len(cookie.path), cookie.creation_index))
            values.extend(f"{cookie.name}={cookie.value}" for cookie in matched)
        return "; ".join(values)

    def clear(self):
        with self._lock:
            self._cookies.clear()


def same_origin(left, right):
    a, b = urlsplit(str(left)), urlsplit(str(right))
    def origin(value):
        port = value.port or (443 if value.scheme == "https" else 80 if value.scheme == "http" else None)
        return value.scheme.lower(), (value.hostname or "").lower(), port
    return origin(a) == origin(b)


class CronetNetworkBackend:
    """Owns one optional cycronet client and never calls V8 from workers."""
    def __init__(self, *, options=None):
        self.options = dict(options or {})
        try:
            self.cycronet = importlib.import_module("cycronet")
        except ImportError as exc:
            raise NetworkUnavailableError("real_network=True requires the optional 'cycronet' package") from exc
        self.client = self._new_client()
        self.executor = ThreadPoolExecutor(max_workers=int(self.options.pop("max_workers", 8)), thread_name_prefix="pyv8c-cronet")
        self.cookies = CookieJar()
        self._websockets = set()
        self._closed = False
        self._lock = RLock()

    def _new_client(self):
        client_type = getattr(self.cycronet, "CronetClient", None)
        if client_type is None:
            client_type = getattr(self.cycronet, "PyCronetClient", None)
        if client_type is None:
            raise NetworkUnavailableError("installed cycronet does not expose CronetClient or PyCronetClient")
        return client_type(**self.options)

    @staticmethod
    def _response(raw, url):
        headers = getattr(raw, "headers", {}) or {}
        if hasattr(headers, "items"):
            headers = {
                str(k).lower(): ([str(item) for item in v] if isinstance(v, (list, tuple)) else str(v))
                for k, v in headers.items()
            }
        else:
            headers = {}
        body = getattr(raw, "content", None)
        if body is None:
            body = getattr(raw, "text", "")
        if callable(body):
            body = body()
        return NetworkResponse(
            str(getattr(raw, "url", None) or url),
            int(getattr(raw, "status_code", getattr(raw, "status", 200))),
            str(getattr(raw, "reason", "")), headers, body,
            bool(getattr(raw, "history", ())),
        )

    def _call(self, request: NetworkRequest):
        if self._closed:
            raise InvalidStateError("network backend is closed")
        headers = dict(request.headers)
        cookie = self.cookies.header_for(request.url) if request.credentials != "omit" and (request.credentials == "include" or not request.origin or same_origin(request.origin, request.url)) else ""
        if cookie and "cookie" not in {k.lower() for k in headers}:
            headers["Cookie"] = cookie
        method = request.method.lower()
        fn = getattr(self.client, method, None)
        if fn is None:
            fn = getattr(self.client, "request", None)
            if fn is None:
                raise NetworkUnavailableError(f"cycronet client has no {method} method")
            raw = fn(request.method, request.url, headers=headers, data=request.body, timeout=request.timeout)
        else:
            kwargs = {"headers": headers}
            if request.body is not None:
                kwargs["data"] = request.body
            if request.timeout is not None:
                try:
                    parameters = inspect.signature(fn).parameters.values()
                    accepts_timeout = any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD or parameter.name == "timeout"
                        for parameter in parameters
                    )
                except (TypeError, ValueError):
                    accepts_timeout = True
                if accepts_timeout:
                    kwargs["timeout"] = request.timeout
            raw = fn(request.url, **kwargs)
        response = self._response(raw, request.url)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookies.add_from_response(response.url, set_cookie)
        return response

    def request_sync(self, request: NetworkRequest):
        return self._call(request)

    def request_async(self, page, request, callback):
        with self._lock:
            if self._closed:
                page.post_task(lambda: callback(None, RuntimeError("network backend is closed")))
                return
            page.begin_network()
            future = self.executor.submit(self._call, request)

        def complete(done):
            try:
                result, error = done.result(), None
            except BaseException as exc:
                result, error = None, exc
            if page.closed:
                page.complete_network()
                return
            page.post_task(lambda: (page.complete_network(), callback(result, error)))
        future.add_done_callback(complete)

    def websocket(self, page, url, protocols, callbacks, options=None):
        if self._closed:
            raise InvalidStateError("network backend is closed")
        factory = getattr(self.client, "websocket", None)
        if factory is None:
            raise NetworkUnavailableError("installed cycronet does not expose websocket callback API")
        callbacks = dict(callbacks)
        kwargs = {"on_open": lambda ws: page.post_task(lambda: callbacks.get("open", lambda *_: None)(ws)),
                  "on_message": lambda ws, message, is_text=True: page.post_task(lambda: callbacks.get("message", lambda *_: None)(ws, message, is_text)),
                  "on_close": lambda ws, code, reason, was_clean=True: page.post_task(lambda: callbacks.get("close", lambda *_: None)(ws, code, reason, was_clean)),
                  "on_error": lambda ws, error, net_error=None: page.post_task(lambda: callbacks.get("error", lambda *_: None)(ws, error, net_error))}
        try:
            ws = factory(url, protocols=protocols or [], **kwargs)
        except TypeError:
            ws = factory(url, **kwargs)
        with self._lock:
            self._websockets.add(ws)
        if hasattr(ws, "run_in_background"):
            ws.run_in_background()
        return ws

    def close_websocket(self, ws):
        try:
            ws.close(1000, "page closed")
        except Exception:
            pass
        with self._lock:
            self._websockets.discard(ws)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sockets = list(self._websockets)
            self._websockets.clear()
        for ws in sockets:
            try:
                ws.close(1000, "context closed")
            except Exception:
                pass
        close = getattr(self.client, "close", None)
        if callable(close):
            close()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()
