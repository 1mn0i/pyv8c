"""Which element a point lands on, and the stack of elements behind it.

The geometry is one layout pass read twice: the boxes a walk from the root
builds (:func:`dom.layout.positioned_box`) are the ones ``getBoundingClientRect``
reports, so a point a page computes from a rect and a point a host hit tests
agree on the element, and a point outside the viewport reports ``null`` exactly
where Chrome's ``elementFromPoint`` does.

What is modelled, and what is deliberate:

* ``display:none`` removes the subtree; ``visibility:hidden`` and
  ``pointer-events:none`` skip the element itself but keep testing its
  descendants, so a child that turns either back on is still reachable -- all
  three measured against Chrome 153;
* the topmost element wins and the rest of the stack is returned deepest
  first, which is the paint order this DOM has: later siblings paint over
  earlier ones, children over their parents;
* every child is tested even when its parent's box misses the point, because a
  box with no height still has children with one;
* the box model carries no positioning -- ``left`` is always 0, a block
  element is as wide as its container -- and there is no scrolling, so a hit
  test is only as precise as the layout behind it.
"""

import pyv8

from .html_elements import Element
from .layout import Box, computed_style, layout, positioned_box

#: Viewport used when a document has no environment to ask, matching
#: :func:`dom.layout.layout`.
_DEFAULT_VIEWPORT = (1280, 720)

_COLLAPSED_VISIBILITY = ("hidden", "collapse")


def border_box(element):
    """The element's box in viewport coordinates, as ``getBoundingClientRect`` reports it."""
    return positioned_box(element)


def _inside(box, x, y):
    """Whether the point is in the box, half-open as Chromium tests it."""
    return 0 <= x < box.width and box.top <= y < box.top + box.height


def contains(element, x, y):
    """Whether the point is inside *element*'s box, positioned as the hit test reads it."""
    return _inside(border_box(element), x, y)


def viewport_size(document):
    """``(width, height)`` of the document's viewport."""
    services = getattr(document, "_services", None)
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


def _collect(element, boxes, x, y, found):
    """Append the elements under the point in *element*'s subtree, deepest first.

    Children are walked last-to-first so a later sibling is stacked above an
    earlier one.
    """
    box = boxes.get(element)
    if box is None:
        return
    hit = _hit_testable(computed_style(element)) and _inside(box, x, y)
    for child in reversed(list(getattr(element, "_children", ()))):
        if isinstance(child, Element) and computed_style(child).get("display") != "none":
            _collect(child, boxes, x, y, found)
    if hit:
        found.append(element)


def elements_from_point(document, x, y):
    """The elements under a point, topmost first, as ``elementsFromPoint`` reports them."""
    width, height = viewport_size(document)
    if not (0 <= x < width and 0 <= y < height):
        return []
    root = document.get_documentElement()
    if root is None or root is pyv8.Null:
        return []
    boxes = {}
    layout(root, collected=boxes)
    found = []
    _collect(root, boxes, x, y, found)
    if not any(node is root for node in found):
        # Chromium reports the root for every point in the viewport, whatever
        # its content box measured.
        found.append(root)
    return found


def element_from_point(document, x, y):
    """The topmost element under a point, or ``None`` when the point is outside."""
    stack = elements_from_point(document, x, y)
    return stack[0] if stack else None


__all__ = [
    "Box", "border_box", "contains", "element_from_point", "elements_from_point",
    "viewport_size",
]
