"""Host-side HTTP for one page: ``page.net.get(...)``, ``page.net.post(...)``.

Driving a page from Python usually means talking to the same site it is on.  That
means sending the cookies it collected, the headers a browser would send, and
doing it over whichever transport the page itself uses -- cycronet, curl_cffi or
a host-supplied engine (see ``web/network/registry.py``).  This facade is that
shortcut.

It lives in the platform layer because it holds the context's transport and its
cookie jar, neither of which ``web/**`` may reach by name.  It never touches V8,
and nothing of it is visible to page code: a page sends its own requests through
``fetch``/XHR, which take the resource path instead.
"""
from __future__ import annotations

import json as json_module
from concurrent.futures import Future, ThreadPoolExecutor
from urllib.parse import urlencode, urljoin

from ..foundation.errors import InvalidStateError, NetworkError
from ..web.network.models import NetworkRequest, NetworkResponse

#: Headers every request starts from when the environment does not say otherwise.
_FALLBACK_ACCEPT = "*/*"

#: Methods the convenience helpers exist for.
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _header_set(headers, name, value):
    """Set *name* unless a header with that name is already present.

    Header names are case-insensitive on the wire, so a caller's ``content-type``
    has to win over the one this module would add.
    """
    lowered = str(name).lower()
    if any(str(key).lower() == lowered for key in headers):
        return
    headers[name] = value


def _merge_headers(*layers):
    merged: dict[str, str] = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            lowered = str(key).lower()
            for existing in [name for name in merged if name.lower() == lowered]:
                del merged[existing]
            merged[str(key)] = "" if value is None else str(value)
    return merged


class PageNet:
    """The page's own HTTP client, carrying its cookies and headers."""

    def __init__(self, page):
        self._page = page
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pyv8c-net")
        self._closed = False
        #: What the browser applies to every host request, read once because it
        #: cannot change after construction.
        self._browser_headers = dict(getattr(page.context_owner.browser, "default_headers", None) or {})
        #: Page-level default headers, applied on top of the browser's and below
        #: a request's own.  Mutable on purpose: a caller can set the token its
        #: page needs once and forget about it.
        self.headers: dict[str, str] = {}

    # -- transport -------------------------------------------------------

    @property
    def available(self):
        backend = self._page.network_backend
        return backend is not None and backend.http_available

    def _backend(self):
        backend = self._page.network_backend
        if backend is None or not backend.http_available:
            raise NetworkError("page.net requires an HTTP engine; set network_engine= or real_network=True")
        if self._closed:
            raise InvalidStateError("page.net is closed")
        return backend

    # -- request construction --------------------------------------------

    def _default_headers(self):
        environment = self._page.environment
        return {
            "User-Agent": environment.user_agent,
            "Accept-Language": ", ".join(environment.languages),
            "Accept": _FALLBACK_ACCEPT,
        }

    def _resolved_url(self, url, params):
        target = urljoin(self._page.url, str(url))
        if params:
            separator = "&" if "?" in target else "?"
            target = f"{target}{separator}{urlencode(params, doseq=True)}"
        return target

    def _body(self, data, json, headers):
        if json is not None:
            _header_set(headers, "Content-Type", "application/json")
            return json_module.dumps(json)
        if isinstance(data, dict):
            _header_set(headers, "Content-Type", "application/x-www-form-urlencoded")
            return urlencode(data, doseq=True)
        return data

    def build(self, method, url, *, headers=None, params=None, data=None, json=None,
              cookies=True, timeout=None, referer=None, options=None):
        """Turn a host call into one :class:`NetworkRequest`."""
        merged = _merge_headers(
            self._default_headers(),
            self._browser_headers,
            self.headers,
            headers,
        )
        if referer is not None:
            _header_set(merged, "Referer", referer)
        body = self._body(data, json, merged)
        transport_options = dict(options or {})
        request = NetworkRequest(
            url=self._resolved_url(url, params),
            method=str(method).upper(),
            headers=merged,
            body=body,
            timeout=timeout,
            # A host request is not a page request: it has no origin to protect
            # and no CORS to satisfy, so it starts from the jar.  ``omit`` is what
            # tells the transport not to add cookies itself when the caller asked
            # for none, since the header it would add is the one being suppressed.
            credentials="omit" if cookies is False else "include",
            mode="no-cors",
            redirect="follow",
            origin=None,
            options=transport_options,
        )
        cookie = self._cookie_header(request, cookies)
        if cookie:
            request.headers["Cookie"] = cookie
        return request

    def _cookie_header(self, request, cookies):
        """The ``Cookie`` header for *request*, honouring the *cookies* argument."""
        if cookies is False: return ""
        jar = getattr(self._page.network_backend, "cookies", None)
        from_jar = jar.header_for(request.url) if jar is not None else ""
        if not isinstance(cookies, dict):
            return from_jar
        overridden = {str(name) for name in cookies}
        kept = [
            part for part in from_jar.split("; ")
            if part and part.split("=", 1)[0] not in overridden
        ]
        # The caller's own cookies lead, the way the jar leads with its most
        # specific path: an explicit value is the most specific one there is.
        explicit = [f"{name}={value}" for name, value in cookies.items()]
        return "; ".join(explicit + kept)

    def request(self, method, url, **kwargs) -> NetworkResponse:
        """Send one request and return its :class:`NetworkResponse`."""
        backend = self._backend()
        return backend.request_sync(self.build(method, url, **kwargs))

    def request_async(self, method, url, **kwargs) -> Future:
        """Send one request on this page's pool, returning a ``Future``"""
        backend = self._backend()
        request = self.build(method, url, **kwargs)
        future: Future = Future()
        if self._closed:
            future.set_exception(InvalidStateError("page.net is closed"))
            return future

        def run():
            try:
                result = backend.request_sync(request)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

        self._executor.submit(run)
        return future

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


def _install_methods():
    """Give ``PageNet`` its ``get``/``post``/... and ``*_async`` helpers."""
    for name in _METHODS:
        method = name.upper()

        def sync(self, url, _method=method, **kwargs):
            return self.request(_method, url, **kwargs)

        def asynchronous(self, url, _method=method, **kwargs):
            return self.request_async(_method, url, **kwargs)

        sync.__name__ = name
        sync.__doc__ = f"Send a {method} request."
        asynchronous.__name__ = f"{name}_async"
        asynchronous.__doc__ = f"Send a {method} request on the page's pool; returns a ``Future``."
        setattr(PageNet, name, sync)
        setattr(PageNet, f"{name}_async", asynchronous)


_install_methods()


__all__ = ["PageNet"]
