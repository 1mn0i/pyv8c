"""Navigator, geolocation, and browser-compatible plugin collections."""

import math
import time

import pyv8
from pyv8.flag import FlagArrayProto, FlagExposed, V8Attribute
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from ...foundation.indexed_properties import ReadOnlyIndexedPropertyMixin
from ...foundation.environment import BrowserEnvironment, MimeTypeConfig
from ...foundation.errors import LegacyPropertyError, V8TypeError


_ITERABLE_FLAGS = (
    FlagArrayProto.iterator | FlagArrayProto.entries | FlagArrayProto.keys
    | FlagArrayProto.values | FlagArrayProto.forEach
)
_NAMED_ATTRIBUTES = V8Attribute.ReadOnly | V8Attribute.DontEnum
_OBJECT_PROTOTYPE_NAMES = frozenset((
    "constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty",
    "__lookupGetter__", "__lookupSetter__", "isPrototypeOf",
    "propertyIsEnumerable", "toString", "valueOf", "__proto__", "toLocaleString",
))


def _unsigned_long(value):
    """Apply Web IDL unsigned-long conversion used by item(index)."""
    if value is pyv8.Undefined or value is pyv8.Null or value is None:
        return 0
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(numeric):
        return 0
    return int(numeric) % (2 ** 32)


class _LegacyNamedCollection(ReadOnlyIndexedPropertyMixin):
    """Readonly indexed and named properties shared by plugin collections."""

    _prototype_names = _OBJECT_PROTOTYPE_NAMES | {"length", "item", "namedItem"}

    def _supported_names(self):
        raise NotImplementedError

    def _named_item(self, name):
        raise NotImplementedError

    def _visible_names(self):
        return [name for name in self._supported_names() if name not in self._prototype_names]

    def __v8_name_get__(self, name):
        if not isinstance(name, str) or name not in self._visible_names():
            return None
        return self._named_item(name)

    def __v8_name_set__(self, name, value):
        if isinstance(name, str) and name in self._visible_names():
            raise LegacyPropertyError.named(
                "set", name, self._interface_name(),
                "Named property setter is not supported.",
            )
        return None

    def __v8_name_query__(self, name):
        if not isinstance(name, str):
            return None
        return _NAMED_ATTRIBUTES if name in self._visible_names() else None

    def __v8_name_del__(self, name):
        if isinstance(name, str) and name in self._visible_names():
            raise LegacyPropertyError.named(
                "delete", name, self._interface_name(),
                "Named property deleter is not supported.",
            )
        return None

    def __v8_name_enum__(self):
        return self._visible_names()

    def _item_argument(self, args, api_name):
        if not args:
            raise V8TypeError(
                f"Failed to execute 'item' on '{api_name}': 1 argument required, but only 0 present."
            )
        return _unsigned_long(args[0])

    def _named_argument(self, args, api_name):
        if not args:
            raise V8TypeError(
                f"Failed to execute 'namedItem' on '{api_name}': 1 argument required, but only 0 present."
            )
        return str(args[0])


@interface("MimeType", None, constructible=False, tag="MimeType", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class MimeType:
    __v8_attribute__ = js_attributes(
        ("type", "get_type", None, 0, 1), ("suffixes", "get_suffixes", None, 0, 1),
        ("description", "get_description", None, 0, 1),
        ("enabledPlugin", "get_enabledPlugin", None, 0, 1),
    )

    def __init__(self, config=None, enabled_plugin=None, **_):
        self._config = MimeTypeConfig.from_value(config or {"type": "application/pdf"})
        self._enabled_plugin = enabled_plugin

    def get_type(self): return self._config.type
    def get_suffixes(self): return self._config.suffixes
    def get_description(self): return self._config.description
    def get_enabledPlugin(self): return self._enabled_plugin if self._enabled_plugin is not None else pyv8.Null


@interface("Plugin", None, constructible=False, tag="Plugin", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1, v8_array=_ITERABLE_FLAGS)
class Plugin(_LegacyNamedCollection):
    __v8_attribute__ = js_attributes(
        ("name", "get_name", None, 0, 1), ("filename", "get_filename", None, 0, 1),
        ("description", "get_description", None, 0, 1), ("length", "get_length", None, 0, 1),
    )
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))

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
    def get_length(self): return len(self._mime_types)
    def fn_item(self, *args): return self._item_at(self._item_argument(args, "Plugin"))
    def fn_namedItem(self, *args): return self._named_item(self._named_argument(args, "Plugin"))


@interface("PluginArray", None, constructible=False, tag="PluginArray", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1, v8_array=_ITERABLE_FLAGS)
class PluginArray(_LegacyNamedCollection):
    _prototype_names = _LegacyNamedCollection._prototype_names | {"refresh"}
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(
        ("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1),
        ("refresh", "fn_refresh", 0, 0),
    )

    def __init__(self, configs=(), **_):
        self._plugins = tuple(Plugin(config) for config in configs)

    def _indexed_items(self): return self._plugins
    def _supported_names(self): return [item.get_name() for item in self._plugins]
    def _named_item(self, name):
        return next((item for item in self._plugins if item.get_name() == name), pyv8.Null)
    def get_length(self): return len(self._plugins)
    def fn_item(self, *args): return self._item_at(self._item_argument(args, "PluginArray"))
    def fn_namedItem(self, *args): return self._named_item(self._named_argument(args, "PluginArray"))
    def fn_refresh(self, reload_documents=False): return None


@interface("MimeTypeArray", None, constructible=False, tag="MimeTypeArray", exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1, v8_array=_ITERABLE_FLAGS)
class MimeTypeArray(_LegacyNamedCollection):
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))

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
    def get_length(self): return len(self._mime_types)
    def fn_item(self, *args): return self._item_at(self._item_argument(args, "MimeTypeArray"))
    def fn_namedItem(self, *args): return self._named_item(self._named_argument(args, "MimeTypeArray"))


def create_plugin_collections(environment):
    """Create stable Navigator plugin and MIME collections for one realm."""
    environment = BrowserEnvironment.from_value(environment)
    plugins = PluginArray(environment.plugins)
    return plugins, MimeTypeArray(plugins._plugins)


@interface("Geolocation", None, exposed_in=("Window",))
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Geolocation:
    __v8_method__ = js_methods(
        ("getCurrentPosition", "fn_getCurrentPosition", 3, 0),
        ("watchPosition", "fn_watchPosition", 3, 0),
        ("clearWatch", "fn_clearWatch", 1, 0),
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

    def fn_getCurrentPosition(self, success, error=None, options=None):
        if not callable(success):
            raise V8TypeError("success callback must be callable")
        if error is not None and not callable(error):
            raise V8TypeError("error callback must be callable")
        self._deliver(success, error)

    def fn_watchPosition(self, success, error=None, options=None):
        if not callable(success):
            raise V8TypeError("success callback must be callable")
        if error is not None and not callable(error):
            raise V8TypeError("error callback must be callable")
        ident = self._next_watch
        self._next_watch += 1
        self._watchers[ident] = (success, error)
        self._deliver(success, error, ident)
        return ident

    def fn_clearWatch(self, ident): self._watchers.pop(int(ident), None)


@interface("Navigator", None)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Navigator:
    __v8_attribute__ = js_attributes(
        ("userAgent", "get_userAgent", None, 0, 1), ("platform", "get_platform", None, 0, 1),
        ("language", "get_language", None, 0, 1), ("languages", "get_languages", None, 0, 1),
        ("onLine", "get_onLine", None, 0, 1), ("hardwareConcurrency", "get_hardwareConcurrency", None, 0, 1),
        ("deviceMemory", "get_deviceMemory", None, 0, 1), ("maxTouchPoints", "get_maxTouchPoints", None, 0, 1),
        ("geolocation", "get_geolocation", None, 0, 1), ("plugins", "get_plugins", None, 0, 1),
        ("mimeTypes", "get_mimeTypes", None, 0, 1),
    )

    def __init__(self, user_agent=None, services=None, environment=None, worker=False, **_):
        self._environment = BrowserEnvironment.from_value(environment)
        self._worker = bool(worker)
        if user_agent is not None:
            self._environment = BrowserEnvironment.from_value({"user_agent": user_agent}, base=self._environment)
        self._geolocation = (
            Geolocation(services=services, environment=self._environment)
            if services is not None and not self._worker else pyv8.Undefined
        )
        self._plugins, self._mime_types = (
            create_plugin_collections(self._environment)
            if not self._worker else (pyv8.Undefined, pyv8.Undefined)
        )

    def get_userAgent(self): return self._environment.user_agent
    def get_platform(self): return self._environment.platform
    def get_language(self): return self._environment.language
    def get_languages(self): return list(self._environment.languages)
    def get_onLine(self): return self._environment.on_line
    def get_hardwareConcurrency(self): return self._environment.hardware_concurrency
    def get_deviceMemory(self): return self._environment.device_memory
    def get_maxTouchPoints(self): return self._environment.max_touch_points
    def get_geolocation(self): return self._geolocation
    def get_plugins(self): return self._plugins
    def get_mimeTypes(self): return self._mime_types


__all__ = [
    "Navigator", "Geolocation", "Plugin", "PluginArray", "MimeType", "MimeTypeArray",
]
