"""Helpers shared by the browser object model modules; more than one module
needs each of them.
"""

from urllib.parse import urlsplit

from ..page_errors import SecurityError
from ..page_errors import js_error_value


def deny_cross_origin():
    raise SecurityError("Blocked a frame with origin from accessing a cross-origin frame")


def document_key(url):
    """The URL parts a same-document (fragment) navigation cannot change."""
    parts = urlsplit(url)
    return (parts.scheme, parts.netloc, parts.path, parts.query)


def is_secure_context(url):
    """Whether *url* is an origin a browser treats as potentially trustworthy."""
    text = str(url)
    # A blob (or filesystem) URL is as trustworthy as the origin it carries.
    if text[:5].lower() == "blob:":
        return is_secure_context(text[5:])
    parts = urlsplit(text)
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    if scheme in ("https", "wss", "file", "data"):
        return True
    if scheme in ("http", "ws"):
        return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")
    return False


#: How many creator hops to follow before calling it unknown.
CREATOR_HOPS = 8


def page_secure_context(page, url=None):
    """Whether a ``Page``'s document is in a secure context, its creator included.

    ``about:blank`` and ``about:srcdoc`` carry no scheme of their own, so a browser inherits the
    context from the document that created them: an inline frame inside an ``https`` page is a
    secure context, and Chrome exposes ``navigator.userActivation`` and its thirty relatives
    there.  ``Page.origin`` already inherits along this same ``parent_page`` link
    (``origin_tuple(..., inherited=...)``); the secure context has to follow it too, or the two
    disagree about the frame -- which is exactly the probe Cloudflare's widget makes (measured
    against Chrome 2026-10-06, see ``examples/5s/script/secure_frame.py``).

    ``url`` is for a commit still in flight, where the page has not taken the new address yet.
    """
    for _hop in range(CREATOR_HOPS):
        if page is None:
            return False
        text = str(url if url is not None else getattr(page, "url", "") or "")
        if urlsplit(text).scheme.lower() not in ("", "about"):
            return is_secure_context(text)
        url = None
        page = getattr(page, "parent_page", None)
    return False


def page_base_url(page, url=None):
    """The URL relative loads in a ``Page``'s document resolve against.

    ``about:blank`` and ``about:srcdoc`` carry no scheme of their own, so a browser takes the
    base URL from the document that created them -- which is why ``<script src="/cdn-cgi/.../jsd/main.js">``
    inside a frame still reaches the host that served the frame.  Resolving against the
    document's own address instead yields a bare path, and that path is then refused as "not
    a scheme a page loads", so nothing is requested at all (measured against Chrome
    2026-10-10, see ``examples/5s/script/base_url_frames.py``).

    Same walk as :func:`page_secure_context`, and the same reason: ``Page.origin`` already
    inherits along ``parent_page``, so resolution has to follow it too.  The document's own URL
    is left alone -- ``location.href`` still reads ``about:blank``.
    """
    for _hop in range(CREATOR_HOPS):
        if page is None:
            return str(url or "")
        text = str(url if url is not None else getattr(page, "url", "") or "")
        if urlsplit(text).scheme.lower() not in ("", "about"):
            return text
        url = None
        page = getattr(page, "parent_page", None)
    return str(url or "")


def window_secure_context(window, url=None):
    """``window``'s secure context, found through the realm's page rather than its parent.

    ``window.parent`` is an API-level accessor and a sandboxed frame has none to give: the
    creator is an *internal* link, and ``PageServices._page_realm.page`` is where
    ``ancestor_origins()`` already reads it from.
    """
    text = url
    if text is None:
        try:
            text = window.location.get_href()
        except BaseException:  # noqa: BLE001 - a window on its way out is not this question
            return False
    services = getattr(window, "_services", None)
    page = getattr(getattr(services, "_page_realm", None), "page", None)
    if page is None:
        return is_secure_context(text)
    return page_secure_context(page, text)


def settled(services, *, value=None, error=None):
    """A promise in this realm, already settled either way."""
    promise, resolve, reject = services.realm_services.make_promise()
    if error is None:
        resolve(value)
    else:
        reject(js_error_value(error))
    return promise


def base64_like(interface, member, data):
    """Shared WebIDL conversion and forgiving Base64 for Window and Workers."""
    import pyv8
    from ...foundation.arguments import MISSING, missing_error, dom_string
    from ...foundation.base64 import forgiving_base64_decode, forgiving_base64_encode
    from ..page_errors import InvalidCharacterError, PageTypeError
    if data is MISSING:
        raise missing_error(interface, member, 1, 0, constructor=False)
    try:
        text = dom_string(data)
    except pyv8.JSException as error:
        original = getattr(error, 'original', None)
        reason = str(original._get('message')) if isinstance(original, pyv8.JSObject) else ''
        if reason not in ('Cannot convert a Symbol value to a string',
                          'Cannot convert object to primitive value'):
            raise
        raise PageTypeError(f"Failed to execute '{member}' on '{interface}': {reason}") from None
    action = 'decoded' if member == 'atob' else 'encoded'
    if any(ord(character) > 255 for character in text):
        raise InvalidCharacterError(f"Failed to execute '{member}' on '{interface}':"
                                    f" The string to be {action} contains characters outside of the Latin1 range.")
    result = forgiving_base64_decode(text) if member == 'atob' else forgiving_base64_encode(text)
    if result is None:
        raise InvalidCharacterError(f"Failed to execute '{member}' on '{interface}':"
                                    " The string to be decoded is not correctly encoded.")
    return result


def promise_receiver_error(receiver, cls, method):
    """Reject a Promise operation called on an object without its WebIDL brand.

    Blink leaves these operations' native receiver check off, so the binding can answer
    a rejected Promise instead of throwing before the call returns.
    """
    if isinstance(receiver, cls):
        return None
    import pyv8
    from ...foundation.promises import make_promise
    from ..page_errors import PageTypeError

    error = PageTypeError(f"Failed to execute '{method}' on '{cls.__name__}': Illegal invocation")
    context = pyv8.current_context()
    if context is None:
        raise error
    promise, _resolve, reject = make_promise(context)
    reject(js_error_value(error))
    return promise


def promise_rejected_in_caller_realm(error, services=None):
    """A rejected promise in the realm of the operation that is running.

    A conversion failure belongs to the realm the call was made through, while a
    successful result belongs to the receiver's realm; a borrowed operation shows both.
    """
    import pyv8
    from ...foundation.promises import make_promise

    context = pyv8.current_context()
    if context is not None:
        promise, _resolve, reject = make_promise(context)
    else:
        realm = getattr(services, 'realm_services', None)
        if realm is None:
            raise error
        promise, _resolve, reject = realm.make_promise()
    reject(js_error_value(error))
    return promise
