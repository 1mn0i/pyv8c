"""``Clipboard``: the asynchronous clipboard.

Chrome waits for a permission prompt here; without one a page's call ends in
``NotAllowedError``, which is what this reports rather than hanging.
"""

from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, missing_error
from ...foundation.errors import dom_exception
from ..dom.events import EventTarget
from .utils import settled

__all__ = ["Clipboard"]


@interface('Clipboard', 'EventTarget')
class Clipboard(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="onclipboardchange", getter="get_onclipboardchange",
                                  setter="set_onclipboardchange"),),
        methods=(
            MethodSpec(name="read", callback="fn_read", length=0, side_effect=1),
            MethodSpec(name="readText", callback="fn_readText", length=0, side_effect=1),
            MethodSpec(name="write", callback="fn_write", length=1),
            MethodSpec(name="writeText", callback="fn_writeText", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="Clipboard",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_onclipboardchange(self): return self._get_handler("onclipboardchange")
    def set_onclipboardchange(self, value): self._set_handler("onclipboardchange", value)
    def _denied(self, method):
        return settled(self._services, error=dom_exception(
            "NotAllowedError",
            "Failed to execute '%s' on 'Clipboard': Write permission denied." % method))
    def fn_read(self, *_extra): return self._denied("read")
    def fn_readText(self, *_extra): return self._denied("readText")
    def fn_write(self, data=MISSING, *_extra):
        if data is MISSING:
            raise missing_error('Clipboard', 'write', 1, 0, constructor=False)
        return self._denied("write")
    def fn_writeText(self, data=MISSING, *_extra):
        if data is MISSING:
            raise missing_error('Clipboard', 'writeText', 1, 0, constructor=False)
        return self._denied("writeText")
