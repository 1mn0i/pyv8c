"""Navigator, geolocation, and browser-compatible plugin collections."""
import time

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import FeatureImplementation, LazyValueStore, interface
from ...foundation.arguments import (
    MISSING,
    boolean,
    callback_value,
    dictionary,
    event_handler,
    integer,
    is_nullish,
    missing_error,
)
from ...foundation.environment import BrowserEnvironment, MimeTypeConfig
from ...foundation.errors import V8TypeError
from ...foundation.realm import post_or_call
from ..dom.events import EventTarget
from ..dom.mixins import LegacyNamedPropertyMixin, ReadOnlyIndexedPropertyMixin
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

    # Declared per subclass through ``__v8_idl_spec__``;
    # the bodies only depend on ``_indexed_items``/``_named_item`` and the
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
        self._config = MimeTypeConfig.from_value(config or {"type": "application/pdf"})
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
    environment = BrowserEnvironment.from_value(environment)
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
        self._environment = BrowserEnvironment.from_value(environment)
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


class _NavigatorFeatureValues(LazyValueStore):
    __slots__ = ("_navigator",)

    def __init__(self, navigator):
        super().__init__()
        self._navigator = navigator

    def _create_value(self, name):
        navigator = self._navigator
        environment = navigator._environment
        services = navigator._services
        if name == "connection":
            return NetworkInformation(environment.connection, services=services)
        if name == "battery":
            return BatteryManager(environment.battery, services=services)
        if name == "geolocation":
            return Geolocation(services=services, environment=environment)
        if name == "webkitTemporaryStorage":
            return DeprecatedStorageQuota(
                environment.temporary_storage_quota, services=services
            )
        if name == "webkitPersistentStorage":
            return DeprecatedStorageQuota(
                environment.persistent_storage_quota, services=services
            )
        if name in ("plugins", "mimeTypes"):
            plugins, mime_types = create_plugin_collections(environment)
            self._cache("plugins", plugins)
            self._cache("mimeTypes", mime_types)
            return plugins if name == "plugins" else mime_types
        raise AttributeError(f"unknown Navigator feature value: {name!r}")


@interface('Navigator', None)
class Navigator:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="userAgent", getter="get_userAgent", side_effect=1),
            AttributeSpec(name="appName", getter="get_appName", side_effect=1),
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
            AttributeSpec(name="locks", getter="get_locks", side_effect=1),
            AttributeSpec(name="cookieEnabled", getter="get_cookieEnabled", side_effect=1),
            AttributeSpec(name="pdfViewerEnabled", getter="get_pdfViewerEnabled", side_effect=1),
            AttributeSpec(name="doNotTrack", getter="get_doNotTrack", side_effect=1),
            AttributeSpec(name="webdriver", getter="get_webdriver", side_effect=1),
            AttributeSpec(name="connection", getter="get_connection", side_effect=1),
            AttributeSpec(name="webkitTemporaryStorage", getter="get_webkitTemporaryStorage", side_effect=1),
            AttributeSpec(name="webkitPersistentStorage", getter="get_webkitPersistentStorage", side_effect=1),
            AttributeSpec(name="geolocation", getter="get_geolocation", side_effect=1),
            AttributeSpec(name="plugins", getter="get_plugins", side_effect=1),
            AttributeSpec(name="mimeTypes", getter="get_mimeTypes", side_effect=1),
        ),
        methods=(MethodSpec(name="getBattery", callback="fn_getBattery"),),
        constructor_behavior=0,
        has_constructor=1,
    )

    def __init__(self, user_agent=None, services=None, environment=None, worker=False, **_):
        self._services = services
        self._environment = BrowserEnvironment.from_value(environment)
        self._worker = bool(worker)
        if user_agent is not None:
            self._environment = BrowserEnvironment.from_value({"user_agent": user_agent}, base=self._environment)
        self._features = _NavigatorFeatureValues(self)

    def get_userAgent(self): return self._environment.user_agent
    # Frozen compatibility values, as Chrome reports them.
    def get_appName(self): return "Netscape"
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
    #: This browser keeps cookies and shows PDFs, the way Chrome reports both.
    def get_cookieEnabled(self): return True
    def get_pdfViewerEnabled(self): return True
    def get_locks(self):
        """Its own manager, in a secure context only -- where Chrome exposes one."""
        if self._worker or self._services is None:
            return pyv8.Undefined
        document = getattr(getattr(self._services, "document_services", None), "document", None)
        if not is_secure_context(getattr(document, "url", "")):
            return pyv8.Undefined
        manager = getattr(self, "_locks", None)
        if manager is None:
            manager = self._locks = LockManager(services=self._services)
        return manager
    def get_doNotTrack(self): return pyv8.Null
    def get_webdriver(self): return self._environment.webdriver
    def get_connection(self): return self._features.value("connection")
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
    def fn_getBattery(self, *_extra):
        battery = self._features.value("battery")
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(battery) if realm is not None else battery


__all__ = [
    "Navigator", "Geolocation", "Plugin", "PluginArray", "MimeType", "MimeTypeArray",
    "NetworkInformation", "BatteryManager", "DeprecatedStorageQuota",
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
    },
    {
        "navigator": lambda window: Navigator(
            services=window._services,
            environment=getattr(getattr(window._services, "realm_services", None), "environment", None),
        ),
    },
)
