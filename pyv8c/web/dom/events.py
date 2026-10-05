"""DOM event model: events, event-handler slots and the target dispatch pipeline."""
import time

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import FlagLocation

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    abort_signal,
    boolean,
    callback_value,
    dictionary,
    dom_string,
    event_constructor_arguments,
    event_handler,
    event_init,
    event_listener,
    integer,
    interface_value,
    is_nullish,
    missing_error,
    number,
)
from ...foundation.errors import V8TypeError
from ...foundation.logging import logger

_DOCUMENT_NODE = 9  # DOM standard nodeType of Document; hardcoded to avoid a dom.nodes import cycle.


def _js_call(fn, this, *args):
    if isinstance(fn, pyv8.JSObject):
        return fn.call(this, *args)
    return fn(*args)


#: Exceptions that must interrupt the process rather than become a page's error.
_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit)


def _report_listener_exception(target, exc):
    """Report an exception a listener raised, as "report the exception" says.

    The DOM invoked the listener, so nothing can catch it: it goes to the realm's
    error reporting and dispatching continues; a gone realm drops it.
    """
    realm = getattr(target._services, "realm_services", None)
    reporter = getattr(realm, "report_exception", None) if realm is not None else None
    if reporter is not None:
        reporter(exc)


def _modifier_state(event, key):
    """Shared ``getModifierState`` table used by mouse and keyboard events."""
    return {
        "Control": event.ctrlKey,
        "Shift": event.shiftKey,
        "Alt": event.altKey,
        "Meta": event.metaKey,
    }.get(dom_string(key), False)


def _nullable_string(value):
    """Web IDL ``DOMString?``: ``null``/``undefined`` are preserved as null."""
    return pyv8.Null if is_nullish(value) else dom_string(value)


@interface('Event', None)
class Event:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="target", getter="get_target", side_effect=1),
            AttributeSpec(name="currentTarget", getter="get_currentTarget", side_effect=1),
            AttributeSpec(name="bubbles", getter="get_bubbles", side_effect=1),
            AttributeSpec(name="cancelable", getter="get_cancelable", side_effect=1),
            AttributeSpec(name="defaultPrevented", getter="get_defaultPrevented", side_effect=1),
            AttributeSpec(name="eventPhase", getter="get_eventPhase", side_effect=1),
            AttributeSpec(name="cancelBubble", getter="get_cancelBubble", setter="set_cancelBubble"),
            AttributeSpec(name="timeStamp", getter="get_timeStamp", side_effect=1),
            AttributeSpec(name="isTrusted", getter="get_isTrusted", side_effect=1),
            AttributeSpec(name="composed", getter="get_composed", side_effect=1),
            AttributeSpec(name="returnValue", getter="get_returnValue", setter="set_returnValue"),
            AttributeSpec(name="persisted", getter="get_persisted", side_effect=1),
        ),
        methods=(
            MethodSpec(name="preventDefault", callback="fn_preventDefault"),
            MethodSpec(name="stopPropagation", callback="fn_stopPropagation"),
            MethodSpec(name="stopImmediatePropagation", callback="fn_stopImmediatePropagation"),
            MethodSpec(name="composedPath", callback="fn_composedPath", side_effect=1),
            MethodSpec(name="initEvent", callback="fn_initEvent", length=3),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="Event",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **_):
        event_type, options = event_constructor_arguments('Event', event_type, options)
        init = event_init(options)
        self.type = event_type
        self.bubbles = boolean(init.get("bubbles", False))
        self.cancelable = boolean(init.get("cancelable", False))
        self.target = self.currentTarget = None; self.eventPhase = 0; self.defaultPrevented = False
        self._stop = self._immediate = False; self._path = []; self._passive = False
        self._composed = boolean(init.get("composed", False))
        self._time_stamp = time.time() * 1000.0
        # Trust is host state, never an EventInit member.
        self._trusted = bool(init.get("isTrusted", False)) if type(options) is dict else False
        self._persisted = bool(init.get("persisted", False)) if type(options) is dict else False
        # Enter and leave events reach their target alone (see fn_dispatchEvent).
        self._non_propagating = False
    def get_type(self): return self.type
    def get_target(self): return self.target or pyv8.Null
    def get_currentTarget(self): return self.currentTarget or pyv8.Null
    def get_bubbles(self): return self.bubbles
    def get_cancelable(self): return self.cancelable
    def get_defaultPrevented(self): return self.defaultPrevented
    def get_eventPhase(self): return self.eventPhase
    def get_cancelBubble(self): return self._stop
    def set_cancelBubble(self, value):
        value = boolean(value)
        self._stop = bool(value)
    def get_timeStamp(self): return self._time_stamp
    def get_isTrusted(self): return self._trusted
    def get_composed(self): return self._composed
    def get_persisted(self): return self._persisted
    def get_returnValue(self): return not self.defaultPrevented
    def set_returnValue(self, value):
        value = boolean(value)
        if not value and self.cancelable: self.defaultPrevented = True
    def fn_preventDefault(self, *_extra):
        if self.cancelable and not self._passive: self.defaultPrevented = True
    def fn_stopPropagation(self, *_extra):
        self._stop = True
    def fn_stopImmediatePropagation(self, *_extra):
        self._stop = self._immediate = True
    def fn_composedPath(self, *_extra):
        return list(self._path)
    def fn_initEvent(self, event_type=MISSING, bubbles=False, cancelable=False, *_extra):
        # Legacy initializer; the DOM's "already dispatching" no-op is out of scope here.
        if event_type is MISSING:
            raise missing_error('Event', 'initEvent', 3, 0, constructor=False)
        if bubbles is pyv8.Undefined:
            bubbles = False
        if cancelable is pyv8.Undefined:
            cancelable = False
        self.type = dom_string(event_type)
        self.bubbles = boolean(bubbles)
        self.cancelable = boolean(cancelable)


@interface('UIEvent', 'Event')
class UIEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="view", getter="get_view", side_effect=1),
            AttributeSpec(name="detail", getter="get_detail", side_effect=1),
            AttributeSpec(name="which", getter="get_which", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="UIEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('UIEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.detail = integer(init.get("detail", 0), signed=True)
        self.view = init.get("view", pyv8.Null)
    def get_view(self): return self.view
    def get_detail(self): return self.detail
    def get_which(self): return self.detail


@interface('MouseEvent', 'UIEvent')
class MouseEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="screenX", getter="get_screenX", side_effect=1),
            AttributeSpec(name="screenY", getter="get_screenY", side_effect=1),
            AttributeSpec(name="clientX", getter="get_clientX", side_effect=1),
            AttributeSpec(name="clientY", getter="get_clientY", side_effect=1),
            AttributeSpec(name="pageX", getter="get_pageX", side_effect=1),
            AttributeSpec(name="pageY", getter="get_pageY", side_effect=1),
            AttributeSpec(name="x", getter="get_x", side_effect=1),
            AttributeSpec(name="y", getter="get_y", side_effect=1),
            AttributeSpec(name="offsetX", getter="get_offsetX", side_effect=1),
            AttributeSpec(name="offsetY", getter="get_offsetY", side_effect=1),
            AttributeSpec(name="movementX", getter="get_movementX", side_effect=1),
            AttributeSpec(name="movementY", getter="get_movementY", side_effect=1),
            AttributeSpec(name="ctrlKey", getter="get_ctrlKey", side_effect=1),
            AttributeSpec(name="shiftKey", getter="get_shiftKey", side_effect=1),
            AttributeSpec(name="altKey", getter="get_altKey", side_effect=1),
            AttributeSpec(name="metaKey", getter="get_metaKey", side_effect=1),
            AttributeSpec(name="button", getter="get_button", side_effect=1),
            AttributeSpec(name="buttons", getter="get_buttons", side_effect=1),
            AttributeSpec(name="relatedTarget", getter="get_relatedTarget", side_effect=1),
            AttributeSpec(name="fromElement", getter="get_fromElement", side_effect=1),
            AttributeSpec(name="toElement", getter="get_toElement", side_effect=1),
            AttributeSpec(name="which", getter="get_which", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getModifierState", callback="fn_getModifierState", length=1, side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="MouseEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('MouseEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.screenX = number(init.get("screenX", 0))
        self.screenY = number(init.get("screenY", 0))
        self.clientX = number(init.get("clientX", 0))
        self.clientY = number(init.get("clientY", 0))
        # No scrolling, so page and viewport coordinates coincide unless given.
        # Owner-free name: ``web`` must not name a host page object.
        self._document_x = number(init.get("pageX", self.clientX))
        self._document_y = number(init.get("pageY", self.clientY))
        self.x = number(init.get("x", self.clientX))
        self.y = number(init.get("y", self.clientY))
        self.offsetX = number(init.get("offsetX", self.clientX))
        self.offsetY = number(init.get("offsetY", self.clientY))
        self.movementX = number(init.get("movementX", 0))
        self.movementY = number(init.get("movementY", 0))
        self.ctrlKey = boolean(init.get("ctrlKey", False))
        self.shiftKey = boolean(init.get("shiftKey", False))
        self.altKey = boolean(init.get("altKey", False))
        self.metaKey = boolean(init.get("metaKey", False))
        self.button = integer(init.get("button", 0), bits=16, signed=True)
        self.buttons = integer(init.get("buttons", 0), bits=16)
        self.relatedTarget = init.get("relatedTarget", pyv8.Null)

    def get_screenX(self): return self.screenX
    def get_screenY(self): return self.screenY
    def get_clientX(self): return self.clientX
    def get_clientY(self): return self.clientY
    def get_pageX(self): return self._document_x
    def get_pageY(self): return self._document_y
    def get_x(self): return self.x
    def get_y(self): return self.y
    def get_offsetX(self): return self.offsetX
    def get_offsetY(self): return self.offsetY
    def get_movementX(self): return self.movementX
    def get_movementY(self): return self.movementY
    def get_ctrlKey(self): return self.ctrlKey
    def get_shiftKey(self): return self.shiftKey
    def get_altKey(self): return self.altKey
    def get_metaKey(self): return self.metaKey
    def get_button(self): return self.button
    def get_buttons(self): return self.buttons
    def get_relatedTarget(self): return self.relatedTarget
    # Legacy aliases: Blink exposes relatedTarget only for the matching type.
    def get_fromElement(self): return self.relatedTarget if self.type == "mouseover" else pyv8.Null
    def get_toElement(self): return self.relatedTarget if self.type == "mouseout" else pyv8.Null
    def get_which(self): return self.button + 1
    def fn_getModifierState(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('MouseEvent', 'getModifierState', 1, 0, constructor=False)
        return _modifier_state(self, key)


@interface('KeyboardEvent', 'UIEvent')
class KeyboardEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="key", getter="get_key", side_effect=1),
            AttributeSpec(name="code", getter="get_code", side_effect=1),
            AttributeSpec(name="location", getter="get_location", side_effect=1),
            AttributeSpec(name="ctrlKey", getter="get_ctrlKey", side_effect=1),
            AttributeSpec(name="shiftKey", getter="get_shiftKey", side_effect=1),
            AttributeSpec(name="altKey", getter="get_altKey", side_effect=1),
            AttributeSpec(name="metaKey", getter="get_metaKey", side_effect=1),
            AttributeSpec(name="repeat", getter="get_repeat", side_effect=1),
            AttributeSpec(name="isComposing", getter="get_isComposing", side_effect=1),
            AttributeSpec(name="charCode", getter="get_charCode", side_effect=1),
            AttributeSpec(name="keyCode", getter="get_keyCode", side_effect=1),
            AttributeSpec(name="which", getter="get_which", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getModifierState", callback="fn_getModifierState", length=1, side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="KeyboardEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('KeyboardEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.key = dom_string(init.get("key", ""))
        self.code = dom_string(init.get("code", ""))
        self.location = integer(init.get("location", 0), bits=32)
        self.ctrlKey = boolean(init.get("ctrlKey", False))
        self.shiftKey = boolean(init.get("shiftKey", False))
        self.altKey = boolean(init.get("altKey", False))
        self.metaKey = boolean(init.get("metaKey", False))
        self.repeat = boolean(init.get("repeat", False))
        self.isComposing = boolean(init.get("isComposing", False))
        # Legacy numeric aliases stay init-driven; no key-code table is derived.
        self.charCode = integer(init.get("charCode", 0), bits=32)
        self.keyCode = integer(init.get("keyCode", 0), bits=32)

    def get_key(self): return self.key
    def get_code(self): return self.code
    def get_location(self): return self.location
    def get_ctrlKey(self): return self.ctrlKey
    def get_shiftKey(self): return self.shiftKey
    def get_altKey(self): return self.altKey
    def get_metaKey(self): return self.metaKey
    def get_repeat(self): return self.repeat
    def get_isComposing(self): return self.isComposing
    def get_charCode(self): return self.charCode
    def get_keyCode(self): return self.keyCode
    def get_which(self): return self.keyCode
    def fn_getModifierState(self, key=MISSING, *_extra):
        if key is MISSING:
            raise missing_error('KeyboardEvent', 'getModifierState', 1, 0, constructor=False)
        return _modifier_state(self, key)


@interface('CustomEvent', 'Event')
class CustomEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="detail", getter="get_detail", side_effect=1),),
        methods=(
            MethodSpec(name="initCustomEvent", callback="fn_initCustomEvent", length=4),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="CustomEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('CustomEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.detail = init.get("detail", pyv8.Null)

    def get_detail(self): return self.detail
    def fn_initCustomEvent(self, event_type=MISSING, bubbles=False, cancelable=False, detail=pyv8.Null, *_extra):
        if event_type is MISSING:
            raise missing_error('CustomEvent', 'initCustomEvent', 4, 0, constructor=False)
        if bubbles is pyv8.Undefined:
            bubbles = False
        if cancelable is pyv8.Undefined:
            cancelable = False
        self.fn_initEvent(event_type, bubbles, cancelable)
        self.detail = detail


@interface('FocusEvent', 'UIEvent')
class FocusEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="relatedTarget", getter="get_relatedTarget", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="FocusEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('FocusEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        self.relatedTarget = event_init(options).get("relatedTarget", pyv8.Null)

    def get_relatedTarget(self): return self.relatedTarget


@interface('InputEvent', 'UIEvent')
class InputEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="data", getter="get_data", side_effect=1),
            AttributeSpec(name="isComposing", getter="get_isComposing", side_effect=1),
            AttributeSpec(name="inputType", getter="get_inputType", side_effect=1),
            AttributeSpec(name="dataTransfer", getter="get_dataTransfer", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getTargetRanges", callback="fn_getTargetRanges", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="InputEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('InputEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.data = _nullable_string(init.get("data", pyv8.Null))
        self.isComposing = boolean(init.get("isComposing", False))
        self.inputType = dom_string(init.get("inputType", ""))
        self.dataTransfer = init.get("dataTransfer", pyv8.Null)

    def get_data(self): return self.data
    def get_isComposing(self): return self.isComposing
    def get_inputType(self): return self.inputType
    def get_dataTransfer(self): return self.dataTransfer
    # No editing pipeline exists to report target ranges for.
    def fn_getTargetRanges(self, *_extra): return []


@interface('CompositionEvent', 'UIEvent')
class CompositionEvent(UIEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="data", getter="get_data", side_effect=1),),
        constructor_behavior=1,
        length=1,
        to_string_tag="CompositionEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('CompositionEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        self.data = dom_string(event_init(options).get("data", ""))

    def get_data(self): return self.data


@interface('PointerEvent', 'MouseEvent')
class PointerEvent(MouseEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="pointerId", getter="get_pointerId", side_effect=1),
            AttributeSpec(name="width", getter="get_width", side_effect=1),
            AttributeSpec(name="height", getter="get_height", side_effect=1),
            AttributeSpec(name="pressure", getter="get_pressure", side_effect=1),
            AttributeSpec(name="tiltX", getter="get_tiltX", side_effect=1),
            AttributeSpec(name="tiltY", getter="get_tiltY", side_effect=1),
            AttributeSpec(name="azimuthAngle", getter="get_azimuthAngle", side_effect=1),
            AttributeSpec(name="altitudeAngle", getter="get_altitudeAngle", side_effect=1),
            AttributeSpec(name="tangentialPressure", getter="get_tangentialPressure", side_effect=1),
            AttributeSpec(name="twist", getter="get_twist", side_effect=1),
            AttributeSpec(name="pointerType", getter="get_pointerType", side_effect=1),
            AttributeSpec(name="isPrimary", getter="get_isPrimary", side_effect=1),
            AttributeSpec(name="persistentDeviceId", getter="get_persistentDeviceId", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getCoalescedEvents", callback="fn_getCoalescedEvents", side_effect=1),
            MethodSpec(name="getPredictedEvents", callback="fn_getPredictedEvents", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="PointerEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PointerEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.pointerId = integer(init.get("pointerId", 0), signed=True)
        self.width = number(init.get("width", 1))
        self.height = number(init.get("height", 1))
        self.pressure = number(init.get("pressure", 0.5))
        self.tiltX = integer(init.get("tiltX", 0), signed=True)
        self.tiltY = integer(init.get("tiltY", 0), signed=True)
        self.azimuthAngle = number(init.get("azimuthAngle", 0))
        self.altitudeAngle = number(init.get("altitudeAngle", 0))
        self.tangentialPressure = number(init.get("tangentialPressure", 0))
        self.twist = integer(init.get("twist", 0), signed=True)
        self.pointerType = dom_string(init.get("pointerType", ""))
        self.isPrimary = boolean(init.get("isPrimary", False))
        self.persistentDeviceId = integer(init.get("persistentDeviceId", 0), signed=True)

    def get_pointerId(self): return self.pointerId
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_pressure(self): return self.pressure
    def get_tiltX(self): return self.tiltX
    def get_tiltY(self): return self.tiltY
    def get_azimuthAngle(self): return self.azimuthAngle
    def get_altitudeAngle(self): return self.altitudeAngle
    def get_tangentialPressure(self): return self.tangentialPressure
    def get_twist(self): return self.twist
    def get_pointerType(self): return self.pointerType
    def get_isPrimary(self): return self.isPrimary
    def get_persistentDeviceId(self): return self.persistentDeviceId
    # No coalescing/prediction pipeline; return empty so iterating scripts keep working.
    def fn_getCoalescedEvents(self, *_extra): return []
    def fn_getPredictedEvents(self, *_extra): return []


@interface('WheelEvent', 'MouseEvent')
class WheelEvent(MouseEvent):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="deltaX", getter="get_deltaX", side_effect=1),
            AttributeSpec(name="deltaY", getter="get_deltaY", side_effect=1),
            AttributeSpec(name="deltaZ", getter="get_deltaZ", side_effect=1),
            AttributeSpec(name="deltaMode", getter="get_deltaMode", side_effect=1),
            AttributeSpec(name="wheelDeltaX", getter="get_wheelDeltaX", side_effect=1),
            AttributeSpec(name="wheelDeltaY", getter="get_wheelDeltaY", side_effect=1),
            AttributeSpec(name="wheelDelta", getter="get_wheelDelta", side_effect=1),
            AttributeSpec(name="momentum", getter="get_momentum", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="WheelEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('WheelEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.deltaX = number(init.get("deltaX", 0))
        self.deltaY = number(init.get("deltaY", 0))
        self.deltaZ = number(init.get("deltaZ", 0))
        self.deltaMode = integer(init.get("deltaMode", 0), bits=32)
        # Legacy Blink aliases: positive when the content scrolls up/left.
        self.wheelDeltaX = number(init.get("wheelDeltaX", -self.deltaX))
        self.wheelDeltaY = number(init.get("wheelDeltaY", -self.deltaY))
        self.wheelDelta = number(init.get("wheelDelta", -self.deltaY))
        self.momentum = boolean(init.get("momentum", False))

    def get_deltaX(self): return self.deltaX
    def get_deltaY(self): return self.deltaY
    def get_deltaZ(self): return self.deltaZ
    def get_deltaMode(self): return self.deltaMode
    def get_wheelDeltaX(self): return self.wheelDeltaX
    def get_wheelDeltaY(self): return self.wheelDeltaY
    def get_wheelDelta(self): return self.wheelDelta
    def get_momentum(self): return self.momentum


@interface('PopStateEvent', 'Event')
class PopStateEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="state", getter="get_state", side_effect=1),
            AttributeSpec(name="hasUAVisualTransition", getter="get_hasUAVisualTransition", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="PopStateEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PopStateEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        state = init.get("state", pyv8.Null)
        # ``None`` is the host's "no state"; a JS ``undefined`` stays undefined.
        self.state = pyv8.Null if state is None else state
        self.has_ua_visual_transition = boolean(init.get("hasUAVisualTransition", False))

    def get_state(self): return self.state
    def get_hasUAVisualTransition(self): return self.has_ua_visual_transition


@interface('HashChangeEvent', 'Event')
class HashChangeEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="oldURL", getter="get_oldURL", side_effect=1),
            AttributeSpec(name="newURL", getter="get_newURL", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="HashChangeEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('HashChangeEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.oldURL = dom_string(init.get("oldURL", ""))
        self.newURL = dom_string(init.get("newURL", ""))

    def get_oldURL(self): return self.oldURL
    def get_newURL(self): return self.newURL


@interface('StorageEvent', 'Event')
class StorageEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="key", getter="get_key", side_effect=1),
            AttributeSpec(name="oldValue", getter="get_oldValue", side_effect=1),
            AttributeSpec(name="newValue", getter="get_newValue", side_effect=1),
            AttributeSpec(name="url", getter="get_url", side_effect=1),
            AttributeSpec(name="storageArea", getter="get_storageArea", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="StorageEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('StorageEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.key = _nullable_string(init.get("key", pyv8.Null))
        self.oldValue = _nullable_string(init.get("oldValue", pyv8.Null))
        self.newValue = _nullable_string(init.get("newValue", pyv8.Null))
        self.url = dom_string(init.get("url", ""))
        self.storageArea = init.get("storageArea", pyv8.Null)

    def get_key(self): return self.key
    def get_oldValue(self): return self.oldValue
    def get_newValue(self): return self.newValue
    def get_url(self): return self.url
    def get_storageArea(self): return self.storageArea


@interface('WebGLContextEvent', 'Event')
class WebGLContextEvent(Event):
    """``webglcontextlost`` and its siblings: what a lost context reports."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="statusMessage", getter="get_statusMessage",
                                  side_effect=1),),
        constructor_behavior=1,
        length=1,
        to_string_tag="WebGLContextEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('WebGLContextEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        self.statusMessage = dom_string(event_init(options).get("statusMessage", ""))

    def get_statusMessage(self): return self.statusMessage


@interface('AnimationEvent', 'Event')
class AnimationEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="animationName", getter="get_animationName", side_effect=1),
            AttributeSpec(name="elapsedTime", getter="get_elapsedTime", side_effect=1),
            AttributeSpec(name="pseudoElement", getter="get_pseudoElement", side_effect=1),
            AttributeSpec(name="animation", getter="get_animation", side_effect=1),
            AttributeSpec(name="pseudoTarget", getter="get_pseudoTarget", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="AnimationEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('AnimationEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.animationName = dom_string(init.get("animationName", ""))
        self.elapsedTime = number(init.get("elapsedTime", 0))
        self.pseudoElement = dom_string(init.get("pseudoElement", ""))
        self.animation = init.get("animation", pyv8.Null)
        self.pseudoTarget = init.get("pseudoTarget", pyv8.Null)

    def get_animationName(self): return self.animationName
    def get_elapsedTime(self): return self.elapsedTime
    def get_pseudoElement(self): return self.pseudoElement
    def get_animation(self): return self.animation
    def get_pseudoTarget(self): return self.pseudoTarget


@interface('TransitionEvent', 'Event')
class TransitionEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="propertyName", getter="get_propertyName", side_effect=1),
            AttributeSpec(name="elapsedTime", getter="get_elapsedTime", side_effect=1),
            AttributeSpec(name="pseudoElement", getter="get_pseudoElement", side_effect=1),
            AttributeSpec(name="animation", getter="get_animation", side_effect=1),
            AttributeSpec(name="pseudoTarget", getter="get_pseudoTarget", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="TransitionEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('TransitionEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.propertyName = dom_string(init.get("propertyName", ""))
        self.elapsedTime = number(init.get("elapsedTime", 0))
        self.pseudoElement = dom_string(init.get("pseudoElement", ""))
        self.animation = init.get("animation", pyv8.Null)
        self.pseudoTarget = init.get("pseudoTarget", pyv8.Null)

    def get_propertyName(self): return self.propertyName
    def get_elapsedTime(self): return self.elapsedTime
    def get_pseudoElement(self): return self.pseudoElement
    def get_animation(self): return self.animation
    def get_pseudoTarget(self): return self.pseudoTarget


@interface('PromiseRejectionEvent', 'Event')
class PromiseRejectionEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="promise", getter="get_promise", side_effect=1),
            AttributeSpec(name="reason", getter="get_reason", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="PromiseRejectionEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PromiseRejectionEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        self.promise = init.get("promise", pyv8.Null)
        self.reason = init.get("reason", pyv8.Null)

    def get_promise(self): return self.promise
    def get_reason(self): return self.reason


@interface('PageTransitionEvent', 'Event')
class PageTransitionEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="persisted", getter="get_persisted", side_effect=1),),
        constructor_behavior=1,
        length=1,
        to_string_tag="PageTransitionEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PageTransitionEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        self._persisted = boolean(event_init(options).get("persisted", False))

    def get_persisted(self): return self._persisted


@interface('PageRevealEvent', 'Event')
class PageRevealEvent(Event):
    """``pagereveal``: fired once when the page is first presented."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="viewTransition", getter="get_viewTransition",
                                  side_effect=1),),
        constructor_behavior=1,
        length=1,
        to_string_tag="PageRevealEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PageRevealEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        transition = event_init(options).get("viewTransition")
        # A page with no view transition running has none, which Chrome reports as null.
        self._view_transition = pyv8.Null if is_nullish(transition) else transition

    def get_viewTransition(self): return self._view_transition


@interface('PageSwapEvent', 'Event')
class PageSwapEvent(Event):
    """``pageswap``: the last event before a document is replaced."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="viewTransition", getter="get_viewTransition", side_effect=1),
            AttributeSpec(name="activation", getter="get_activation", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        to_string_tag="PageSwapEvent",
        has_constructor=1,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('PageSwapEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        init = event_init(options)
        transition = init.get("viewTransition")
        activation = init.get("activation")
        self._view_transition = pyv8.Null if is_nullish(transition) else transition
        self._activation = pyv8.Null if is_nullish(activation) else activation

    def get_viewTransition(self): return self._view_transition
    def get_activation(self): return self._activation


@interface('BeforeUnloadEvent', 'Event')
class BeforeUnloadEvent(Event):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="returnValue", getter="get_returnValue", setter="set_returnValue"),
        ),
        # ``allow_new=false`` in the baseline: non-constructible, but the host
        # still builds one through ``__init__``.
        constructor_behavior=0,
        to_string_tag="BeforeUnloadEvent",
        has_constructor=0,
    )

    def __init__(self, event_type=MISSING, options=None, *_extra, **kw):
        event_type, options = event_constructor_arguments('BeforeUnloadEvent', event_type, options)
        super().__init__(event_type, options, **kw)
        self._return_value = ""

    def get_returnValue(self): return self._return_value
    def set_returnValue(self, value):
        value = dom_string(value)
        self._return_value = value
        if value:
            self.defaultPrevented = True


@interface('EventTarget', None)
class EventTarget:
    # Every ``on*`` slot is generated from ``_HANDLER_SLOTS`` at module bottom.
    __v8_idl_spec__ = IdlSpec(
        attributes=(),
        methods=(
            MethodSpec(name="addEventListener", callback="fn_addEventListener", length=2),
            MethodSpec(name="removeEventListener", callback="fn_removeEventListener", length=2),
            MethodSpec(name="dispatchEvent", callback="fn_dispatchEvent", length=1),
        ),
        constructor_behavior=1,
        has_constructor=1,
    )

    def __init__(self, *_extra, services=None, **_):
        if services is pyv8.Undefined:
            services = None
        self._services = services
        self._listeners = {}
        self._listener_order = 0
        self._handlers = {}

    def _get_handler(self, name): return self._handlers.get(name, pyv8.Null)
    def _set_handler(self, name, value): self._handlers[name] = event_handler(value)

    def fn_addEventListener(self, event_type=MISSING, listener=MISSING, options=False, *_extra):
        if event_type is MISSING:
            raise missing_error('EventTarget', 'addEventListener', 2, 0, constructor=False)
        if listener is MISSING:
            raise missing_error('EventTarget', 'addEventListener', 2, 1, constructor=False)
        event_type = dom_string(event_type)
        if not is_nullish(listener):
            listener = event_listener(listener)
        if options is pyv8.Undefined:
            options = False
        if isinstance(options, pyv8.JSObject):
            options = dictionary(options)
        capture = boolean(options.get("capture", False)) if isinstance(options, dict) else boolean(options)
        once = bool(options.get("once", False)) if isinstance(options, dict) else False
        passive = bool(options.get("passive", False)) if isinstance(options, dict) else False
        signal = None
        if isinstance(options, dict):
            try:
                signal = abort_signal(options.get("signal"))
            except V8TypeError as exc:
                raise V8TypeError(
                    "Failed to execute 'addEventListener' on 'EventTarget': Failed to read"
                    " the 'signal' property from 'AddEventListenerOptions': Failed to"
                    " convert value to 'AbortSignal'.") from exc
        if is_nullish(listener):
            return
        bucket = self._listeners.setdefault(str(event_type), [])
        if not any(item[0] is listener and item[1] == capture for item in bucket):
            self._listener_order += 1; bucket.append((listener, capture, once, passive, signal, self._listener_order))
            if signal is not None:
                # Chrome takes the listener back out when the signal fires; the check
                # in ``_invoke`` only covers a signal that had already aborted here.
                signal._subscribe(self._drop_listener(str(event_type), listener, capture))
            self._motion_listener_added(str(event_type))

    def _drop_listener(self, event_type, listener, capture):
        """What a signal runs to take one registration back out of its bucket."""
        def drop():
            bucket = self._listeners.get(event_type)
            if bucket:
                self._listeners[event_type] = [
                    item for item in bucket
                    if not (item[0] is listener and item[1] == capture)]
        return drop

    def _motion_listener_added(self, event_type):
        """Tell the realm that a motion listener appeared on the window.

        Chrome starts delivering these readings as soon as a page listens, and only
        the realm knows where the document is in its life, so the decision is its.
        """
        if event_type not in MOTION_EVENT_TYPES:
            return
        document_services = getattr(self._services, "document_services", None)
        if getattr(document_services, "window", None) is not self:
            return
        notify = getattr(document_services, "motion_event_listener", None)
        if notify is not None:
            notify(event_type)

    def fn_removeEventListener(self, event_type=MISSING, listener=MISSING, options=False, *_extra):
        if event_type is MISSING:
            raise missing_error('EventTarget', 'removeEventListener', 2, 0, constructor=False)
        if listener is MISSING:
            raise missing_error('EventTarget', 'removeEventListener', 2, 1, constructor=False)
        event_type = dom_string(event_type)
        if not is_nullish(listener):
            listener = event_listener(listener)
        if options is pyv8.Undefined:
            options = False
        if isinstance(options, pyv8.JSObject):
            options = dictionary(options)
        capture = boolean(options.get("capture", False)) if isinstance(options, dict) else boolean(options)
        bucket = self._listeners.get(str(event_type), [])
        self._listeners[str(event_type)] = [x for x in bucket if not (x[0] is listener and x[1] == capture)]

    def fn_dispatchEvent(self, event=MISSING, *_extra):
        if event is MISSING:
            raise missing_error('EventTarget', 'dispatchEvent', 1, 0, constructor=False)
        event = interface_value(event, 'Event', 'EventTarget', 'dispatchEvent', 1, nullable=False)
        if not isinstance(event, Event):
            raise V8TypeError("Failed to execute 'dispatchEvent' on 'EventTarget': parameter 1 is not of type 'Event'.")
        # Every event a page receives passes through here, trusted or not.
        logger.debug("dispatch {} on {} (trusted={}, listeners={})", event.type,
                     type(self).__name__, event._trusted,
                     len(self._listeners.get(event.type, ())))
        event.target = event.target or self
        # A non-propagating event reaches its target alone: Chromium dispatches
        # ``mouseenter``/``mouseleave`` (and pointer twins) with a path of just the node.
        path = [self]
        if not event._non_propagating:
            node = self
            while node is not None:
                node = getattr(node, "parentNode", None)
                if node is None and getattr(path[-1], "nodeType", None) == _DOCUMENT_NODE:
                    node = getattr(getattr(getattr(path[-1], "_services", None), "document_services", None), "window", None)
                if node is not None:
                    path.append(node)
        event._path = list(path)
        for target in reversed(path[1:]):
            event.eventPhase, event.currentTarget = 1, target
            target._invoke(event, capture=True)
            if event._stop:
                return not event.defaultPrevented
        event.eventPhase, event.currentTarget = 2, self
        self._invoke(event, capture=True)
        if not event._immediate: self._invoke(event, capture=False)
        if event.bubbles and not event._stop:
            for target in path[1:]:
                event.eventPhase, event.currentTarget = 3, target
                target._invoke(event, capture=False)
                if event._stop: break
        event.eventPhase, event.currentTarget = 0, None
        return not event.defaultPrevented

    def _invoke(self, event, capture):
        listeners = self._listeners.get(event.type)
        handler = self._handlers.get("on" + event.type)
        # A dispatch visits every node on the path, and most of them listen for
        # nothing: this is the check that keeps those visits to two dict reads.
        if listeners is None and handler is None:
            return
        for listener, registered_capture, once, passive, signal, _order in list(listeners or ()):
            if registered_capture != capture:
                continue
            if signal is not None and getattr(signal, "aborted", False): continue
            event._passive = passive
            try:
                if callable(listener):
                    _js_call(listener, self, event)
                else:
                    callback = listener._get("handleEvent") if isinstance(listener, pyv8.JSObject) else listener.get("handleEvent")
                    _js_call(callback_value(callback), listener, event)
            except _CONTROL_EXCEPTIONS:
                raise
            except BaseException as exc:  # reported, then dispatching continues
                # Escaping here would make dispatchEvent throw and skip later listeners.
                _report_listener_exception(self, exc)
            finally:
                event._passive = False
            if once: self._listeners[event.type] = [x for x in self._listeners.get(event.type, ()) if x[0] is not listener or x[1] != registered_capture]
            if event._immediate:
                return
        # EventHandler slots may hold non-callable objects; Blink invokes their function.
        if not capture and callable(handler):
            try:
                if event.type == "error" and self.__class__.__name__ == "Window":
                    _js_call(handler, self, getattr(event, "message", ""),
                             getattr(event, "filename", ""), getattr(event, "lineno", 0),
                             getattr(event, "colno", 0), getattr(event, "error", pyv8.Null))
                else:
                    result = _js_call(handler, self, event)
                    if event.type == "beforeunload" and result not in (None, pyv8.Null, pyv8.Undefined, ""):
                        event.defaultPrevented = True
            except _CONTROL_EXCEPTIONS:
                raise
            except BaseException as exc:  # reported, not propagated
                # The handler slot is a listener with a shorter spelling: report the same way.
                _report_listener_exception(self, exc)

    def __getattr__(self, name):
        if name.startswith("on"):
            return self._handlers.get(name, pyv8.Null)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        # A plain index beats str.startswith, and almost every assignment fails it.
        if name[:2] == "on" and name != "one" and "_handlers" in self.__dict__:
            self._handlers[name] = value
            return
        object.__setattr__(self, name, value)


#: Types Chrome delivers on its own for a listening page, from the device sensors.
MOTION_EVENT_TYPES = ("devicemotion", "deviceorientation", "deviceorientationabsolute")


# Event-handler attributes shared by every EventTarget subclass.  Only names the
# Chromium baseline declares are listed, and composition slots are absent (the
# baseline declares them only on ``EditContext``).  Installed with
# ``FlagLocation.kInstance``, keeping them off ``EventTarget.prototype``.
_HANDLER_SLOTS = (
    "onload", "onerror", "onreadystatechange", "onpageshow", "onpagehide",
    "onunload", "onbeforeunload",
    "onclick", "ondblclick", "onauxclick", "oncontextmenu",
    "onmousedown", "onmouseup", "onmousemove", "onmouseover", "onmouseout",
    "onmouseenter", "onmouseleave",
    "onfocus", "onblur", "oninput", "onbeforeinput", "onchange",
    "onselect", "oninvalid", "onsubmit", "onreset",
    "onkeydown", "onkeyup", "onkeypress",
    "onpointerdown", "onpointerup", "onpointermove", "onpointerover", "onpointerout",
    "onpointerenter", "onpointerleave", "onpointercancel",
    "ongotpointercapture", "onlostpointercapture", "onwheel", "onscroll",
    "ontouchstart", "ontouchend", "ontouchmove", "ontouchcancel",
    "onhashchange", "onpopstate", "onstorage", "onmessageerror",
    "onvisibilitychange", "onfreeze", "onresume",
    "onloadstart", "onloadend", "onprogress", "onabort", "ontimeout",
    "onanimationstart", "onanimationend", "onanimationiteration", "onanimationcancel",
    "ontransitionstart", "ontransitionend", "ontransitionrun", "ontransitioncancel",
    "oncopy", "oncut", "onpaste", "onselectstart", "onselectionchange",
    "onunhandledrejection", "onrejectionhandled",
    "oncanplay", "oncanplaythrough", "ondurationchange", "onemptied", "onended",
    "onloadeddata", "onloadedmetadata", "onpause", "onplay", "onplaying",
    "onratechange", "onseeked", "onseeking", "onstalled", "onsuspend",
    "ontimeupdate", "onvolumechange", "onwaiting",
    "ondrag", "ondragend", "ondragenter", "ondragleave", "ondragover", "ondragstart",
    "ondrop",
    "ondevicemotion", "ondeviceorientation", "ondeviceorientationabsolute",
    "ononline", "onoffline", "onlanguagechange", "onorientationchange",
    "onafterprint", "onbeforeprint", "onbeforetoggle", "onbeforematch", "onbeforefilter",
    "onbeforeinstallprompt", "onappinstalled", "onpageswap", "onpagereveal",
    "oncommand", "oncomplete",
    "onmove", "onresize", "onscrollend", "onscrollsnapchange", "onscrollsnapchanging",
    "onoverscrollstart", "onoverscrollchanging", "onoverscrollend", "onoverscrollcancel",
    "ongamepadconnected", "ongamepaddisconnected", "ongamepadrawinputchanged",
    "oncontextlost", "oncontextrestored",
    "oncancel", "onclose", "oncuechange", "onformdata", "onmousewheel",
    "onpointerrawupdate", "onsearch", "onsecuritypolicyviolation", "onslotchange",
    "ontimezonechange", "ontoggle", "onbeforexrselect",
    "oncontentvisibilityautostatechange",
    "onwebkitanimationstart", "onwebkitanimationend", "onwebkitanimationiteration",
    "onwebkittransitionend",
)


def _install_handler_slots(cls, names):
    """Create one getter/setter pair per event-handler attribute.

    ``_raw_callback`` resolves a callback by *name* from the class ``__mro__``, so
    one generic accessor cannot serve several attribute names; generated per slot
    once at import time.
    """
    attributes = []
    for name in names:
        def getter(self, _name=name):
            return self._handlers.get(_name, pyv8.Null)

        def setter(self, value, _name=name):
            self._handlers[_name] = event_handler(value)

        getter.__name__ = "get_" + name
        setter.__name__ = "set_" + name
        setattr(cls, getter.__name__, getter)
        setattr(cls, setter.__name__, setter)
        attributes.append(AttributeSpec(name=name, getter=getter.__name__, setter=setter.__name__,
                                        location=FlagLocation.kInstance))
    return tuple(attributes)


EventTarget.__v8_idl_spec__ = EventTarget.__v8_idl_spec__.replace(
    attributes=EventTarget.__v8_idl_spec__.attributes
    + _install_handler_slots(EventTarget, _HANDLER_SLOTS)
)
