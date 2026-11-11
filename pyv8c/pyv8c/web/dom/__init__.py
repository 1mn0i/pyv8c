"""The flat DOM namespace: this package re-exports the sibling modules."""

__all__ = [
    "NodeList", "HTMLCollection", "HTMLAllCollection", "DOMTokenList",
    "Event", "UIEvent", "MouseEvent", "KeyboardEvent", "CustomEvent", "EventTarget",
    "Node", "DocumentFragment", "Attr", "NamedNodeMap", "CharacterData", "Text",
    "Comment", "DocumentType", "Element", "HTMLElement", "HTML_CLASSES",
    "HTMLImageElement", "HTMLScriptElement", "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement",
    "HTMLTemplateElement", "HTMLIFrameElement",
    "HTMLMediaElement", "HTMLAudioElement", "HTMLVideoElement", "Document",
]


_EXPORTS = {
    **{name: (".character_data", name) for name in ("CharacterData", "Comment", "DocumentType", "Text")},
    **{name: (".collections", name) for name in ("DOMTokenList", "HTMLAllCollection", "HTMLCollection", "NamedNodeMap", "NodeList")},
    **{name: (".events", name) for name in ("CustomEvent", "Event", "EventTarget", "KeyboardEvent", "MouseEvent", "UIEvent")},
    **{name: (".nodes", name) for name in ("Attr", "DocumentFragment", "Node")},
    "Document": (".document", "Document"),
    "Element": (".element", "Element"),
    "HTML_CLASSES": ("..html.registry", "HTML_CLASSES"),
    "HTMLElement": ("..html.elements", "HTMLElement"),
    "HTMLImageElement": ("..html.image", "HTMLImageElement"),
    "HTMLLinkElement": ("..html.links", "HTMLLinkElement"),
    **{name: ("..html.document_elements", name) for name in (
        "HTMLMetaElement", "HTMLScriptElement", "HTMLStyleElement", "HTMLTemplateElement")},
    "HTMLIFrameElement": ("..html.iframe", "HTMLIFrameElement"),
    **{name: ("..html.media", name) for name in
       ("HTMLAudioElement", "HTMLMediaElement", "HTMLVideoElement")},
}
def __getattr__(name):
    from importlib import import_module
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
