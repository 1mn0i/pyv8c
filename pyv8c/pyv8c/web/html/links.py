"""HTML link and anchor elements."""
from urllib.parse import urlsplit, urlunsplit

from ...idl import interface
from ...foundation.arguments import dom_string
from ...foundation.urls import credentials_netloc, origin_string
from ...web.dom.collections import DOMTokenList, HYPERLINK_REL_TOKENS, LINK_REL_TOKENS
from .utils import url_value, set_url_value
from .elements import HTMLElement


#: The destinations ``link.as`` may report; anything else reads back as empty.
LINK_AS_VALUES = frozenset({
    "audio", "audioworklet", "document", "embed", "fetch", "font", "frame", "iframe",
    "image", "json", "manifest", "object", "paintworklet", "report", "script",
    "serviceworker", "sharedworker", "style", "track", "video", "webidentity", "worker",
    "xslt",
})


@interface('HTMLLinkElement', 'HTMLElement')
class HTMLLinkElement(HTMLElement):
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", LINK_REL_TOKENS)
        return value
    def get_href(self): return url_value(self, "href")
    def set_href(self, value):
        set_url_value(self, "href", value)
        self._restart_resource()
    #: ``rel`` and ``as`` decide whether an ``href`` is a resource at all.
    _RESOURCE_ATTRIBUTES = ("href", "rel", "as")
    def _attribute_changed_here(self, name, old, new):
        super()._attribute_changed_here(name, old, new)
        if name in self._RESOURCE_ATTRIBUTES:
            self._restart_resource()
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value):
        value = dom_string(value)
        self.attributes['rel'] = str(value)
        self._restart_resource()
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)
    def get_as(self):
        value = str(self.attributes.get("as", "")).lower()
        return value if value in LINK_AS_VALUES else ""
    def set_as(self, value):
        value = dom_string(value)
        self.attributes['as'] = str(value)
        self._restart_resource()


@interface('HTMLAnchorElement', 'HTMLElement')
class HTMLAnchorElement(HTMLElement):
    """``<a>``: the URL decomposition members ``href`` carries.  Assigning one parses
    the current href, replaces that component and writes the result back, per URLUtils."""

    def _href(self):
        """The href every URL member below is derived from."""
        return url_value(self, "href")

    def _parts(self):
        return urlsplit(self._href())

    def _set_component(self, **changes):
        self.attributes['href'] = urlunsplit(self._parts()._replace(**changes))

    def get_href(self): return self._href()
    def set_href(self, value): set_url_value(self, "href", value)
    def get_origin(self): return origin_string(self._href()) if self._href() else ""
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ":"
    def set_protocol(self, value): self._set_component(scheme=str(dom_string(value)).rstrip(":"))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        self._set_credentials(username=str(dom_string(value)), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        self._set_credentials(username=self.get_username(), password=str(dom_string(value)))
    def _set_credentials(self, *, username, password):
        parts = self._parts()
        self._set_component(
            netloc=credentials_netloc(parts.hostname or "", parts.port, username, password))
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value): self._set_component(netloc=str(dom_string(value)))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._set_component(netloc=credentials + str(dom_string(value)) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        value = str(dom_string(value))
        self._set_component(netloc=credentials + (parts.hostname or "") + ((":" + value) if value else ""))
    def get_pathname(self):
        parts = self._parts()
        return parts.path or ("/" if parts.netloc else "")
    def set_pathname(self, value): self._set_component(path=str(dom_string(value)) or "/")
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value): self._set_component(query=str(dom_string(value)).lstrip("?"))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value): self._set_component(fragment=str(dom_string(value)).lstrip("#"))
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(dom_string(value))
    def get_target(self): return self.attributes.get("target", "")
    def set_target(self, value): self.attributes["target"] = str(dom_string(value))
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", HYPERLINK_REL_TOKENS)
        return value
    def fn_toString(self, *_extra): return self._href()

    def _activatable(self):
        """A link with an ``href`` navigates when clicked; a ``download`` link is
        declined (Chromium saves it, and this DOM has nowhere to save it)."""
        return bool(self._href()) and "download" not in self.attributes

    def _run_activation(self, event=None):
        """Follow the link via the location object, so a ``#fragment`` link stays a
        same-document navigation instead of a reload, as Chromium treats it."""
        href = self._href()
        if not href:
            return
        window = getattr(getattr(self._services, "document_services", None), "window", None)
        if window is not None:
            window.location.set_href(href)
