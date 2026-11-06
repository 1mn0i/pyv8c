"""Which element a point lands on, and the stack of elements behind it.

The boxes ``getBoundingClientRect`` reports are the ones hit-tested, so a point
outside the viewport reports ``null`` where Chrome's ``elementFromPoint`` does.
``visibility:hidden``/``pointer-events:none`` skip the element itself but keep
its descendants reachable; the topmost element wins and the rest of the stack is
deepest first.  Measured against Chrome 153.
"""

from pyv8c import pyv8

from .element import Element
from .layout import Box, _child_elements, layout_snapshot, positioned_box


#: Viewport used when a document has no environment to ask, matching ``dom.layout``.
_DEFAULT_VIEWPORT = (1280, 720)

_COLLAPSED_VISIBILITY = ("hidden", "collapse")


def border_box(element):
    """The element's box in viewport coordinates, as ``getBoundingClientRect`` reports it."""
    return positioned_box(element)


def _inside(box, x, y):
    """Whether the point is in the box, half-open as Chromium tests it."""
    return (box.left <= x < box.left + box.width
            and box.top <= y < box.top + box.height)


def contains(element, x, y):
    """Whether the point is inside *element*'s box, positioned as the hit test reads it."""
    return _inside(border_box(element), x, y)


def viewport_size(document):
    """``(width, height)`` of the document's viewport."""
    services = getattr(document, "_services", None)
    window = getattr(getattr(services, 'document_services', None), 'window', None)
    if window is not None:
        return window.innerWidth, window.innerHeight
    realm = getattr(services, "realm_services", None)
    viewport = getattr(getattr(realm, "environment", None), "viewport", None)
    return (
        getattr(viewport, "width", None) or _DEFAULT_VIEWPORT[0],
        getattr(viewport, "height", None) or _DEFAULT_VIEWPORT[1],
    )


def _hit_testable(style):
    """Whether a box is a hit-test target at all, before its children are tried."""
    return (
        style.get("visibility", "visible") not in _COLLAPSED_VISIBILITY
        and style.get("pointer-events", "auto") != "none"
    )


def _collect(element, boxes, styles, x, y, found):
    """Append the elements under the point in *element*'s subtree, deepest first.
    Children are walked last-to-first so a later sibling stacks above an earlier."""
    box = boxes.get(element)
    if box is None:
        return
    hit = _hit_testable(styles.get(element, {})) and _inside(box, x, y)
    for child in reversed(_child_elements(element)):
        if isinstance(child, Element) and styles.get(child, {}).get("display") != "none":
            _collect(child, boxes, styles, x, y, found)
    if hit:
        found.append(element)


def elements_from_point(document, x, y, *, deep=False):
    """The elements under a point, topmost first, as ``elementsFromPoint`` reports them."""
    width, height = viewport_size(document)
    if not (0 <= x < width and 0 <= y < height):
        return []
    root = document.get_documentElement()
    if root is None or root is pyv8.Null:
        return []
    boxes, styles = layout_snapshot(document)
    found = []
    _collect(root, boxes, styles, x, y, found)
    if not any(node is root for node in found):
        # Chromium reports the root for every point in the viewport, whatever
        # its content box measured.
        found.append(root)
    if not deep:
        retargeted = []
        for node in found:
            tree = node.root_node()
            while getattr(tree, '_host', None) is not None:
                node = tree._host
                tree = node.root_node()
            if node not in retargeted:
                retargeted.append(node)
        return retargeted
    return found


def element_from_point(document, x, y):
    """The topmost element under a point, or ``None`` when the point is outside."""
    stack = elements_from_point(document, x, y)
    return stack[0] if stack else None


def deep_element_from_point(document, x, y):
    """The internal event target before document-level shadow retargeting."""
    stack = elements_from_point(document, x, y, deep=True)
    return stack[0] if stack else None

__all__ = [
    "Box", "border_box", "contains", "element_from_point", "elements_from_point",
    "viewport_size",
]
