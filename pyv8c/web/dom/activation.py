"""What a click does to an element besides dispatching the event.

A click is not only an event: HTML gives some elements an activation behavior
that runs after it.  The rules, each measured against Chrome 153:

* the ``click`` event comes first and the behavior runs only when no listener
  cancelled it;
* the behavior belongs to the nearest ancestor that has one, so a click on a
  ``span`` inside a ``button`` submits that button's form and a click on a
  ``label`` reaches the control it labels;
* a disabled form control is inert: ``click()`` on it dispatches nothing at
  all, and the mouse reaches it with pointer events but without
  ``mousedown``/``mouseup``/``click`` or focus.

The behaviors themselves live on the elements (:meth:`Element._run_activation`),
where Blink keeps them; this module is the walk that finds them and the
synthetic click ``element.click()`` runs.
"""

import pyv8

#: Local names that a ``disabled`` attribute makes inert.
_FORM_CONTROLS = frozenset((
    "button", "fieldset", "input", "optgroup", "option", "select", "textarea",
))


def is_disabled_form_control(element):
    """Whether *element* is a form control switched off by its own attribute."""
    return (
        getattr(element, "localName", "") in _FORM_CONTROLS
        and "disabled" in getattr(element, "attributes", {})
    )


def user_activated(event):
    """Whether *event* is a user gesture, which a submission reports as
    ``Sec-Fetch-User: ?1``.

    Measured on Chrome 153: a trusted click -- what the host's input machine
    delivers -- is a user activation, while a page's own ``element.click()`` is
    not.  Both still submit, and both still send the pressed button's entry.
    """
    return bool(getattr(event, "_trusted", False))


def accepts_mouse_events(element):
    """Whether the mouse reaches *element* with its compatibility events.

    A disabled form control still receives the pointer events -- a page can
    hover it -- but Chromium dispatches neither the mouse events nor focus.
    """
    return element is not None and element is not pyv8.Null and not is_disabled_form_control(element)


def activation_target(node):
    """The nearest element at or above *node* whose click runs a behavior."""
    candidate = node
    while candidate is not None and candidate is not pyv8.Null:
        activatable = getattr(candidate, "_activatable", None)
        if activatable is not None and activatable():
            return candidate
        candidate = getattr(candidate, "parentElement", None)
    return None


def run_activation(node, event=None):
    """Run the activation behavior a click on *node* reaches; reports whether one ran.

    *event* is the click that got here, which a behavior may pass on: a
    ``label`` repeats the press on the control it labels, and how it repeats it
    depends on how it was itself clicked.
    """
    target = activation_target(node)
    if target is None:
        return False
    target._run_activation(event)
    return True


def click_element(element):
    """The synthetic click activation steps ``element.click()`` runs.

    Chrome builds an untrusted, uncancelled-by-default ``click`` at the origin
    with ``detail`` 0 and then runs the behavior; a cancelled event stops
    there, and a disabled control stops before the event.
    """
    if not accepts_mouse_events(element):
        return False
    from .events import MouseEvent
    event = MouseEvent("click", {
        "bubbles": True, "cancelable": True, "detail": 0,
        "button": 0, "buttons": 0, "clientX": 0, "clientY": 0,
    })
    element.fn_dispatchEvent(event)
    if not event.defaultPrevented:
        run_activation(element, event)
    return True


def repeat_click(element, source):
    """Run a click on *element* that repeats *source*, trusted, as a label does.

    Chromium's label forwards the press it received rather than a fresh
    synthetic one: the control's click carries the same coordinates, the same
    click count and the trust of the event the label was clicked with.
    """
    from .events import MouseEvent
    repeated = MouseEvent("click", {
        "bubbles": True, "cancelable": True,
        "detail": getattr(source, "detail", 0),
        "button": getattr(source, "button", 0),
        "buttons": 0,
        "clientX": getattr(source, "clientX", 0),
        "clientY": getattr(source, "clientY", 0),
    })
    repeated._trusted = bool(getattr(source, "_trusted", False))
    element.fn_dispatchEvent(repeated)
    if not repeated.defaultPrevented:
        run_activation(element, repeated)
    return True


__all__ = [
    "accepts_mouse_events", "activation_target", "click_element",
    "is_disabled_form_control", "repeat_click", "run_activation", "user_activated",
]
