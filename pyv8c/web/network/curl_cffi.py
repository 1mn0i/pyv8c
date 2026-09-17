"""Thread-safe HTTP transport bridge for the optional curl_cffi package.

curl_cffi's reason to exist is the impersonation layer, so this backend exists to
let a page (or a host script driving ``page.net``) send requests that look like a
real browser's at the TLS layer.  Everything else is deliberately thin: the
request/response shapes, the error type and the delivery contract are the ones
``models.py`` defines, so the resource loader cannot tell which engine answered.

Two deliberate choices are worth knowing about:

* **The context's jar is the only cookie store.**  Requests carry a ``Cookie``
  header built from it and responses write their ``Set-Cookie`` back into it,
  while the client's own jar is discarded (``discard_cookies``).  Sharing one
  store is what makes ``page.net`` and a page's ``fetch`` see each other's
  cookies; keeping curl's jar would send them twice and let the two drift.
* **The transport adds no headers of its own.**  ``default_headers=False`` is the
  session default so that what goes on the wire is exactly the merge the caller
  decided (engine defaults, then page defaults, then per-request overrides).
"""
import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from loguru import logger

from ...foundation.errors import InvalidStateError, NetworkError
from .cookies import CookieJar
from .models import (
    NetworkRequest,
    NetworkResponse,
    NetworkUnavailableError,
    RequestHandle,
    cookie_header,
)
from .registry import signature_option_keys

#: Per-request options this transport understands, forwarded straight to
#: ``curl_cffi``.  Options a caller sets for another engine are ignored here.
_EXTRA_KWARGS = (
    "impersonate", "ja3", "akamai", "perk", "extra_fp",
    "proxies", "proxy", "proxy_auth", "verify", "cert",
    "http_version", "interface", "doh_url",
)


def _curl_cffi_module():
    """The optional ``curl_cffi.requests`` module, or an error naming what is missing."""
    try:
        return importlib.import_module("curl_cffi.requests")
    except ImportError as exc:
        raise NetworkUnavailableError(
            "network_engine='curl_cffi' requires the optional 'curl_cffi' package"
        ) from exc


def _curl_cffi_session_type(module):
    session_type = getattr(module, "Session", None)
    if session_type is None:
        raise NetworkUnavailableError("installed curl_cffi does not expose requests.Session")
    return session_type


class CurlCffiTransport:
    """HTTP transport backed by one curl_cffi session.

    The session is built with ``use_thread_local_curl`` (curl_cffi's default),
    so a single instance serves both the synchronous and the pooled paths: each
    thread gets its own curl handle, and no lock is needed around a request.

    It performs requests only: a page's ``new WebSocket`` is served by whichever
    WebSocket engine the context assembled alongside it; see ``registry.py``.
    """

    #: Options this bridge consumes itself instead of forwarding them: its own
    #: thread pool size, and ``default_headers``.
    bridge_option_keys = frozenset({"default_headers", "max_workers"})

    @classmethod
    def accepted_option_keys(cls) -> frozenset[str] | None:
        """The keys ``options=`` may carry: the bridge's, plus the session's own.

        curl_cffi declares its session parameters to type checkers only (its
        ``BaseSessionParams`` lives under ``if TYPE_CHECKING``), so at runtime
        there is usually no list to read and ``None`` says exactly that.  It
        costs nothing: the session refuses a key it does not know when it is
        built, at the same point in creating the context.
        """
        session_keys = signature_option_keys(_curl_cffi_session_type(_curl_cffi_module()))
        if session_keys is None:
            return None
        return session_keys | cls.bridge_option_keys

    def __init__(self, *, options=None):
        self.options = dict(options or {})
        # ``max_workers`` configures this bridge's Python executor rather than
        # the client, so it is consumed here and never forwarded.
        max_workers = int(self.options.pop("max_workers", 4))
        default_headers = bool(self.options.pop("default_headers", False))
        self.curl_cffi = _curl_cffi_module()
        self.session = self._new_session(default_headers)
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="pyv8c-curl")
        self.cookies = CookieJar()
        self._closed = False
        self._lock = RLock()

    def _new_session(self, default_headers):
        session_type = _curl_cffi_session_type(self.curl_cffi)
        options = dict(self.options)
        options.setdefault("default_headers", default_headers)
        try:
            return session_type(**options)
        except TypeError as exc:
            # With no key list to check against (see accepted_option_keys), the
            # session is the one that refuses a misspelt option.  Naming where
            # the option came from is all that is left to add; the original text
            # stays in the chain.
            raise TypeError(
                f"http_options was refused by the curl_cffi session: {exc}"
            ) from exc

    def _call(self, request: NetworkRequest):
        if self._closed:
            raise InvalidStateError("network backend is closed")
        logger.debug("network download: {} {}", request.method, request.url)
        headers = dict(request.headers)
        cookie = cookie_header(self.cookies, request)
        if cookie and "cookie" not in {str(name).lower() for name in headers}:
            headers["Cookie"] = cookie
        options = request.options or {}
        allow_redirects = options.get("allow_redirects")
        kwargs = {
            "headers": headers,
            "discard_cookies": True,
            "allow_redirects": request.redirect != "manual" if allow_redirects is None else bool(allow_redirects),
        }
        if request.body is not None:
            kwargs["data"] = request.body
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        for name in _EXTRA_KWARGS:
            if options.get(name) is not None:
                kwargs[name] = options[name]
        started = time.perf_counter()
        try:
            raw = self.session.request(str(request.method).upper(), request.url, **kwargs)
        except Exception as exc:  # every transport failure reads the same here
            raise NetworkError(f"{request.method} {request.url} failed: {exc}") from exc
        response = self._response(raw, request.url, time.perf_counter() - started)
        set_cookie = response.headers.get("set-cookie")
        if set_cookie:
            self.cookies.add_from_response(response.url, set_cookie)
        return response

    @staticmethod
    def _headers(raw):
        """Response headers with lower-case names and every Set-Cookie kept.

        ``multi_items`` is the only view that sees repeated headers: a client
        that folds them into a dict keeps just the first ``Set-Cookie``, and the
        cookies after it would be lost.  Values are joined with ``, `` -- the
        form the cookie jar's splitter expects -- because a page-visible
        ``Headers`` object cannot carry repeated names.
        """
        headers = getattr(raw, "headers", None)
        if headers is None:
            return {}
        collected: dict[str, list[str]] = {}
        multi = getattr(headers, "multi_items", None)
        if callable(multi):
            try:
                items = list(multi())
            except Exception:  # fall back to the plain view
                items = []
        else:
            items = []
        if not items:
            source = headers.items() if hasattr(headers, "items") else headers
            items = [(key, value) for key, value in source]
        for key, value in items:
            collected.setdefault(str(key).lower(), []).append(str(value))
        return {key: ", ".join(values) for key, values in collected.items()}

    @classmethod
    def _response(cls, raw, url, fallback_elapsed):
        elapsed = getattr(raw, "elapsed", None)
        total_seconds = getattr(elapsed, "total_seconds", None)
        body = getattr(raw, "content", None)
        return NetworkResponse(
            str(getattr(raw, "url", None) or url),
            int(getattr(raw, "status_code", 200)),
            str(getattr(raw, "reason", "") or ""),
            cls._headers(raw),
            getattr(raw, "text", "") if body is None else body,
            bool(getattr(raw, "history", ())),
            "basic",
            float(total_seconds()) if callable(total_seconds) else float(fallback_elapsed),
        )

    def request_sync(self, request: NetworkRequest): return self._call(request)

    def request_async(self, tasks, request, callback):
        """Run *request* on the delivery pool, answering on the realm's queue.

        *tasks* is the realm's task capability: the callback is posted through it
        so JavaScript is only ever touched on the owner thread, and the in-flight
        counters are what let ``run_until_idle`` and navigation waits see that a
        request is still outstanding.
        """
        handle = RequestHandle()
        with self._lock:
            if self._closed:
                tasks.post_task(lambda: callback(None, RuntimeError("network backend is closed")))
                return handle
            tasks.begin_network()
            future = self.executor.submit(self._call, request)
            handle.future = future

        def complete(done):
            try:
                result, error = done.result(), None
            except BaseException as exc:  # delivered to the caller, not raised here
                result, error = None, exc
            if handle.cancelled or tasks.closed:
                tasks.complete_network()
                return
            tasks.post_task(
                lambda: (tasks.complete_network(), callback(result, error))
                if not handle.cancelled else tasks.complete_network()
            )

        future.add_done_callback(complete)
        return handle

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self.session, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # teardown must not raise
                logger.debug("failed to close the curl_cffi session", exc_info=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.cookies.clear()


__all__ = ["CurlCffiTransport"]
