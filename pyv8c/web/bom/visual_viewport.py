"""``window.visualViewport``: the layout viewport's twin, unzoomed here."""

from pyv8 import AttributeSpec, IdlSpec

from ...engine.features import interface
from ..dom.events import EventTarget, _install_handler_slots

#: Read away one at a time below; each is initialised to nothing here.
VALUES = ("offsetLeft", "offsetTop", "pageLeft", "pageTop", "width", "height", "scale")
HANDLERS = ("onresize", "onscroll", "onscrollend")


@interface('VisualViewport', 'EventTarget')
class VisualViewport(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=tuple(
            AttributeSpec(name=name, getter="get_" + name, side_effect=1) for name in VALUES
        ),
        constructor_behavior=0,
        to_string_tag="VisualViewport",
        has_constructor=1,
    )

    def __init__(self, window=None, **_):
        super().__init__(**_)
        self._window = window

    def get_width(self): return self._window.innerWidth
    def get_height(self): return self._window.innerHeight
    #: Nothing here zooms or scrolls the view, so the page sits at the origin.
    def get_scale(self): return 1
    def get_offsetLeft(self): return 0
    def get_offsetTop(self): return 0
    def get_pageLeft(self): return 0
    def get_pageTop(self): return 0


VisualViewport.__v8_idl_spec__ = VisualViewport.__v8_idl_spec__.replace(
    attributes=VisualViewport.__v8_idl_spec__.attributes
    + _install_handler_slots(VisualViewport, HANDLERS)
)


__all__ = ["VisualViewport"]
