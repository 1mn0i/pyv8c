"""Cookie storage and matching for page and network contexts.

One :class:`CookieJar` per context is the single store both sides share, making a
document and its requests one session; matching follows RFC 6265's host/path/secure
rules, with a wire view and a script-visible view.
"""
import re
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from threading import RLock
from urllib.parse import urlsplit

from ..foundation.logging import logger
from ..foundation.sites import is_public_suffix

_INVALID_COOKIE_NAME_CHARS = frozenset("()<>@,;:") | {"\\", "[", "]", "?", "=", "{", "}", " ", "\t"}

#: A comma starts a new joined cookie only when ``token=`` follows before any ``;`` or ``,``.
_SET_COOKIE_SEPARATOR = re.compile(r",(?=\s*[^;=,\s]+\s*=)")


def split_set_cookie(header):
    text = str(header)
    if "," not in text:
        return [text]
    return _SET_COOKIE_SEPARATOR.split(text)


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
    """The context's single cookie store."""

    def __init__(self):
        self._cookies: dict[tuple[str, str, str], Cookie] = {}
        self._lock = RLock()
        self._creation_index = 0

    def add_from_response(self, url, header):
        if not header: return
        if isinstance(header, (list, tuple)):
            for value in header: self.add_from_response(url, value)
            return
        parsed_url = urlsplit(url)
        for value in split_set_cookie(header):
            self._add_one_from_response(parsed_url, value)

    def _add_one_from_response(self, parsed_url, header):
        """Store one ``Set-Cookie`` value.

        The attributes are read here rather than handed to
        ``http.cookies.SimpleCookie``: that parser is written for a ``Cookie:``
        *request* header, and a ``Set-Cookie`` whose attribute shape it does not
        anticipate makes it stop early, dropping every cookie in the line without a
        word.  A Cloudflare challenge's ``cf_clearance`` was lost that way
        (``Expires=Wed, 03 Nov 2027 ...`` , measured 2026-11-03) while the header
        itself was well formed.  The grammar below is the one
        :meth:`set_from_document` already reads, except that ``HttpOnly`` is allowed
        here -- a script may not read it, the wire still sends it.  ``Partitioned``
        (CHIPS) is read and ignored rather than dropped: it narrows a cookie's
        visibility, it does not make the line unreadable.
        """
        pieces = str(header).split(";")
        pair = pieces[0]
        if "=" not in pair:
            return
        name, value = (part.strip() for part in pair.split("=", 1))
        if not name or any(char in _INVALID_COOKIE_NAME_CHARS for char in name):
            return
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        options = {
            "domain": (parsed_url.hostname or "").lower(),
            "path": self._default_path(parsed_url.path),
            "secure": False, "host_only": True, "http_only": False,
            "same_site": "", "expires": None, "max_age": None,
        }
        for attribute in pieces[1:]:
            key, separator, raw_value = attribute.strip().partition("=")
            key, raw_value = key.lower(), raw_value.strip()
            if key == "domain" and separator:
                domain = raw_value.lower().lstrip(".")
                if not domain:
                    return
                options["domain"], options["host_only"] = domain, False
            elif key == "path" and separator and raw_value.startswith("/"):
                options["path"] = raw_value
            elif key == "secure":
                options["secure"] = True
            elif key == "httponly":
                options["http_only"] = True
            elif key == "samesite" and separator:
                options["same_site"] = raw_value.lower()
            elif key == "expires" and separator:
                options["expires"] = self._parse_expires(raw_value)
            elif key == "max-age" and separator:
                options["max_age"] = self._parse_max_age(raw_value)
        self._store(parsed_url, name, value, **options, source="network")

    @staticmethod
    def _default_path(path):
        path = str(path or "/")
        if not path.startswith("/") or path.count("/") <= 1: return "/"
        return path[:path.rfind("/")] or "/"

    @staticmethod
    def _allows_cookies(parsed_url): return parsed_url.scheme in ("http", "https") and bool(parsed_url.hostname)

    @staticmethod
    def _parse_expires(value):
        if not value: return None
        try:
            parsed = parsedate_to_datetime(str(value))
            return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except (TypeError, ValueError, OverflowError): return None

    @staticmethod
    def _parse_max_age(value):
        if value in (None, ""): return None
        try: return int(str(value).strip())
        except (TypeError, ValueError): return None

    @staticmethod
    def _domain_matches(host, domain, host_only):
        if not domain: return not host
        return host == domain if host_only else host == domain or host.endswith("." + domain)

    @staticmethod
    def _path_matches(request_path, cookie_path):
        return request_path == cookie_path or (request_path.startswith(cookie_path) and (cookie_path.endswith("/") or request_path[len(cookie_path):].startswith("/")))

    def _expired(self, cookie, now=None): return cookie.expires is not None and cookie.expires <= (time.time() if now is None else now)

    def _purge_expired(self):
        now = time.time()
        for key, cookie in list(self._cookies.items()):
            if self._expired(cookie, now): del self._cookies[key]

    def _store(self, parsed_url, name, value, *, domain, path, secure, host_only,
               http_only=False, same_site="", expires=None, max_age=None, source="script"):
        if not self._allows_cookies(parsed_url): return False
        host = (parsed_url.hostname or "").lower()
        if domain and not self._domain_matches(host, domain, False): return False
        if not host_only and is_public_suffix(domain):
            if domain != host: return False
            host_only = True
        if secure and parsed_url.scheme not in ("https", "wss") and source == "script": return False
        if same_site.lower() == "none" and not secure: return False
        if name.startswith("__Secure-") and not secure: return False
        if name.startswith("__Host-") and (not secure or not host_only or path != "/"): return False
        if max_age is not None: expires = time.time() + max_age if max_age > 0 else 0
        key = (domain, path, name)
        with self._lock:
            self._purge_expired(); previous = self._cookies.get(key)
            if source == "script" and previous is not None and previous.http_only: return False
            if expires is not None and expires <= time.time():
                changed = previous is not None; self._cookies.pop(key, None)
            else:
                self._creation_index += 1
                item = Cookie(name, value, domain, path, secure, host_only, http_only, same_site, expires, previous.creation_index if previous else self._creation_index)
                changed = previous != item; self._cookies[key] = item
        if changed: logger.debug("cookie changed: {}", self.header_for(parsed_url.geturl()))
        return changed

    def set_from_document(self, url, assignment):
        parsed_url = urlsplit(str(url))
        if not self._allows_cookies(parsed_url): return False
        pair, *attributes = str(assignment).split(";")
        if "=" not in pair: return False
        name, value = (part.strip() for part in pair.split("=", 1))
        if not name or any(char in _INVALID_COOKIE_NAME_CHARS for char in name): return False
        options = {"domain": (parsed_url.hostname or "").lower(), "path": self._default_path(parsed_url.path), "secure": False, "host_only": True, "http_only": False, "same_site": "", "expires": None, "max_age": None}
        for attribute in attributes:
            key, separator, raw_value = attribute.strip().partition("="); key = key.lower(); raw_value = raw_value.strip()
            if key == "domain" and separator:
                domain = raw_value.lower().lstrip(".")
                if not domain or not self._domain_matches((parsed_url.hostname or "").lower(), domain, False): return False
                options["domain"], options["host_only"] = domain, False
            elif key == "path" and separator and raw_value.startswith("/"): options["path"] = raw_value
            elif key == "secure": options["secure"] = True
            elif key == "httponly": return False
            elif key == "samesite" and separator: options["same_site"] = raw_value.lower()
            elif key == "expires" and separator: options["expires"] = self._parse_expires(raw_value)
            elif key == "max-age" and separator: options["max_age"] = self._parse_max_age(raw_value)
        return self._store(parsed_url, name, value, **options)

    def header_for(self, url): return self._for_url(url, include_http_only=True)
    def document_for(self, url): return self._for_url(url, include_http_only=False)

    def _for_url(self, url, *, include_http_only):
        parsed = urlsplit(url)
        if not self._allows_cookies(parsed): return ""
        host, path, secure = (parsed.hostname or "").lower(), parsed.path or "/", parsed.scheme in ("https", "wss")
        with self._lock:
            self._purge_expired()
            matched = [
                cookie for cookie in self._cookies.values()
                if (include_http_only or not cookie.http_only)
                and self._domain_matches(host, cookie.domain, cookie.host_only)
                and self._path_matches(path, cookie.path)
                and (not cookie.secure or secure)
            ]
            matched.sort(key=lambda cookie: (-len(cookie.path), cookie.creation_index))
            return "; ".join(f"{cookie.name}={cookie.value}" for cookie in matched)

    def clear(self):
        with self._lock: self._cookies.clear()


__all__ = ["Cookie", "CookieJar"]
