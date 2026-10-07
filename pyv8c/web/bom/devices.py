"""Device APIs the widget enumerates: USB, HID and WebXR.

No device is attached here, so each answers what Chrome answers with none: an empty
device list, and a chooser that refuses without a user gesture.
"""

from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, missing_error
from ...foundation.errors import NotSupportedError, SecurityError
from ..dom.events import EventTarget
from .utils import settled

__all__ = ["HID", "USB", "XRSystem"]

#: Chrome's own wording for the chooser refusal; a page reads it out of the rejection.
_GESTURE = "Must be handling a user gesture to show a permission request."


@interface('USB', 'EventTarget')
class USB(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onconnect", getter="get_onconnect", setter="set_onconnect"),
            AttributeSpec(name="ondisconnect", getter="get_ondisconnect",
                          setter="set_ondisconnect"),
        ),
        methods=(
            MethodSpec(name="getDevices", callback="fn_getDevices", length=0, side_effect=1),
            MethodSpec(name="requestDevice", callback="fn_requestDevice", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="USB",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_onconnect(self): return self._get_handler("onconnect")
    def set_onconnect(self, value): self._set_handler("onconnect", value)
    def get_ondisconnect(self): return self._get_handler("ondisconnect")
    def set_ondisconnect(self, value): self._set_handler("ondisconnect", value)
    def fn_getDevices(self, *_extra): return settled(self._services, value=[])
    def fn_requestDevice(self, options=MISSING, *_extra):
        if options is MISSING:
            raise missing_error('USB', 'requestDevice', 1, 0, constructor=False)
        return settled(self._services, error=SecurityError(
            "Failed to execute 'requestDevice' on 'USB': " + _GESTURE))


@interface('HID', 'EventTarget')
class HID(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onconnect", getter="get_onconnect", setter="set_onconnect"),
            AttributeSpec(name="ondisconnect", getter="get_ondisconnect",
                          setter="set_ondisconnect"),
        ),
        methods=(
            MethodSpec(name="getDevices", callback="fn_getDevices", length=0, side_effect=1),
            MethodSpec(name="requestDevice", callback="fn_requestDevice", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="HID",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_onconnect(self): return self._get_handler("onconnect")
    def set_onconnect(self, value): self._set_handler("onconnect", value)
    def get_ondisconnect(self): return self._get_handler("ondisconnect")
    def set_ondisconnect(self, value): self._set_handler("ondisconnect", value)
    def fn_getDevices(self, *_extra): return settled(self._services, value=[])
    def fn_requestDevice(self, options=MISSING, *_extra):
        if options is MISSING:
            raise missing_error('HID', 'requestDevice', 1, 0, constructor=False)
        return settled(self._services, error=SecurityError(
            "Failed to execute 'requestDevice' on 'HID': " + _GESTURE))


@interface('XRSystem', 'EventTarget')
class XRSystem(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="ondevicechange", getter="get_ondevicechange",
                                  setter="set_ondevicechange"),),
        methods=(
            MethodSpec(name="isSessionSupported", callback="fn_isSessionSupported", length=1,
                       side_effect=1),
            MethodSpec(name="requestSession", callback="fn_requestSession", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="XRSystem",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)

    def get_ondevicechange(self): return self._get_handler("ondevicechange")
    def set_ondevicechange(self, value): self._set_handler("ondevicechange", value)
    def fn_isSessionSupported(self, mode=MISSING, *_extra):
        if mode is MISSING:
            raise missing_error('XRSystem', 'isSessionSupported', 1, 0, constructor=False)
        return settled(self._services, value=False)
    def fn_requestSession(self, mode=MISSING, options=None, *_extra):
        if mode is MISSING:
            raise missing_error('XRSystem', 'requestSession', 1, 0, constructor=False)
        # Chrome resolves an inline session, which is screen-based; with no XR device and
        # no compositor to draw one, refusing is the honest answer here.
        return settled(self._services, error=NotSupportedError(
            "Failed to execute 'requestSession' on 'XRSystem': No XR device is available."))
