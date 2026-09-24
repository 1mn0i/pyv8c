"""The page's mouse: where it is, which buttons are down, and what that fires.

A mouse is a state machine rather than a single "click" call: moving crosses
element boundaries, pressing focuses, and a press and a release on different
elements still make one ``click`` at their nearest common ancestor.  Every
sequence here was observed on the wire from Chrome 153 instead of derived from
the specification:

* a move onto a new element fires ``pointerout``/``pointerover`` and then
  ``mouseout``/``mouseover``, followed by ``pointermove`` and ``mousemove``;
* a press is ``pointerdown``, ``mousedown`` and then focus, in that order --
  and a cancelled ``mousedown`` leaves focus where it was;
* a release is ``pointerup``, ``mouseup`` and then ``click``, whose activation
  behavior runs when no listener cancelled the event; a second click close
  enough in time and place reports ``detail`` 2 and is followed by ``dblclick``;
* a disabled form control receives the pointer events and none of the others,
  which is how the mouse and ``click()`` both treat it;
* ``mouseenter``/``mouseleave`` and their pointer twins do not propagate, so
  only listeners on the element itself ever see them.

The controller owns the position, the button mask and the click counter, and
every entry point runs on the page's owner thread like the rest of the DOM
surface.
"""

from __future__ import annotations

import time

import pyv8

from ..foundation.errors import InvalidStateError
from ..web.dom.activation import accepts_mouse_events, run_activation
from ..web.dom.focus import focus_on_mousedown
from ..web.dom.hit_testing import border_box, element_from_point

#: The DOM's button index and the ``buttons`` mask bit, per button name.
BUTTON_INDEX = {"left": 0, "middle": 1, "right": 2}
BUTTON_BIT = {"left": 1, "middle": 4, "right": 2}

#: The pointer the mouse is reported as: the primary mouse pointer, as Chrome
#: describes it on every pointer event it derives from mouse input.
POINTER_ID = 1

#: A second click within this many seconds and this many pixels, on the same
#: element, counts as another click of the same run -- Chromium's own window
#: for reporting ``detail`` 2 and firing ``dblclick``.
MULTI_CLICK_INTERVAL = 0.5
MULTI_CLICK_DISTANCE = 4.0


def _ancestors(node):
    """*node* and its ancestors, itself first."""
    chain = []
    current = node
    while current is not None:
        chain.append(current)
        current = getattr(current, "parentElement", None)
    return chain


def _leaving(previous, target):
    """The elements a leave event reaches, the element being left first."""
    staying = _ancestors(target) if target is not None else []
    return [node for node in _ancestors(previous)
            if not any(node is other for other in staying)]


def _entering(previous, target):
    """The elements an enter event reaches, the outermost one first."""
    leaving = _ancestors(previous) if previous is not None else []
    chain = [node for node in _ancestors(target)
             if not any(node is other for other in leaving)]
    return list(reversed(chain))


def _common_ancestor(first, second):
    """The nearest element containing both, which is where a press-release clicks."""
    if first is None or second is None:
        return first or second
    below = _ancestors(second)
    for node in _ancestors(first):
        if any(node is other for other in below):
            return node
    return None


def describe(element):
    """A short ``<tag id="...">`` label for error messages."""
    if element is None:
        return "nothing"
    tag = getattr(element, "localName", None) or "node"
    identifier = getattr(element, "attributes", {}).get("id")
    return f"<{tag} id={identifier!r}>" if identifier else f"<{tag}>"


class MouseInput:
    """One page's mouse, reached as ``page.mouse``.

    ``position`` and ``pressed`` are plain reads of host-side state and may be
    read from any thread; everything that dispatches events marshals onto the
    page's owner thread, so it may be called from any thread too.
    """

    def __init__(self, page):
        self._page = page
        self._x = 0.0
        self._y = 0.0
        self._bits = 0
        self._hovered = None
        self._pressed = None
        self._press_count = 1
        self._count = 0
        self._last_click = None

    # -- state -------------------------------------------------------------

    @property
    def position(self) -> tuple[float, float]:
        """The mouse position as ``(x, y)`` in viewport coordinates."""
        return (self._x, self._y)

    @property
    def pressed(self) -> frozenset[str]:
        """The buttons currently held down, as a set of names."""
        return frozenset(name for name, bit in BUTTON_BIT.items() if self._bits & bit)

    # -- moving and pressing ------------------------------------------------

    def move(self, x: float, y: float, *, steps: int = 1) -> None:
        """Move the mouse to ``(x, y)``, moving through *steps* positions.

        Each position fires the boundary events a crossing element makes and
        then the two move events, so a page watching ``mouseover`` sees the
        same sequence a real cursor produces.

        :param x: viewport x coordinate.
        :param y: viewport y coordinate.
        :param steps: how many intermediate positions to report; ``1`` jumps.
        """
        return self._call(self._move_to, float(x), float(y), max(1, int(steps)))

    def down(self, *, button: str = "left", click_count: int | None = None) -> None:
        """Press *button* where the mouse is, and focus what the press lands on.

        :param button: ``"left"``, ``"middle"`` or ``"right"``.
        :param click_count: the ``detail`` both the press and the matching click
            report; by default a press close to the last one continues its run.
        """
        return self._call(self._down, self._button(button), click_count)

    def up(self, *, button: str = "left", click_count: int | None = None) -> None:
        """Release *button*, firing the click a completed press produces.

        The click lands on the nearest common ancestor of the press and the
        release, which is the element itself when both happened in one place.
        """
        return self._call(self._up, self._button(button), click_count)

    def click(self,
              x: float | None = None,
              y: float | None = None,
              *,
              button: str = "left",
              click_count: int | None = None) -> None:
        """Move to ``(x, y)`` when both are given, then press and release *button*."""
        return self._call(self._click, x, y, self._button(button), click_count)

    def dblclick(self,
                 x: float | None = None,
                 y: float | None = None,
                 *,
                 button: str = "left") -> None:
        """Two clicks reported as one run, ending in ``dblclick`` on the second."""
        return self._call(self._dblclick, x, y, self._button(button))

    def move_to_element(self, element, *, force: bool = False) -> None:
        """Move the mouse to the centre of *element*.

        :raises InvalidStateError: the element has no box (it is not rendered),
            or something else covers the centre and *force* is false.
        """
        return self._call(self._move_to_element, element, force)

    def click_element(self, element, *, button: str = "left", click_count: int = 1,
                      force: bool = False) -> None:
        """Move to the centre of *element* and click there, the way a user would.

        :raises InvalidStateError: the element has no box, or the centre is not
            the element or one of its descendants and *force* is false.
        """
        return self._call(
            self._click_element, element, self._button(button), int(click_count), force,
        )

    # -- the owner-thread half ---------------------------------------------

    def _call(self, action, *args):
        return self._page.realm.executor.call(action, *args)

    def _button(self, name):
        if name not in BUTTON_INDEX:
            raise ValueError(
                f"unknown mouse button {name!r}; expected one of "
                f"{', '.join(sorted(BUTTON_INDEX))}"
            )
        return name

    def _target(self):
        """The element the mouse is over, or the root when it is outside the viewport."""
        document = self._page.document
        found = element_from_point(document, self._x, self._y)
        if found is not None:
            return found
        root = document.get_documentElement()
        return None if root is None or root is pyv8.Null else root

    def _emit(self, event_type, target, *, button=0, detail=0, related=None,
              bubbles=True, cancelable=True, event_class=None, propagates=True):
        """Dispatch one event and hand it back, or ``None`` when there is no target."""
        if target is None:
            return None
        options = {
            "bubbles": bubbles, "cancelable": cancelable, "detail": detail,
            "button": button, "buttons": self._bits,
            "clientX": self._x, "clientY": self._y,
        }
        if related is not None:
            options["relatedTarget"] = related
        interface = event_class or ("PointerEvent" if event_type.startswith("pointer") else "MouseEvent")
        if interface == "PointerEvent":
            options.update({"pointerId": POINTER_ID, "pointerType": "mouse", "isPrimary": True})
            if not event_type.endswith(("down", "up")):
                # A pointer event that reports no button transition says so with
                # -1; the mouse event beside it keeps the 0 Chromium gives it.
                options["button"] = -1
        return self._page.lifecycle.dispatch_page_event(
            event_type, target, options, event_class=interface, propagates=propagates,
        )

    def _move_to(self, x, y, steps):
        start_x, start_y = self._x, self._y
        for step in range(1, steps + 1):
            self._move_once(
                start_x + (x - start_x) * step / steps,
                start_y + (y - start_y) * step / steps,
            )

    def _move_once(self, x, y):
        self._x, self._y = x, y
        target = self._target()
        self._cross(target)
        self._emit("pointermove", target)
        self._emit("mousemove", target)

    def _cross(self, target):
        """Fire the boundary events a move from the hovered element to *target* makes."""
        previous = self._hovered
        if target is previous:
            return
        self._hovered = target
        for kind, interface in (("pointer", "PointerEvent"), ("mouse", "MouseEvent")):
            if previous is not None:
                self._emit(f"{kind}out", previous, related=target, event_class=interface)
                for node in _leaving(previous, target):
                    self._emit(f"{kind}leave", node, related=target, event_class=interface,
                               bubbles=False, cancelable=False, propagates=False)
            if target is not None:
                self._emit(f"{kind}over", target, related=previous, event_class=interface)
                for node in _entering(previous, target):
                    self._emit(f"{kind}enter", node, related=previous, event_class=interface,
                               bubbles=False, cancelable=False, propagates=False)

    def _count_for(self, target, click_count):
        """The ``detail`` this press reports, and the run it belongs to."""
        if click_count is not None:
            self._count = max(1, int(click_count))
        else:
            now = time.monotonic()
            previous = self._last_click
            if (previous is not None
                    and now - previous[0] <= MULTI_CLICK_INTERVAL
                    and abs(self._x - previous[1]) <= MULTI_CLICK_DISTANCE
                    and abs(self._y - previous[2]) <= MULTI_CLICK_DISTANCE
                    and previous[3] is target):
                self._count += 1
            else:
                self._count = 1
        self._last_click = (time.monotonic(), self._x, self._y, target)
        return self._count

    def _down(self, button, click_count):
        target = self._target()
        count = self._count_for(target, click_count)
        self._bits |= BUTTON_BIT[button]
        self._pressed, self._press_count = target, count
        self._emit("pointerdown", target, button=BUTTON_INDEX[button])
        if not accepts_mouse_events(target):
            return
        event = self._emit("mousedown", target, button=BUTTON_INDEX[button], detail=count)
        # Focus is the one thing a cancelled press takes away: the click that
        # follows still fires, as Chrome's does.
        if event is None or not event.defaultPrevented:
            focus_on_mousedown(target)

    def _up(self, button, click_count):
        target = self._target()
        count = self._press_count if click_count is None else max(1, int(click_count))
        pressed = self._pressed
        self._pressed = None
        self._bits &= ~BUTTON_BIT[button]
        self._emit("pointerup", target, button=BUTTON_INDEX[button])
        if not accepts_mouse_events(target):
            return
        self._emit("mouseup", target, button=BUTTON_INDEX[button], detail=count)
        clicked = _common_ancestor(pressed, target) or target
        if button != "left":
            self._emit("auxclick", clicked, button=BUTTON_INDEX[button], detail=count)
            if button == "right":
                self._emit("contextmenu", clicked, button=BUTTON_INDEX[button])
            return
        event = self._emit("click", clicked, detail=count)
        if event is not None and not event.defaultPrevented:
            run_activation(clicked, event)
        if count == 2:
            self._emit("dblclick", clicked, detail=count)

    def _click(self, x, y, button, click_count):
        if x is not None and y is not None:
            self._move_to(float(x), float(y), 1)
        self._down(button, click_count)
        self._up(button, click_count)

    def _dblclick(self, x, y, button):
        self._click(x, y, button, 1)
        self._click(None, None, button, 2)

    def _point_in(self, element, force, action):
        """The centre of *element*, once it is the element a click would reach there."""
        box = border_box(element)
        if box.width <= 0 or box.height <= 0:
            raise InvalidStateError(
                f"{describe(element)} has no box to click: it is not rendered"
            )
        x, y = box.left + box.width / 2, box.top + box.height / 2
        if not force:
            hit = element_from_point(self._page.document, x, y)
            covered = hit is not element and not any(
                node is element for node in _ancestors(hit)
            )
            if covered:
                raise InvalidStateError(
                    f"{describe(element)} is covered at its centre ({x:g}, {y:g})"
                    f" by {describe(hit)}; pass force=True to click there anyway"
                )
        return action(x, y)

    def _move_to_element(self, element, force):
        return self._point_in(element, force, lambda x, y: self._move_to(x, y, 1))

    def _click_element(self, element, button, click_count, force):
        return self._point_in(
            element, force, lambda x, y: self._click(x, y, button, click_count),
        )


__all__ = ["MouseInput"]
