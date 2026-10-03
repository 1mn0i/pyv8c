"""The HTML local-name to interface-class registry.  It sits above the element
modules, which only need the node model below."""

from .html_canvas import HTMLCanvasElement
from .html_elements import (
    HTMLAnchorElement,
    HTMLButtonElement,
    HTMLImageElement,
    HTMLLabelElement,
    HTMLLinkElement,
    HTMLMetaElement,
    HTMLScriptElement,
    HTMLStyleElement,
    HTMLTableCellElement,
    HTMLTableElement,
    HTMLTableRowElement,
    HTMLTemplateElement,
    HTMLUnknownElement,
    _html_class,
)
from .html_form import HTMLFormElement
from .html_iframe import HTMLIFrameElement
from .html_input import (
    HTMLInputElement,
    HTMLOptionElement,
    HTMLSelectElement,
    HTMLTextAreaElement,
)
from .html_media import HTMLAudioElement, HTMLMediaElement, HTMLVideoElement

#: Interface name -> implementation class, for every modelled interface.
HTML_CLASSES = {
    **{name: _html_class(name) for name in (
        "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement",
        "HTMLSpanElement")},
    # Interfaces with members of their own carry a hand-written class.
    "HTMLFormElement": HTMLFormElement,
    "HTMLButtonElement": HTMLButtonElement,
    "HTMLLabelElement": HTMLLabelElement,
    "HTMLMediaElement": HTMLMediaElement,
    "HTMLAudioElement": HTMLAudioElement,
    "HTMLVideoElement": HTMLVideoElement,
    "HTMLTableElement": HTMLTableElement,
    "HTMLTableRowElement": HTMLTableRowElement,
    "HTMLTableCellElement": HTMLTableCellElement,
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

#: Local name -> implementation class for every modelled tag, written out because a
#: tag name is not always its interface's lower-cased middle (``img``/``td``/``tr``).
TAG_CLASSES = {
    "a": HTML_CLASSES["HTMLAnchorElement"],
    "audio": HTML_CLASSES["HTMLAudioElement"],
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
    "label": HTML_CLASSES["HTMLLabelElement"],
    "link": HTML_CLASSES["HTMLLinkElement"],
    "meta": HTML_CLASSES["HTMLMetaElement"],
    "option": HTML_CLASSES["HTMLOptionElement"],
    "script": HTML_CLASSES["HTMLScriptElement"],
    "select": HTML_CLASSES["HTMLSelectElement"],
    "span": HTML_CLASSES["HTMLSpanElement"],
    "style": HTML_CLASSES["HTMLStyleElement"],
    "table": HTML_CLASSES["HTMLTableElement"],
    "td": HTML_CLASSES["HTMLTableCellElement"],
    "th": HTML_CLASSES["HTMLTableCellElement"],
    "template": HTML_CLASSES["HTMLTemplateElement"],
    "textarea": HTML_CLASSES["HTMLTextAreaElement"],
    "tr": HTML_CLASSES["HTMLTableRowElement"],
    "video": HTML_CLASSES["HTMLVideoElement"],
}


def class_for_tag(tag):
    """The implementation class for an HTML local name: a modelled tag its own
    interface, everything else (custom-element names, unknown tags) the
    :class:`HTMLUnknownElement` Chromium exposes.
    """
    return TAG_CLASSES.get(str(tag).lower(), HTML_CLASSES["HTMLUnknownElement"])


def class_for_local_name(local_name):
    """The class for a local name *as written*: ``createElementNS`` does not lower-case
    the name it was given, so ``DIV`` is an unknown element to it.
    """
    return TAG_CLASSES.get(str(local_name), HTML_CLASSES["HTMLUnknownElement"])


#: The SVG local names whose interface is not their own capitalised name.
_SVG_NAMES = {"svg": "SVGSVGElement", "tspan": "SVGTSpanElement"}


def svg_interface_name(local_name):
    """The interface Chrome gives an SVG local name, matched case for case.

    Whether the realm declares it is its question: Chrome answers ``SVGElement`` for a
    name it has none for, and so does the caller when this one is not in the baseline.
    The filter primitives keep a capital ``FE`` rather than the name's own ``fe``.
    """
    match = _SVG_NAMES.get(local_name)
    if match is not None:
        return match
    capital = local_name[:2].upper() + local_name[2:] if local_name.startswith("fe") \
        else local_name[:1].upper() + local_name[1:]
    return "SVG" + capital + "Element"


__all__ = ["HTML_CLASSES", "TAG_CLASSES", "class_for_local_name", "class_for_tag",
           "svg_interface_name"]
