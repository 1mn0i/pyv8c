"""``Screen``: the display the window was created on.

Every field is read from the browser environment's screen configuration, so a
page sees the dimensions its environment was given.
"""

from v8n import AttributeSpec, IdlSpec

from ...engine.features import interface
from ...foundation.environment import BrowserEnvironment
from ..dom.events import EventTarget

__all__ = ["Screen"]


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
                    "colorDepth",
                    "pixelDepth",
                )

        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, environment=None, **_):
        super().__init__(**_)
        config = BrowserEnvironment.from_value(environment).screen
        self.width, self.height = config.width, config.height
        self.availWidth, self.availHeight = config.avail_width, config.avail_height
        self.colorDepth, self.pixelDepth = config.color_depth, config.pixel_depth
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_availWidth(self): return self.availWidth
    def get_availHeight(self): return self.availHeight
    def get_colorDepth(self): return self.colorDepth
    def get_pixelDepth(self): return self.pixelDepth
