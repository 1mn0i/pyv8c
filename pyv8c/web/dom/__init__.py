"""DOM events, nodes, elements, parsing, and selectors.

The implementations live in the sibling modules -- :mod:`dom.nodes` (``Node``,
``DocumentFragment``, ``Attr``), :mod:`dom.character_data` (``CharacterData``,
``Text``, ``Comment``, ``DocumentType``), :mod:`dom.document` (``Document``),
:mod:`dom.html_elements` / :mod:`dom.html_input` / :mod:`dom.html_canvas` (the
HTML tag classes) with :mod:`dom.html_classes` mapping local names onto them,
plus :mod:`dom.mixins`, :mod:`dom.utils`, :mod:`dom.collections` and
:mod:`dom.events`.  This package re-exports them so ``pyv8c.web.dom`` is the
single flat DOM namespace.
"""

from .character_data import CharacterData, Comment, DocumentType, Text
from .collections import (
    DOMTokenList,
    HTMLAllCollection,
    HTMLCollection,
    NamedNodeMap,
    NodeList,
)
from .document import Document
from .events import (
    CustomEvent,
    Event,
    EventTarget,
    KeyboardEvent,
    MouseEvent,
    UIEvent,
)
from .html_classes import HTML_CLASSES
from .html_elements import (
    Element,
    HTMLElement,
    HTMLIFrameElement,
    HTMLImageElement,
    HTMLLinkElement,
    HTMLMetaElement,
    HTMLScriptElement,
    HTMLStyleElement,
    HTMLTemplateElement,
)
from .nodes import Attr, DocumentFragment, Node

__all__ = [
    "NodeList", "HTMLCollection", "HTMLAllCollection", "DOMTokenList",
    "Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "EventTarget",
    "Node", "DocumentFragment", "Attr", "NamedNodeMap", "CharacterData", "Text",
    "Comment", "DocumentType", "Element", "HTMLElement", "HTML_CLASSES",
    "HTMLImageElement", "HTMLScriptElement", "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement",
    "HTMLTemplateElement", "HTMLIFrameElement", "Document",
]
