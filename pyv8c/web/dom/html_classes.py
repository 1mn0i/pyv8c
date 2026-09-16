"""The HTML local-name to interface-class registry.

It sits above the element modules because the registry needs classes from all
of them, while those modules only need the node model below.
"""

from .html_canvas import HTMLCanvasElement
from .html_elements import (
    HTMLAnchorElement,
    HTMLIFrameElement,
    HTMLImageElement,
    HTMLLinkElement,
    HTMLMetaElement,
    HTMLScriptElement,
    HTMLStyleElement,
    HTMLTemplateElement,
    HTMLUnknownElement,
    _html_class,
)
from .html_input import (
    HTMLInputElement,
    HTMLOptionElement,
    HTMLSelectElement,
    HTMLTextAreaElement,
)

#: Interface name -> implementation class, for every modelled interface.
HTML_CLASSES = {
    **{name: _html_class(name) for name in (
        "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement",
        "HTMLFormElement", "HTMLMediaElement",
        "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement",
        "HTMLButtonElement")},
    # Interfaces with members of their own carry a hand-written class.
    "HTMLAnchorElement": HTMLAnchorElement,
    "HTMLTextAreaElement": HTMLTextAreaElement,
    "HTMLOptionElement": HTMLOptionElement,
    "HTMLSelectElement": HTMLSelectElement,
    "HTMLUnknownElement": HTMLUnknownElement,
    "HTMLImageElement": HTMLImageElement, "HTMLScriptElement": HTMLScriptElement, "HTMLCanvasElement": HTMLCanvasElement,
    "HTMLLinkElement": HTMLLinkElement, "HTMLStyleElement": HTMLStyleElement,
    "HTMLMetaElement": HTMLMetaElement, "HTMLTemplateElement": HTMLTemplateElement,
    "HTMLIFrameElement": HTMLIFrameElement, "HTMLInputElement": HTMLInputElement,
    # Chromium also resolves the historical lowercase spelling.
    "HTMLIframeElement": HTMLIFrameElement,
}

#: Local name -> implementation class for every modelled tag.  Written out
#: rather than derived from ``HTML_CLASSES`` because a tag name is not always
#: the lower-cased middle of its interface name (``img``/``td``/``tr``), and
#: because ``HTMLMediaElement`` and ``HTMLUnknownElement`` are not tags.
TAG_CLASSES = {
    "a": HTML_CLASSES["HTMLAnchorElement"],
    "body": HTML_CLASSES["HTMLBodyElement"],
    "button": HTML_CLASSES["HTMLButtonElement"],
    "canvas": HTML_CLASSES["HTMLCanvasElement"],
    "div": HTML_CLASSES["HTMLDivElement"],
    "form": HTML_CLASSES["HTMLFormElement"],
    "head": HTML_CLASSES["HTMLHeadElement"],
    "html": HTML_CLASSES["HTMLHtmlElement"],
    "iframe": HTML_CLASSES["HTMLIFrameElement"],
    "img": HTML_CLASSES["HTMLImageElement"],
    "input": HTML_CLASSES["HTMLInputElement"],
    "link": HTML_CLASSES["HTMLLinkElement"],
    "meta": HTML_CLASSES["HTMLMetaElement"],
    "option": HTML_CLASSES["HTMLOptionElement"],
    "script": HTML_CLASSES["HTMLScriptElement"],
    "select": HTML_CLASSES["HTMLSelectElement"],
    "span": HTML_CLASSES["HTMLSpanElement"],
    "style": HTML_CLASSES["HTMLStyleElement"],
    "table": HTML_CLASSES["HTMLTableElement"],
    "td": HTML_CLASSES["HTMLTableCellElement"],
    "template": HTML_CLASSES["HTMLTemplateElement"],
    "textarea": HTML_CLASSES["HTMLTextAreaElement"],
    "tr": HTML_CLASSES["HTMLTableRowElement"],
}


def class_for_tag(tag):
    """The implementation class for an HTML local name.

    Every modelled tag maps to its own interface; each remaining local name --
    the obsolete tags (``bgsound``, ``blink``, ``isindex``, ``keygen``,
    ``multicol``, ``nextid``, ``spacer``), custom element names and tags this
    DOM does not model -- resolves to :class:`HTMLUnknownElement`, which is
    what Chromium exposes for them.
    """
    return TAG_CLASSES.get(str(tag).lower(), HTML_CLASSES["HTMLUnknownElement"])


__all__ = ["HTML_CLASSES", "TAG_CLASSES", "class_for_tag"]
