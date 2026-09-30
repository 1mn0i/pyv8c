"""Navigator, geolocation, and browser-compatible plugin collections."""
import json
import time
from urllib.parse import urljoin

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import FeatureImplementation, LazyValueStore, interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    callback_value,
    dictionary,
    dom_string,
    event_handler,
    integer,
    is_nullish,
    missing_error,
    sequence,
)
from ...foundation.environment import BrowserEnvironment, MimeTypeView
from ...foundation.errors import V8TypeError, dom_exception
from ...foundation.realm import post_or_call
from ...foundation.urls import origin_string
from ..dom.events import EventTarget
from ..dom.mixins import LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin
from .accessories import Scheduling
from .locks import LockManager
from .utils import is_secure_context
from ..dom.utils import ITERABLE_FLAGS


class _LegacyNamedCollection(LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin):
    """Readonly indexed and named properties shared by plugin collections."""

    __v8_idl_spec__ = IdlSpec(
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_query="__v8_name_query__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
    )

    def _item_argument(self, args, api_name):
        if not args:
            raise V8TypeError(
                f"Failed to execute 'item' on '{api_name}': 1 argument required, but only 0 present."
            )
        return integer(args[0], bits=32)

    def _named_argument(self, args, api_name):
        if not args:
            raise V8TypeError(
                f"Failed to execute 'namedItem' on '{api_name}': 1 argument required, but only 0 present."
            )
        return str(args[0])

    # The bodies depend only on ``_indexed_items``/``_named_item`` and the
    # registered interface name, so they live here once.
    def get_length(self): return len(self._indexed_items())
    def fn_item(self, *args):
        return self._item_at(self._item_argument(args, self._interface_name()))
    def fn_namedItem(self, *args):
        return self._named_item(self._named_argument(args, self._interface_name()))


@interface('MimeType', None)
class MimeType:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="suffixes", getter="get_suffixes", side_effect=1),
            AttributeSpec(name="description", getter="get_description", side_effect=1),
            AttributeSpec(name="enabledPlugin", getter="get_enabledPlugin", side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="MimeType",
        has_constructor=1,
    )

    def __init__(self, config=None, enabled_plugin=None, **_):
        self._config = config or MimeTypeView("application/pdf", "", "")
        self._enabled_plugin = enabled_plugin

    def get_type(self): return self._config.type
    def get_suffixes(self): return self._config.suffixes
    def get_description(self): return self._config.description
    def get_enabledPlugin(self): return self._enabled_plugin if self._enabled_plugin is not None else pyv8.Null


@interface('Plugin', None)
class Plugin(_LegacyNamedCollection):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="filename", getter="get_filename", side_effect=1),
            AttributeSpec(name="description", getter="get_description", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
        ),
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="namedItem", callback="fn_namedItem", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="Plugin",
        has_constructor=1,
        array=ITERABLE_FLAGS,
    )

    def __init__(self, config, **_):
        self._config = config
        self._mime_types = tuple(MimeType(mime, enabled_plugin=self) for mime in config.mime_types)

    def _indexed_items(self): return self._mime_types
    def _supported_names(self): return [item.get_type() for item in self._mime_types]
    def _named_item(self, name):
        return next((item for item in self._mime_types if item.get_type() == name), pyv8.Null)
    def get_name(self): return self._config.name
    def get_filename(self): return self._config.filename
    def get_description(self): return self._config.description


@interface('PluginArray', None)
class PluginArray(_LegacyNamedCollection):
    _prototype_names = _LegacyNamedCollection._prototype_names | {"refresh"}
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="namedItem", callback="fn_namedItem", length=1, side_effect=1),
            MethodSpec(name="refresh", callback="fn_refresh"),
        ),
        constructor_behavior=0,
        to_string_tag="PluginArray",
        has_constructor=1,
        array=ITERABLE_FLAGS,
    )

    def __init__(self, configs=(), **_):
        self._plugins = tuple(Plugin(config) for config in configs)

    def _indexed_items(self): return self._plugins
    def _supported_names(self): return [item.get_name() for item in self._plugins]
    def _named_item(self, name):
        return next((item for item in self._plugins if item.get_name() == name), pyv8.Null)
    def fn_refresh(self, reload_documents=False, *_extra):
        if reload_documents is pyv8.Undefined:
            reload_documents = False
        if reload_documents is not pyv8.Undefined:
            reload_documents = boolean(reload_documents)
        return None


@interface('MimeTypeArray', None)
class MimeTypeArray(_LegacyNamedCollection):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="item", callback="fn_item", length=1, side_effect=1),
            MethodSpec(name="namedItem", callback="fn_namedItem", length=1, side_effect=1),
        ),
        constructor_behavior=0,
        to_string_tag="MimeTypeArray",
        has_constructor=1,
        array=ITERABLE_FLAGS,
    )

    def __init__(self, plugins=(), **_):
        items, seen = [], set()
        for plugin in plugins:
            for config in plugin._config.mime_types:
                if config.type not in seen:
                    seen.add(config.type)
                    # Chrome keeps this object distinct from Plugin[index].
                    items.append(MimeType(config, enabled_plugin=plugin))
        self._mime_types = tuple(items)

    def _indexed_items(self): return self._mime_types
    def _supported_names(self): return [item.get_type() for item in self._mime_types]
    def _named_item(self, name):
        return next((item for item in self._mime_types if item.get_type() == name), pyv8.Null)


def create_plugin_collections(environment):
    """Create stable Navigator plugin and MIME collections for one realm."""
    environment = BrowserEnvironment.from_dict(environment)
    plugins = PluginArray(environment.plugins)
    return plugins, MimeTypeArray(plugins._plugins)


@interface('Geolocation', None)
class Geolocation:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="getCurrentPosition", callback="fn_getCurrentPosition", length=3),
            MethodSpec(name="watchPosition", callback="fn_watchPosition", length=3),
            MethodSpec(name="clearWatch", callback="fn_clearWatch", length=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, services=None, environment=None, **_):
        self._services = services
        self._environment = BrowserEnvironment.from_dict(environment)
        self._watchers = {}
        self._next_watch = 1

    def _error(self, code, message): return {"code": code, "message": message}

    def _position(self):
        config = self._environment.geolocation
        if config.latitude is None or config.longitude is None:
            return None
        return {
            "coords": {
                "latitude": config.latitude, "longitude": config.longitude,
                "accuracy": config.accuracy if config.accuracy is not None else 0.0,
                "altitude": None, "altitudeAccuracy": None, "heading": None, "speed": None,
            },
            "timestamp": int(time.time() * 1000),
        }

    def _deliver(self, success, error, watch_id=None):
        def callback():
            if watch_id is not None and watch_id not in self._watchers:
                return
            if self._environment.geolocation.permission != "granted":
                if error is not None:
                    error(self._error(1, "User denied Geolocation"))
                return
            position = self._position()
            if position is None:
                if error is not None:
                    error(self._error(2, "Unable to determine the position"))
                return
            success(position)
        if self._services is not None:
            self._services.task_services.post_task(callback)

    def fn_getCurrentPosition(self, success=MISSING, error=None, options=None, *_extra):
        if success is MISSING:
            raise missing_error('Geolocation', 'getCurrentPosition', 1, 0, constructor=False)
        if success is not pyv8.Undefined:
            success = callback_value(success)
        if error is pyv8.Undefined:
            error = None
        if error is not pyv8.Undefined:
            if not is_nullish(error):
                error = callback_value(error)
        if options is pyv8.Undefined:
            options = None
        if options is not pyv8.Undefined:
            options = dictionary(options)
        if not callable(success):
            raise V8TypeError("success callback must be callable")
        if not is_nullish(error) and not callable(error):
            raise V8TypeError("error callback must be callable")
        self._deliver(success, error)

    def fn_watchPosition(self, success=MISSING, error=None, options=None, *_extra):
        if success is MISSING:
            raise missing_error('Geolocation', 'watchPosition', 1, 0, constructor=False)
        if success is not pyv8.Undefined:
            success = callback_value(success)
        if error is pyv8.Undefined:
            error = None
        if error is not pyv8.Undefined:
            if not is_nullish(error):
                error = callback_value(error)
        if options is pyv8.Undefined:
            options = None
        if options is not pyv8.Undefined:
            options = dictionary(options)
        if not callable(success):
            raise V8TypeError("success callback must be callable")
        if not is_nullish(error) and not callable(error):
            raise V8TypeError("error callback must be callable")
        ident = self._next_watch
        self._next_watch += 1
        self._watchers[ident] = (success, error)
        self._deliver(success, error, ident)
        return ident

    def fn_clearWatch(self, ident=MISSING, *_extra):
        if ident is MISSING:
            raise missing_error('Geolocation', 'clearWatch', 1, 0, constructor=False)
        ident = integer(ident, signed=True)
        self._watchers.pop(int(ident), None)


@interface('NetworkInformation', 'EventTarget')
class NetworkInformation(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onchange", getter="get_onchange", setter="set_onchange"),
            AttributeSpec(name="effectiveType", getter="get_effectiveType", side_effect=1),
            AttributeSpec(name="rtt", getter="get_rtt", side_effect=1),
            AttributeSpec(name="downlink", getter="get_downlink", side_effect=1),
            AttributeSpec(name="saveData", getter="get_saveData", side_effect=1),
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="downlinkMax", getter="get_downlinkMax", side_effect=1),
            AttributeSpec(name="ontypechange", getter="get_ontypechange", setter="set_ontypechange"),
        ),
        constructor_behavior=0,
        to_string_tag="NetworkInformation",
        has_constructor=1,
    )

    def __init__(self, config, services=None, **_):
        super().__init__(services=services)
        self._config = config

    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", event_handler(value))
    def get_effectiveType(self): return self._config.effective_type
    def get_rtt(self): return self._config.rtt
    def get_downlink(self): return self._config.downlink
    def get_saveData(self): return self._config.save_data
    def get_type(self): return self._config.type
    def get_downlinkMax(self): return self._config.downlink_max
    def get_ontypechange(self): return self._get_handler("ontypechange")
    def set_ontypechange(self, value): self._set_handler("ontypechange", event_handler(value))


@interface('BatteryManager', 'EventTarget')
class BatteryManager(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="charging", getter="get_charging", side_effect=1),
            AttributeSpec(name="chargingTime", getter="get_chargingTime", side_effect=1),
            AttributeSpec(name="dischargingTime", getter="get_dischargingTime", side_effect=1),
            AttributeSpec(name="level", getter="get_level", side_effect=1),
            AttributeSpec(name="onchargingchange", getter="get_onchargingchange", setter="set_onchargingchange"),
            AttributeSpec(name="onchargingtimechange", getter="get_onchargingtimechange", setter="set_onchargingtimechange"),
            AttributeSpec(name="ondischargingtimechange", getter="get_ondischargingtimechange", setter="set_ondischargingtimechange"),
            AttributeSpec(name="onlevelchange", getter="get_onlevelchange", setter="set_onlevelchange"),
        ),
        constructor_behavior=0,
        to_string_tag="BatteryManager",
        has_constructor=1,
    )

    def __init__(self, config, services=None, **_):
        super().__init__(services=services)
        self._config = config

    def get_charging(self): return self._config.charging
    def get_chargingTime(self): return self._config.charging_time
    def get_dischargingTime(self): return self._config.discharging_time
    def get_level(self): return self._config.level
    def get_onchargingchange(self): return self._get_handler("onchargingchange")
    def set_onchargingchange(self, value): self._set_handler("onchargingchange", event_handler(value))
    def get_onchargingtimechange(self): return self._get_handler("onchargingtimechange")
    def set_onchargingtimechange(self, value): self._set_handler("onchargingtimechange", event_handler(value))
    def get_ondischargingtimechange(self): return self._get_handler("ondischargingtimechange")
    def set_ondischargingtimechange(self, value): self._set_handler("ondischargingtimechange", event_handler(value))
    def get_onlevelchange(self): return self._get_handler("onlevelchange")
    def set_onlevelchange(self, value): self._set_handler("onlevelchange", event_handler(value))


@interface('DeprecatedStorageQuota', None)
class DeprecatedStorageQuota:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="queryUsageAndQuota", callback="fn_queryUsageAndQuota", length=1),
            MethodSpec(name="requestQuota", callback="fn_requestQuota", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="DeprecatedStorageQuota",
        has_constructor=1,
    )

    def __init__(self, config, services=None, **_):
        self._config = config
        self._services = services

    @staticmethod
    def _optional_callback(value):
        if value is pyv8.Undefined or is_nullish(value):
            return None
        return callback_value(value)

    def fn_queryUsageAndQuota(self, success=MISSING, error=None, *_extra):
        if success is MISSING:
            raise missing_error('DeprecatedStorageQuota', 'queryUsageAndQuota', 1, 0, constructor=False)
        success = callback_value(success)
        self._optional_callback(error)
        post_or_call(self._services, success, self._config.usage, self._config.quota)

    def fn_requestQuota(self, requested=MISSING, success=None, error=None, *_extra):
        if requested is MISSING:
            raise missing_error('DeprecatedStorageQuota', 'requestQuota', 1, 0, constructor=False)
        requested = integer(requested, bits=64)
        success = self._optional_callback(success)
        self._optional_callback(error)
        post_or_call(self._services, success, min(requested, self._config.quota))


#: The three members every ``getHighEntropyValues`` result carries.
_LOW_ENTROPY_HINTS = frozenset(("brands", "mobile", "platform"))


@interface('NavigatorUAData', None)
class NavigatorUAData:
    """``navigator.userAgentData``; not ``[SameObject]``, so every read hands
    out a new one.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="brands", getter="get_brands", side_effect=1),
            AttributeSpec(name="mobile", getter="get_mobile", side_effect=1),
            AttributeSpec(name="platform", getter="get_platform", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getHighEntropyValues", callback="fn_getHighEntropyValues", length=1),
            MethodSpec(name="toJSON", callback="fn_toJSON"),
        ),
        constructor_behavior=0,
        has_constructor=1,
        to_string_tag="NavigatorUAData",
    )

    def __init__(self, environment=None, services=None, **_):
        self._services = services
        self._data = BrowserEnvironment.from_dict(environment).client_hints

    def get_brands(self): return _frozen(self._services, self._pairs(self._data.brands))
    def get_mobile(self): return self._data.mobile
    def get_platform(self): return self._data.platform

    def fn_toJSON(self, *_extra):
        return {"brands": self._pairs(self._data.brands),
                "mobile": self._data.mobile, "platform": self._data.platform}

    def fn_getHighEntropyValues(self, hints=MISSING, *_extra):
        """The hints asked for, and the low-entropy three either way."""
        services = self._services
        promise, resolve, reject = services.realm_services.make_promise()

        def settle():
            try:
                result = self._high_entropy(hints)
            except BaseException as error:
                # The hints convert when the promise settles, so a bad argument
                # rejects rather than throwing.
                reject(services.realm_services.js_error_value(error))
                return
            resolve(result)

        services.task_services.schedule(settle, 0, repeat=False)
        return promise

    def _high_entropy(self, hints):
        requested = frozenset(sequence(hints, lambda hint: str(dom_string(hint))))
        #: Alphabetical, the order Chrome's own result carries.
        values = {
            "architecture": self._data.architecture,
            "bitness": self._data.bitness,
            "brands": self._pairs(self._data.brands),
            "formFactors": list(self._data.form_factors),
            "fullVersionList": self._pairs(self._data.full_version_list),
            "mobile": self._data.mobile,
            "model": self._data.model,
            "platform": self._data.platform,
            "platformVersion": self._data.platform_version,
            "uaFullVersion": self._full_version(),
            "wow64": self._data.wow64,
        }
        return {name: value for name, value in values.items()
                if name in requested or name in _LOW_ENTROPY_HINTS}

    def _full_version(self):
        """The build's own version, from the brand list's Google Chrome entry."""
        return next((entry.version for entry in self._data.full_version_list
                     if entry.brand == "Google Chrome"), "")

    def _pairs(self, entries):
        return [{"brand": entry.brand, "version": entry.version} for entry in entries]


#: Chrome 153's ``getSupportedConstraints()`` answer.
_CONSTRAINTS = (
    "aspectRatio", "autoGainControl", "brightness", "channelCount", "colorTemperature",
    "contrast", "deviceId", "displaySurface", "echoCancellation", "exposureCompensation",
    "exposureMode", "exposureTime", "facingMode", "focusDistance", "focusMode", "frameRate",
    "groupId", "height", "iso", "latency", "noiseSuppression", "pan", "pointsOfInterest",
    "resizeMode", "restrictOwnAudio", "sampleRate", "sampleSize", "saturation", "sharpness",
    "suppressLocalAudioPlayback", "tilt", "torch", "voiceIsolation", "whiteBalanceMode",
    "width", "zoom",
)

#: The permission names Chrome 153 answers, and the ones it starts as granted.
_PERMISSION_NAMES = frozenset((
    "accelerometer", "background-fetch", "background-sync", "camera",
    "captured-surface-control", "clipboard-read", "clipboard-write", "display-capture",
    "geolocation", "gyroscope", "idle-detection", "keyboard-lock", "local-fonts",
    "magnetometer", "microphone", "midi", "notifications", "payment-handler",
    "periodic-background-sync", "persistent-storage", "pointer-lock", "push",
    "screen-wake-lock", "storage-access", "window-management",
))
_PERMISSION_GRANTED = frozenset((
    "accelerometer", "background-fetch", "background-sync", "clipboard-write", "gyroscope",
    "keyboard-lock", "magnetometer", "payment-handler", "pointer-lock", "screen-wake-lock",
    "storage-access",
))

#: The name ``PermissionStatus.name`` reports, which is not always the one queried.
_PERMISSION_SPEC_NAMES = {
    "accelerometer": "sensors", "background-fetch": "background_fetch",
    "background-sync": "background_sync", "camera": "video_capture",
    "clipboard-read": "clipboard_read", "clipboard-write": "clipboard_write",
    "display-capture": "display_capture", "gyroscope": "sensors",
    "idle-detection": "idle_detection", "local-fonts": "local_fonts",
    "magnetometer": "sensors", "microphone": "audio_capture",
    "payment-handler": "payment_handler", "periodic-background-sync": "periodic_background_sync",
    "persistent-storage": "durable_storage", "screen-wake-lock": "screen_wake_lock",
}

#: The ``MediaSessionAction`` values Chrome 153 accepts.
_SESSION_ACTIONS = frozenset((
    "enterpictureinpicture", "hangup", "nextslide", "nexttrack", "pause", "play",
    "previoustrack", "previousslide", "seekbackward", "seekforward", "seekto", "skipad",
    "stop", "togglecamera", "togglemicrophone",
))
_PLAYBACK_STATES = frozenset(("none", "paused", "playing"))
_SCRIPT_MIME_TYPES = ("application/ecmascript", "application/javascript",
                      "text/ecmascript", "text/javascript")


def _settled(services, *, value=None, error=None):
    """A promise in this realm, already settled."""
    promise, resolve, reject = services.realm_services.make_promise()
    if error is None:
        resolve(value)
    else:
        reject(services.realm_services.js_error_value(error))
    return promise


def _frozen(services, values):
    """A fresh frozen array, the way a sequence member hands one out."""
    return services.realm_services.evaluate_script("Object.freeze(" + json.dumps(values) + ")")


def _number_or_none(value):
    """The number *value* holds, or ``None`` when it holds something else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return None if value != value or value in (float("inf"), float("-inf")) else float(value)


@interface('MediaDeviceInfo', None)
class MediaDeviceInfo:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="deviceId", getter="get_deviceId", side_effect=1),
            AttributeSpec(name="kind", getter="get_kind", side_effect=1),
            AttributeSpec(name="label", getter="get_label", side_effect=1),
            AttributeSpec(name="groupId", getter="get_groupId", side_effect=1),
        ),
        methods=(MethodSpec(name="toJSON", callback="fn_toJSON", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="MediaDeviceInfo",
        has_constructor=1,
    )

    def __init__(self, kind="audiooutput", *, device_id="", label="", group_id="", **_):
        self._kind, self._device_id = kind, device_id
        self._label, self._group_id = label, group_id

    def get_deviceId(self): return self._device_id
    def get_kind(self): return self._kind
    def get_label(self): return self._label
    def get_groupId(self): return self._group_id
    def fn_toJSON(self, *_extra):
        return {"deviceId": self._device_id, "kind": self._kind,
                "label": self._label, "groupId": self._group_id}


@interface('MediaDevices', 'EventTarget')
class MediaDevices(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="ondevicechange", getter="get_ondevicechange",
                                 setter="set_ondevicechange"),),
        methods=(
            MethodSpec(name="enumerateDevices", callback="fn_enumerateDevices"),
            MethodSpec(name="getSupportedConstraints", callback="fn_getSupportedConstraints",
                       side_effect=1),
            MethodSpec(name="getUserMedia", callback="fn_getUserMedia"),
            MethodSpec(name="getDisplayMedia", callback="fn_getDisplayMedia"),
            MethodSpec(name="setCaptureHandleConfig", callback="fn_setCaptureHandleConfig"),
        ),
        constructor_behavior=0,
        to_string_tag="MediaDevices",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)
        self._services = services

    def get_ondevicechange(self): return self._get_handler("ondevicechange")
    def set_ondevicechange(self, value):
        self._set_handler("ondevicechange", event_handler(value))

    def fn_enumerateDevices(self, *_extra):
        # Nothing is ever picked here, which is the one device Chrome lists without one.
        return _settled(self._services, value=[MediaDeviceInfo()])

    def fn_getSupportedConstraints(self, *_extra):
        return {name: True for name in _CONSTRAINTS}

    def fn_getUserMedia(self, constraints=None, *_extra):
        options = {} if is_nullish(constraints) else dictionary(constraints)
        if not options.get("audio") and not options.get("video"):
            raise V8TypeError("Failed to execute 'getUserMedia' on 'MediaDevices': "
                              "At least one of audio and video must be requested")
        return _settled(self._services,
                        error=dom_exception("NotFoundError", "Requested device not found"))

    def fn_getDisplayMedia(self, options=None, *_extra):
        return _settled(self._services,
                        error=dom_exception("NotAllowedError", "Permission denied"))

    def fn_setCaptureHandleConfig(self, config=None, *_extra):
        if not is_nullish(config):
            dictionary(config)
        return None


@interface('PermissionStatus', 'EventTarget')
class PermissionStatus(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="state", getter="get_state", side_effect=1),
            AttributeSpec(name="onchange", getter="get_onchange", setter="set_onchange"),
        ),
        constructor_behavior=0,
        to_string_tag="PermissionStatus",
        has_constructor=1,
    )

    def __init__(self, name="", state="prompt", services=None, **_):
        super().__init__(services=services)
        self._name, self._state = name, state

    def get_name(self): return self._name
    def get_state(self): return self._state
    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", event_handler(value))


@interface('Permissions', None)
class Permissions:
    __v8_idl_spec__ = IdlSpec(
        methods=(MethodSpec(name="query", callback="fn_query", length=1),),
        constructor_behavior=0,
        to_string_tag="Permissions",
        has_constructor=1,
    )

    def __init__(self, services=None, environment=None, **_):
        self._services = services
        self._environment = BrowserEnvironment.from_dict(environment)

    def fn_query(self, descriptor=MISSING, *_extra):
        if descriptor is MISSING:
            raise missing_error('Permissions', 'query', 1, 0, constructor=False)
        name = dictionary(descriptor).get("name")
        if name not in _PERMISSION_NAMES:
            # The enum check rejects the promise rather than throwing at the call.
            return _settled(self._services, error=V8TypeError(
                "Failed to execute 'query' on 'Permissions': Failed to read the 'name' property "
                "from 'PermissionDescriptor': The provided value "
                f"'{name}' is not a valid enum value of type PermissionName."))
        if name == "push":
            return _settled(self._services, error=dom_exception(
                "NotSupportedError",
                "Failed to execute 'query' on 'Permissions': Push Permission without "
                "userVisibleOnly:true isn't supported yet."))
        return _settled(self._services, value=PermissionStatus(
            _PERMISSION_SPEC_NAMES.get(name, name), self._state(name), services=self._services))

    def _state(self, name):
        if name == "geolocation":
            granted = self._environment.geolocation.permission == "granted"
            return "granted" if granted else "prompt"
        if name in _PERMISSION_GRANTED:
            return "granted"
        return "denied" if name == "periodic-background-sync" else "prompt"


@interface('MediaMetadata', None)
class MediaMetadata:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="title", getter="get_title", setter="set_title"),
            AttributeSpec(name="artist", getter="get_artist", setter="set_artist"),
            AttributeSpec(name="album", getter="get_album", setter="set_album"),
            AttributeSpec(name="artwork", getter="get_artwork", setter="set_artwork"),
            AttributeSpec(name="chapterInfo", getter="get_chapterInfo", side_effect=1),
        ),
        constructor_behavior=1,
        to_string_tag="MediaMetadata",
        has_constructor=1,
    )

    def __init__(self, init=None, *, _realm=None, **_):
        self._services = _realm
        self._title = self._artist = self._album = ""
        self._images = []
        if not is_nullish(init):
            values = dictionary(init)
            self._title = str(dom_string(values.get("title", "")))
            self._artist = str(dom_string(values.get("artist", "")))
            self._album = str(dom_string(values.get("album", "")))
            if not is_nullish(values.get("artwork")):
                self.set_artwork(values["artwork"])

    def get_title(self): return self._title
    def set_title(self, value): self._title = str(dom_string(value))
    def get_artist(self): return self._artist
    def set_artist(self, value): self._artist = str(dom_string(value))
    def get_album(self): return self._album
    def set_album(self, value): self._album = str(dom_string(value))

    def get_artwork(self):
        """A fresh frozen array per read, the way Chrome hands one out."""
        return self._freeze(self._images)

    def set_artwork(self, value):
        base = self._base_url()
        self._images = [{"src": urljoin(base, str(dom_string(image.get("src", "")))),
                         "sizes": str(dom_string(image.get("sizes", ""))),
                         "type": str(dom_string(image.get("type", "")))}
                        for image in sequence(value, dictionary)]

    def get_chapterInfo(self):
        return self._freeze([])

    def _base_url(self):
        realm = getattr(self._services, "realm_services", None)
        return str(getattr(realm, "url", "") or "")

    def _freeze(self, values):
        return _frozen(self._services, values) if self._services is not None else values


@interface('MediaSession', None)
class MediaSession:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="metadata", getter="get_metadata", setter="set_metadata"),
            AttributeSpec(name="playbackState", getter="get_playbackState",
                          setter="set_playbackState"),
        ),
        methods=(
            MethodSpec(name="setActionHandler", callback="fn_setActionHandler", length=2),
            MethodSpec(name="setCameraActive", callback="fn_setCameraActive", length=1),
            MethodSpec(name="setMicrophoneActive", callback="fn_setMicrophoneActive", length=1),
            MethodSpec(name="setPositionState", callback="fn_setPositionState"),
        ),
        constructor_behavior=0,
        to_string_tag="MediaSession",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services
        self._metadata = None
        self._playback_state = "none"
        self._handlers = {}

    def get_metadata(self):
        return pyv8.Null if self._metadata is None else self._metadata

    def set_metadata(self, value):
        if is_nullish(value):
            self._metadata = None
            return
        if not isinstance(value, MediaMetadata):
            raise V8TypeError("Failed to set the 'metadata' property on 'MediaSession': "
                              "Failed to convert value to 'MediaMetadata'.")
        self._metadata = value

    def get_playbackState(self): return self._playback_state

    def set_playbackState(self, value):
        state = str(dom_string(value))
        if state in _PLAYBACK_STATES:
            self._playback_state = state

    def fn_setActionHandler(self, action=MISSING, handler=MISSING, *_extra):
        if action is MISSING:
            raise missing_error('MediaSession', 'setActionHandler', 2, 0, constructor=False)
        if handler is MISSING:
            raise missing_error('MediaSession', 'setActionHandler', 2, 1, constructor=False)
        name = str(dom_string(action))
        if name not in _SESSION_ACTIONS:
            raise V8TypeError("Failed to execute 'setActionHandler' on 'MediaSession': "
                              f"The provided value '{name}' is not a valid enum value of "
                              "type MediaSessionAction.")
        if is_nullish(handler):
            self._handlers.pop(name, None)
            return None
        if not callable(handler):
            raise V8TypeError("Failed to execute 'setActionHandler' on 'MediaSession': "
                              "parameter 2 is not of type 'Function'.")
        self._handlers[name] = callback_value(handler)
        return None

    def fn_setCameraActive(self, active=MISSING, *_extra):
        if active is MISSING:
            raise missing_error('MediaSession', 'setCameraActive', 1, 0, constructor=False)
        self._camera = boolean(active)
        return None

    def fn_setMicrophoneActive(self, active=MISSING, *_extra):
        if active is MISSING:
            raise missing_error('MediaSession', 'setMicrophoneActive', 1, 0, constructor=False)
        self._microphone = boolean(active)
        return None

    def fn_setPositionState(self, state=pyv8.Undefined, *_extra):
        if is_nullish(state):
            return None
        values = dictionary(state)
        duration = _number_or_none(values.get("duration"))
        if duration is None or duration < 0:
            raise V8TypeError("Failed to execute 'setPositionState' on 'MediaSession': "
                              "The provided duration cannot be less than zero.")
        position = _number_or_none(values.get("position")) or 0.0
        if position > duration:
            raise V8TypeError("Failed to execute 'setPositionState' on 'MediaSession': "
                              "The provided position cannot be greater than the duration.")
        rate = _number_or_none(values.get("playbackRate")) or 1.0
        if rate <= 0:
            raise V8TypeError("Failed to execute 'setPositionState' on 'MediaSession': "
                              "The provided playback rate is not in the supported range.")
        return None


@interface('ServiceWorkerContainer', 'EventTarget')
class ServiceWorkerContainer(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="controller", getter="get_controller", side_effect=1),
            AttributeSpec(name="ready", getter="get_ready", side_effect=1),
            AttributeSpec(name="oncontrollerchange", getter="get_oncontrollerchange",
                          setter="set_oncontrollerchange"),
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
            AttributeSpec(name="onmessageerror", getter="get_onmessageerror",
                          setter="set_onmessageerror"),
        ),
        methods=(
            MethodSpec(name="getRegistration", callback="fn_getRegistration"),
            MethodSpec(name="getRegistrations", callback="fn_getRegistrations"),
            MethodSpec(name="register", callback="fn_register", length=1),
            MethodSpec(name="startMessages", callback="fn_startMessages"),
        ),
        constructor_behavior=0,
        to_string_tag="ServiceWorkerContainer",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        super().__init__(services=services)
        self._services = services
        self._ready = None

    def get_controller(self): return pyv8.Null

    def get_ready(self):
        # Nothing is ever registered here, so this promise never settles.
        if self._ready is None:
            self._ready = self._services.realm_services.make_promise()[0]
        return self._ready

    def get_oncontrollerchange(self): return self._get_handler("oncontrollerchange")
    def set_oncontrollerchange(self, value):
        self._set_handler("oncontrollerchange", event_handler(value))
    def get_onmessage(self): return self._get_handler("onmessage")
    def set_onmessage(self, value): self._set_handler("onmessage", event_handler(value))
    def get_onmessageerror(self): return self._get_handler("onmessageerror")
    def set_onmessageerror(self, value):
        self._set_handler("onmessageerror", event_handler(value))

    def fn_getRegistration(self, url=None, *_extra):
        if not is_nullish(url):
            dom_string(url)
        return _settled(self._services, value=pyv8.Undefined)

    def fn_getRegistrations(self, *_extra):
        return _settled(self._services, value=[])

    def fn_startMessages(self, *_extra): return None

    def fn_register(self, url=MISSING, options=None, *_extra):
        if url is MISSING:
            raise missing_error('ServiceWorkerContainer', 'register', 1, 0, constructor=False)
        if not is_nullish(options):
            dictionary(options)
        base = str(getattr(self._services.realm_services, "url", "") or "")
        target = urljoin(base, str(dom_string(url)))
        script_origin, origin = origin_string(target), origin_string(base)
        if script_origin != origin:
            return _settled(self._services, error=dom_exception(
                "SecurityError",
                "Failed to register a ServiceWorker: The origin of the provided scriptURL "
                f"('{script_origin}') does not match the current origin ('{origin}')."))
        scope = target[:target.rfind("/") + 1]
        content_type = self._script_type(target)
        if content_type is None:
            return _settled(self._services, error=V8TypeError(
                "Failed to register a ServiceWorker: The script could not be fetched."))
        if not any(content_type.startswith(name) for name in _SCRIPT_MIME_TYPES):
            return _settled(self._services, error=dom_exception(
                "SecurityError",
                f"Failed to register a ServiceWorker for scope ('{scope}') with script "
                f"('{target}'): The script has an unsupported MIME type ('{content_type}')."))
        return _settled(self._services, error=dom_exception(
            "NotSupportedError", "Service workers are not implemented here."))

    def _script_type(self, target):
        """The script's MIME type, or ``None`` when fetching it failed."""
        request = {"method": "GET", "mode": "same-origin", "destination": "serviceworker",
                   "resource_type": "script"}
        try:
            response = self._services.resource_services.load_resource(target, request)
        except BaseException:
            return None
        headers = getattr(response, "headers", None)
        if headers is None:
            return ""
        return str(headers.fn_get("content-type") or "").split(";")[0].strip().lower()


@interface('CredentialsContainer', None)
class CredentialsContainer:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="create", callback="fn_create"),
            MethodSpec(name="get", callback="fn_get"),
            MethodSpec(name="preventSilentAccess", callback="fn_preventSilentAccess"),
            MethodSpec(name="store", callback="fn_store", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="CredentialsContainer",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services

    def fn_get(self, options=None, *_extra):
        _values, kinds = self._kinds(options)
        if not kinds:
            return _settled(self._services, error=dom_exception(
                "NotSupportedError", "No credential type was specified in the request."))
        if "publicKey" in kinds:
            return _settled(self._services, error=dom_exception(
                "NotSupportedError", "WebAuthn is not implemented here."))
        # Nothing is stored here either, so both credential kinds find nothing.
        return _settled(self._services, value=pyv8.Null)

    def fn_create(self, options=None, *_extra):
        values, kinds = self._kinds(options)
        if len(kinds) != 1:
            return _settled(self._services, error=dom_exception(
                "NotSupportedError",
                "Only exactly one of 'password', 'federated', and 'publicKey' credential "
                "types are currently supported."))
        if kinds[0] == "password" and "id" not in dictionary(values["password"]):
            raise V8TypeError(
                "Failed to execute 'create' on 'CredentialsContainer': Failed to read the "
                "'password' property from 'CredentialCreationOptions': Failed to read the "
                "'id' property from 'CredentialData': Required member is undefined.")
        return _settled(self._services, error=dom_exception(
            "NotSupportedError", "Credential creation is not implemented here."))

    def fn_preventSilentAccess(self, *_extra):
        return _settled(self._services, value=pyv8.Undefined)

    def fn_store(self, credential=MISSING, *_extra):
        if credential is MISSING:
            raise missing_error('CredentialsContainer', 'store', 1, 0, constructor=False)
        # No Credential interface is modelled, so nothing can be one.
        raise V8TypeError("Failed to execute 'store' on 'CredentialsContainer': "
                          "parameter 1 is not of type 'Credential'.")

    def _kinds(self, options):
        values = {} if is_nullish(options) else dictionary(options)
        return values, [name for name in ("password", "federated", "publicKey")
                        if values.get(name)]


@interface('Bluetooth', 'EventTarget')
class Bluetooth(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="getAvailability", callback="fn_getAvailability"),
            MethodSpec(name="requestDevice", callback="fn_requestDevice"),
        ),
        constructor_behavior=0,
        to_string_tag="Bluetooth",
        has_constructor=1,
    )

    def __init__(self, services=None, environment=None, **_):
        super().__init__(services=services)
        self._services = services
        self._environment = BrowserEnvironment.from_dict(environment)

    def fn_getAvailability(self, *_extra):
        return _settled(self._services, value=bool(self._environment.bluetooth.available))

    def fn_requestDevice(self, options=None, *_extra):
        if not is_nullish(options):
            dictionary(options)
        # Chrome waits for a chooser here; with none to show, the request is refused.
        return _settled(self._services, error=dom_exception(
            "NotFoundError", "User cancelled the requestDevice() chooser."))


@interface('StorageManager', None)
class StorageManager:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="estimate", callback="fn_estimate"),
            MethodSpec(name="persisted", callback="fn_persisted"),
            MethodSpec(name="getDirectory", callback="fn_getDirectory"),
            MethodSpec(name="persist", callback="fn_persist"),
        ),
        constructor_behavior=0,
        to_string_tag="StorageManager",
        has_constructor=1,
    )

    def __init__(self, services=None, environment=None, **_):
        self._services = services
        self._environment = BrowserEnvironment.from_dict(environment)

    def fn_estimate(self, *_extra):
        temporary = self._environment.storage.temporary
        return _settled(self._services, value={"quota": temporary.quota,
                                               "usage": temporary.usage,
                                               "usageDetails": {}})

    def fn_persisted(self, *_extra):
        return _settled(self._services, value=False)

    def fn_persist(self, *_extra):
        return _settled(self._services, value=False)

    def fn_getDirectory(self, *_extra):
        return _settled(self._services, error=dom_exception(
            "NotSupportedError", "The origin private file system is not implemented here."))


def _navigator_values(navigator):
    """The sub-objects a Navigator hands out, built on first read."""
    environment = navigator._environment
    services = navigator._services

    def collections():
        collection, mime_types = create_plugin_collections(environment)
        values._cache("plugins", collection)
        values._cache("mimeTypes", mime_types)
        return collection, mime_types

    def plugins():
        return collections()[0]

    def mime_types():
        return collections()[1]

    values = LazyValueStore({
        "connection": lambda: NetworkInformation(environment.connection, services=services),
        "scheduling": lambda: Scheduling(),
        "locks": lambda: LockManager(services=services),
        "battery": lambda: BatteryManager(environment.battery, services=services),
        "geolocation": lambda: Geolocation(services=services, environment=environment),
        "webkitTemporaryStorage": lambda: DeprecatedStorageQuota(
            environment.storage.temporary, services=services),
        "webkitPersistentStorage": lambda: DeprecatedStorageQuota(
            environment.storage.persistent, services=services),
        "plugins": plugins,
        "mimeTypes": mime_types,
        "mediaDevices": lambda: MediaDevices(services=services),
        "permissions": lambda: Permissions(services=services, environment=environment),
        "mediaSession": lambda: MediaSession(services=services),
        "serviceWorker": lambda: ServiceWorkerContainer(services=services),
        "credentials": lambda: CredentialsContainer(services=services),
        "bluetooth": lambda: Bluetooth(services=services, environment=environment),
        "storage": lambda: StorageManager(services=services, environment=environment),
    })
    return values


@interface('Navigator', None)
class Navigator:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="userAgent", getter="get_userAgent", side_effect=1),
            AttributeSpec(name="userAgentData", getter="get_userAgentData", side_effect=1),
            AttributeSpec(name="appName", getter="get_appName", side_effect=1),
            AttributeSpec(name="appCodeName", getter="get_appCodeName", side_effect=1),
            AttributeSpec(name="appVersion", getter="get_appVersion", side_effect=1),
            AttributeSpec(name="product", getter="get_product", side_effect=1),
            AttributeSpec(name="productSub", getter="get_productSub", side_effect=1),
            AttributeSpec(name="platform", getter="get_platform", side_effect=1),
            AttributeSpec(name="language", getter="get_language", side_effect=1),
            AttributeSpec(name="languages", getter="get_languages", side_effect=1),
            AttributeSpec(name="onLine", getter="get_onLine", side_effect=1),
            AttributeSpec(name="hardwareConcurrency", getter="get_hardwareConcurrency", side_effect=1),
            AttributeSpec(name="deviceMemory", getter="get_deviceMemory", side_effect=1),
            AttributeSpec(name="maxTouchPoints", getter="get_maxTouchPoints", side_effect=1),
            AttributeSpec(name="vendor", getter="get_vendor", side_effect=1),
            AttributeSpec(name="vendorSub", getter="get_vendorSub", side_effect=1),
            AttributeSpec(name="locks", getter="get_locks", side_effect=1),
            AttributeSpec(name="cookieEnabled", getter="get_cookieEnabled", side_effect=1),
            AttributeSpec(name="pdfViewerEnabled", getter="get_pdfViewerEnabled", side_effect=1),
            AttributeSpec(name="doNotTrack", getter="get_doNotTrack", side_effect=1),
            AttributeSpec(name="webdriver", getter="get_webdriver", side_effect=1),
            AttributeSpec(name="connection", getter="get_connection", side_effect=1),
            AttributeSpec(name="scheduling", getter="get_scheduling", side_effect=1),
            AttributeSpec(name="webkitTemporaryStorage", getter="get_webkitTemporaryStorage", side_effect=1),
            AttributeSpec(name="webkitPersistentStorage", getter="get_webkitPersistentStorage", side_effect=1),
            AttributeSpec(name="geolocation", getter="get_geolocation", side_effect=1),
            AttributeSpec(name="plugins", getter="get_plugins", side_effect=1),
            AttributeSpec(name="mimeTypes", getter="get_mimeTypes", side_effect=1),
            AttributeSpec(name="mediaDevices", getter="get_mediaDevices", side_effect=1),
            AttributeSpec(name="permissions", getter="get_permissions", side_effect=1),
            AttributeSpec(name="mediaSession", getter="get_mediaSession", side_effect=1),
            AttributeSpec(name="serviceWorker", getter="get_serviceWorker", side_effect=1),
            AttributeSpec(name="credentials", getter="get_credentials", side_effect=1),
            AttributeSpec(name="bluetooth", getter="get_bluetooth", side_effect=1),
            AttributeSpec(name="storage", getter="get_storage", side_effect=1),
        ),
        methods=(
            MethodSpec(name="getBattery", callback="fn_getBattery"),
            MethodSpec(name="javaEnabled", callback="fn_javaEnabled"),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, user_agent=None, services=None, environment=None, worker=False, **_):
        self._services = services
        self._environment = BrowserEnvironment.from_dict(environment)
        self._worker = bool(worker)
        if user_agent is not None:
            self._environment = self._environment.overlay({"user_agent": user_agent})
        self._features = _navigator_values(self)

    def get_userAgent(self): return self._environment.user_agent
    def get_userAgentData(self):
        """A fresh UA-CH object per read, and only in a secure context."""
        services = self._services
        if services is None or not is_secure_context(services.realm_services.url):
            return pyv8.Undefined
        return NavigatorUAData(environment=self._environment, services=services)
    # Frozen compatibility values, as Chrome reports them.
    def get_appName(self): return "Netscape"
    def get_appCodeName(self): return "Mozilla"
    def get_product(self): return "Gecko"
    def get_productSub(self): return "20030107"
    def get_appVersion(self): return self._environment.app_version
    def get_platform(self): return self._environment.platform
    def get_language(self): return self._environment.language
    def get_languages(self): return list(self._environment.languages)
    def get_onLine(self): return self._environment.on_line
    def get_hardwareConcurrency(self): return self._environment.hardware_concurrency
    def get_deviceMemory(self): return self._environment.device_memory
    def get_maxTouchPoints(self): return self._environment.max_touch_points
    def get_vendor(self): return self._environment.vendor
    def get_vendorSub(self): return ""
    #: This browser keeps cookies and shows PDFs, the way Chrome reports both.
    def get_cookieEnabled(self): return True
    def get_pdfViewerEnabled(self): return True
    def get_locks(self):
        return self._features.value("locks") if self._secure() else pyv8.Undefined
    def get_doNotTrack(self): return pyv8.Null
    def get_webdriver(self): return self._environment.webdriver
    def get_connection(self): return self._features.value("connection")
    def get_scheduling(self): return self._features.value("scheduling")
    def get_webkitTemporaryStorage(self):
        if self._worker:
            return pyv8.Undefined
        return self._features.value("webkitTemporaryStorage")
    def get_webkitPersistentStorage(self):
        if self._worker:
            return pyv8.Undefined
        return self._features.value("webkitPersistentStorage")
    def get_geolocation(self):
        if self._services is None or self._worker:
            return pyv8.Undefined
        return self._features.value("geolocation")
    def get_plugins(self):
        return pyv8.Undefined if self._worker else self._features.value("plugins")
    def get_mimeTypes(self):
        return pyv8.Undefined if self._worker else self._features.value("mimeTypes")
    def _realm_url(self):
        realm = getattr(self._services, "realm_services", None)
        return str(getattr(realm, "url", "") or "")
    def _secure(self, *, worker=False):
        """Whether Chrome exposes a secure-context member here."""
        if self._services is None or (self._worker and not worker):
            return False
        return is_secure_context(self._realm_url())
    def get_mediaDevices(self):
        return self._features.value("mediaDevices") if self._secure() else pyv8.Undefined
    def get_mediaSession(self):
        return pyv8.Undefined if self._worker else self._features.value("mediaSession")
    def get_serviceWorker(self):
        return self._features.value("serviceWorker") if self._secure() else pyv8.Undefined
    def get_credentials(self):
        return self._features.value("credentials") if self._secure() else pyv8.Undefined
    def get_bluetooth(self):
        return self._features.value("bluetooth") if self._secure() else pyv8.Undefined
    def get_storage(self):
        return self._features.value("storage") if self._secure(worker=True) else pyv8.Undefined
    def get_permissions(self):
        if self._services is None:
            return pyv8.Undefined
        return self._features.value("permissions")
    def fn_javaEnabled(self, *_extra):
        return pyv8.Undefined if self._worker else False
    def fn_getBattery(self, *_extra):
        battery = self._features.value("battery")
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(battery) if realm is not None else battery


__all__ = [
    "Navigator", "NavigatorUAData", "Geolocation", "Plugin", "PluginArray", "MimeType",
    "MimeTypeArray", "NetworkInformation", "BatteryManager", "DeprecatedStorageQuota",
    "MediaDeviceInfo", "MediaDevices", "Permissions", "PermissionStatus", "MediaMetadata",
    "MediaSession", "ServiceWorkerContainer", "CredentialsContainer", "Bluetooth",
    "StorageManager",
]


FEATURE = FeatureImplementation(
    {
        "MimeType": MimeType,
        "Plugin": Plugin,
        "PluginArray": PluginArray,
        "MimeTypeArray": MimeTypeArray,
        "Geolocation": Geolocation,
        "NetworkInformation": NetworkInformation,
        "BatteryManager": BatteryManager,
        "DeprecatedStorageQuota": DeprecatedStorageQuota,
        "Navigator": Navigator,
        "NavigatorUAData": NavigatorUAData,
        "MediaDeviceInfo": MediaDeviceInfo,
        "MediaDevices": MediaDevices,
        "Permissions": Permissions,
        "PermissionStatus": PermissionStatus,
        "MediaMetadata": MediaMetadata,
        "MediaSession": MediaSession,
        "ServiceWorkerContainer": ServiceWorkerContainer,
        "CredentialsContainer": CredentialsContainer,
        "Bluetooth": Bluetooth,
        "StorageManager": StorageManager,
    },
    {
        "navigator": lambda window: Navigator(
            services=window._services,
            environment=getattr(getattr(window._services, "realm_services", None), "environment", None),
        ),
    },
)
