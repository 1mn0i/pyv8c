"""Which element has focus, and the events that moving it fires.

The parts of Chromium's model this DOM keeps, each one measured against
Chrome 153 rather than read off the specification:

* ``document.activeElement`` is the body before anything was focused, and
  focusing the body again is not a transition -- nothing is dispatched;
* a focusable area is a form control that is neither disabled nor
  ``type=hidden``, an ``a``/``area`` carrying ``href``, an ``iframe``, or any
  element with a ``tabindex`` attribute.  ``focus()`` on anything else does
  nothing, which is what Chrome does for a plain ``div``;
* a click focuses the nearest focusable ancestor of the element it lands on,
  and clears focus when there is none: clicking a plain ``div`` moves focus
  off an input;
* focusing happens on ``mousedown``, so a ``preventDefault`` there leaves
  focus where it was.

Only the element itself is tracked: there is no tab order, no shadow DOM, and
no ``:focus`` styling, so nothing here moves focus without being asked to.
"""

import pyv8

from .events import FocusEvent

#: Local names that take focus without needing a ``tabindex``.
_FOCUSABLE_TAGS = frozenset(("button", "iframe", "select", "textarea"))

#: Local names whose ``href`` makes them focusable.
_LINK_TAGS = frozenset(("a", "area"))


def _is_form_control(element):
    return getattr(element, "localName", "") in ("button", "input", "select", "textarea")


def _disabled(element):
    return _is_form_control(element) and "disabled" in getattr(element, "attributes", {})


def focusable(element):
    """Whether ``focus()`` does anything on *element*, as Chrome decides it.

    A disconnected element is not focusable, and neither is a disabled form
    control even when it carries a ``tabindex``: disabled controls are inert.
    """
    if element is None or element is pyv8.Null or not getattr(element, "isConnected", False):
        return False
    if _disabled(element):
        return False
    attributes = getattr(element, "attributes", {})
    if "tabindex" in attributes:
        return True
    tag = getattr(element, "localName", "")
    if tag == "input":
        return str(element.get_type()) != "hidden"
    if tag in _FOCUSABLE_TAGS:
        return True
    if tag in _LINK_TAGS:
        return bool(attributes.get("href"))
    return False


def active_element(document):
    """What ``document.activeElement`` reports.

    The focused element while it is still in the document; otherwise the body,
    the root element, or ``null`` for a document that was never given one.
    """
    focused = getattr(document, "_focused_element", None)
    if focused is not None and getattr(focused, "isConnected", False):
        return focused
    for fallback in (document.get_body(), document.get_documentElement()):
        if fallback is not None and fallback is not pyv8.Null:
            return fallback
    return pyv8.Null


def _fire(target, event_type, *, bubbles, related):
    event = FocusEvent(event_type, {"bubbles": bubbles, "cancelable": False})
    if related is not None and related is not pyv8.Null:
        event.relatedTarget = related
    event._trusted = True
    target.fn_dispatchEvent(event)


def _move_focus(document, element):
    """Give *document* a new focused element, firing the transition.

    ``blur``/``focusout`` go to the element that had focus and ``focus``/
    ``focusin`` to the one taking it; a document that had nothing focused
    dispatches neither pair's first half, which is why focusing the first
    element of a page reports only ``focus`` and ``focusin``.
    """
    previous = getattr(document, "_focused_element", None)
    if previous is element:
        return True
    document._focused_element = element
    if previous is not None:
        _fire(previous, "blur", bubbles=False, related=element)
        _fire(previous, "focusout", bubbles=True, related=element)
    if element is not None:
        _fire(element, "focus", bubbles=False, related=previous)
        _fire(element, "focusin", bubbles=True, related=previous)
    return True


def focus(element):
    """Focus *element* if it is a focusable area; returns whether it took focus."""
    document = getattr(element, "ownerDocument", None)
    if document is None or not focusable(element):
        return False
    return _move_focus(document, element)


def blur(document):
    """Clear the document's focus, as ``blur()`` on the focused element would."""
    if getattr(document, "_focused_element", None) is None:
        return False
    return _move_focus(document, None)


def focus_on_mousedown(node):
    """The focus a press lands on: the nearest focusable ancestor, else nothing.

    Chrome walks up from the pressed element and focuses the first focusable
    area it finds; finding none moves focus off whatever had it.
    """
    document = getattr(node, "ownerDocument", None)
    if document is None:
        return False
    candidate = node
    while candidate is not None and candidate is not pyv8.Null:
        if focusable(candidate):
            return _move_focus(document, candidate)
        candidate = getattr(candidate, "parentElement", None)
    return _move_focus(document, None)


__all__ = ["active_element", "blur", "focus", "focus_on_mousedown", "focusable"]
