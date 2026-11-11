"""Helpers shared by HTML element implementations."""

from urllib.parse import urljoin

from ...foundation.arguments import dom_string
from ..dom.element import Element


def form_owner(control):
    """Resolve the form associated with a form-associated control."""
    if control.isConnected and 'form' in control.attributes:
        identifier = str(control.attributes.get('form', ''))
        lookup = getattr(control.root_node(), 'fn_getElementById', None)
        owner = lookup(identifier) if identifier and lookup else None
        return owner if getattr(owner, 'localName', None) == 'form' else None
    node = control.parentElement
    while isinstance(node, Element):
        if node.localName == 'form':
            return node
        node = node.parentElement
    return None


def document_url(element):
    """The URL a relative attribute on *element* resolves against."""
    return str(getattr(getattr(element, "ownerDocument", None), "url", "") or "")


def url_value(element, name):
    """Resolve a nonempty URL attribute against the element's document."""
    raw = str(element.attributes.get(name, ""))
    return urljoin(document_url(element), raw) if raw else ""


def set_url_value(element, name, value):
    element.attributes[name] = str(dom_string(value))
