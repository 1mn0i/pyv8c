"""``Screen``: the display the window was created on.

Every field is read from the browser environment's screen configuration, so a
page sees the dimensions its environment was given.
"""

from pyv8 import AttributeSpec, IdlSpec

from ...engine.features import LazyValueStore, interface
from ...foundation.environment import BrowserEnvironment
from ..dom.events import EventTarget

__all__ = ["Screen", "ScreenOrientation"]


@interface('ScreenOrientation', 'EventTarget')
class ScreenOrientation(EventTarget):
    """The display's orientation: a desktop screen is wider than it is tall."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="angle", getter="get_angle", side_effect=1),
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="onchange", getter="get_onchange", setter="set_onchange"),
        ),
        constructor_behavior=0,
        to_string_tag="ScreenOrientation",
        has_constructor=1,
    )

    def __init__(self, width=1280, height=720, **_):
        super().__init__(**_)
        self._width, self._height = float(width), float(height)

    def get_angle(self): return 0
    def get_type(self):
        return "landscape-primary" if self._width >= self._height else "portrait-primary"
    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", value)


def _screen_values(screen):
    """The sub-objects a Screen hands out, built on first read."""
    return LazyValueStore({
        "orientation": lambda: ScreenOrientation(width=screen.width, height=screen.height),
    })


@interface('Screen', 'EventTarget')
class Screen(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=tuple(

                AttributeSpec(name=n, getter="get_" + n, side_effect=1)
                for n in (
                    "width",
                    "height",
                    "availWidth",
                    "availHeight",
                    "availLeft",
                    "availTop",
                    "colorDepth",
                    "pixelDepth",
                    "orientation",
                )

        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, environment=None, **_):
        super().__init__(**_)
        config = BrowserEnvironment.from_dict(environment).screen
        self.width, self.height = config.width, config.height
        self.availWidth, self.availHeight = config.avail_width, config.avail_height
        self.colorDepth, self.pixelDepth = config.color_depth, config.pixel_depth
        self._values = _screen_values(self)
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_availWidth(self): return self.availWidth
    def get_availHeight(self): return self.availHeight
    #: The available area starts at the screen's own origin here.
    def get_availLeft(self): return 0
    def get_availTop(self): return 0
    def get_colorDepth(self): return self.colorDepth
    def get_pixelDepth(self): return self.pixelDepth
    def get_orientation(self): return self._values.value("orientation")
