"""Immutable browser-environment configuration shared by all realms."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
DEFAULT_LANGUAGES = ("zh-CN", "en", "en-US")


def _number_validator(label, cast, boundary, boundary_message):
    """Build a ``(value, name) -> number`` validator with one boundary check."""

    def validate(value, name):
        if isinstance(value, bool):
            raise TypeError(f"{name} must be {label}")
        try:
            number = cast(value)
        except (TypeError, ValueError):
            raise TypeError(f"{name} must be {label}") from None
        if not boundary(number):
            raise ValueError(f"{name} {boundary_message}")
        return number

    return validate


_positive_int = _number_validator("an integer", int, lambda value: value > 0, "must be greater than zero")
_non_negative_int = _number_validator("an integer", int, lambda value: value >= 0, "must not be negative")
_positive_float = _number_validator("a number", float, lambda value: value > 0, "must be greater than zero")
_non_negative_float = _number_validator("a number", float, lambda value: value >= 0, "must not be negative")


class _ConfigMapping:
    """Shared ``from_value`` for the frozen environment configuration records.

    Every nested configuration accepts an instance, a mapping of field values,
    and (for the optional ones) ``None`` meaning "use the defaults".
    """

    #: Label used in the type error, e.g. ``"viewport"``.
    _config_label = "config"
    #: ``str.format`` template for the type error.
    _type_error = "{label} must be a {name} or mapping"
    #: When true, ``None`` builds a default record instead of raising.
    _none_default = False

    @classmethod
    def from_value(cls, value):
        if value is None and cls._none_default:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(cls._type_error.format(label=cls._config_label, name=cls.__name__))
        return cls(**dict(value))


@dataclass(frozen=True)
class MimeTypeConfig(_ConfigMapping):
    """A browser MIME type advertised by a built-in plugin."""

    _config_label = "plugin mime_types entries"
    _type_error = "{label} must be {name} instances or mappings"

    type: str
    suffixes: str = ""
    description: str = ""

    def __post_init__(self):
        for name in ("type", "suffixes", "description"):
            value = str(getattr(self, name))
            if name == "type" and not value:
                raise ValueError("mime type must not be empty")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class PluginConfig(_ConfigMapping):
    """A browser plugin and the MIME types it exposes."""

    _config_label = "plugins entries"
    _type_error = "{label} must be {name} instances or mappings"

    name: str
    filename: str = ""
    description: str = ""
    mime_types: tuple[MimeTypeConfig, ...] = ()

    def __post_init__(self):
        for name in ("name", "filename", "description"):
            value = str(getattr(self, name))
            if name == "name" and not value:
                raise ValueError("plugin name must not be empty")
            object.__setattr__(self, name, value)
        values = self.mime_types
        if isinstance(values, Mapping):
            # Runtime callers may pass a lone mapping for a single entry even
            # though the dataclass annotation says sequence.
            values = (values,)  # type: ignore[assignment]
        try:
            values = tuple(MimeTypeConfig.from_value(value) for value in values)
        except TypeError:
            raise TypeError("plugin mime_types must be a sequence") from None
        if len({value.type for value in values}) != len(values):
            raise ValueError("plugin mime_types must not contain duplicate types")
        object.__setattr__(self, "mime_types", values)


_PDF_MIME_TYPES = (
    MimeTypeConfig("application/pdf", "pdf", "Portable Document Format"),
    MimeTypeConfig("text/pdf", "pdf", "Portable Document Format"),
)
DEFAULT_PLUGINS = (
    PluginConfig("PDF Viewer", "internal-pdf-viewer", "Portable Document Format", _PDF_MIME_TYPES),
    PluginConfig("Chrome PDF Viewer", "internal-pdf-viewer", "Portable Document Format", _PDF_MIME_TYPES),
    PluginConfig("Chromium PDF Viewer", "internal-pdf-viewer", "Portable Document Format", _PDF_MIME_TYPES),
    PluginConfig("Microsoft Edge PDF Viewer", "internal-pdf-viewer", "Portable Document Format", _PDF_MIME_TYPES),
    PluginConfig("WebKit built-in PDF", "internal-pdf-viewer", "Portable Document Format", _PDF_MIME_TYPES),
)

@dataclass(frozen=True)
class ViewportConfig(_ConfigMapping):
    _config_label = "viewport"
    _none_default = True

    width: int = 1280
    height: int = 720
    device_scale_factor: float = 1.0
    mobile: bool = False
    touch: bool = False

    def __post_init__(self):
        object.__setattr__(self, "width", _positive_int(self.width, "viewport.width"))
        object.__setattr__(self, "height", _positive_int(self.height, "viewport.height"))
        object.__setattr__(
            self, "device_scale_factor",
            _positive_float(self.device_scale_factor, "viewport.device_scale_factor"),
        )
        object.__setattr__(self, "mobile", bool(self.mobile))
        object.__setattr__(self, "touch", bool(self.touch))


@dataclass(frozen=True)
class ScreenConfig(_ConfigMapping):
    _config_label = "screen"
    _none_default = True

    width: int = 1920
    height: int = 1080
    avail_width: int = 1920
    avail_height: int = 1080
    color_depth: int = 24
    pixel_depth: int = 24

    def __post_init__(self):
        for name in ("width", "height", "avail_width", "avail_height"):
            object.__setattr__(self, name, _positive_int(getattr(self, name), f"screen.{name}"))
        for name in ("color_depth", "pixel_depth"):
            value = _positive_int(getattr(self, name), f"screen.{name}")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class GeolocationConfig(_ConfigMapping):
    _config_label = "geolocation"
    _none_default = True

    permission: str = "denied"
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float | None = None

    def __post_init__(self):
        permission = str(self.permission).lower()
        if permission not in ("denied", "granted"):
            raise ValueError("geolocation.permission must be 'denied' or 'granted'")
        object.__setattr__(self, "permission", permission)
        for name, low, high in (("latitude", -90.0, 90.0), ("longitude", -180.0, 180.0)):
            value = getattr(self, name)
            if value is not None:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise TypeError(f"geolocation.{name} must be a number or None") from None
                if not low <= value <= high:
                    raise ValueError(f"geolocation.{name} is outside its valid range")
                object.__setattr__(self, name, value)
        if self.accuracy is not None:
            accuracy = _non_negative_float(self.accuracy, "geolocation.accuracy")
            object.__setattr__(self, "accuracy", accuracy)


@dataclass(frozen=True)
class NetworkInformationConfig(_ConfigMapping):
    _config_label = "connection"
    _none_default = True

    effective_type: str = "4g"
    rtt: int = 100
    downlink: float = 10.0
    save_data: bool = False
    type: str = "unknown"
    downlink_max: float = float("inf")

    def __post_init__(self):
        effective_type = str(self.effective_type)
        if effective_type not in ("slow-2g", "2g", "3g", "4g"):
            raise ValueError("connection.effective_type must be 'slow-2g', '2g', '3g', or '4g'")
        connection_type = str(self.type)
        if connection_type not in (
            "bluetooth", "cellular", "ethernet", "mixed", "none",
            "other", "unknown", "wifi", "wimax",
        ):
            raise ValueError("connection.type is not a valid connection type")
        object.__setattr__(self, "effective_type", effective_type)
        object.__setattr__(self, "rtt", _non_negative_int(self.rtt, "connection.rtt"))
        object.__setattr__(self, "downlink", _non_negative_float(self.downlink, "connection.downlink"))
        object.__setattr__(self, "save_data", bool(self.save_data))
        object.__setattr__(self, "type", connection_type)
        downlink_max = float(self.downlink_max)
        if downlink_max < 0:
            raise ValueError("connection.downlink_max must not be negative")
        object.__setattr__(self, "downlink_max", downlink_max)


@dataclass(frozen=True)
class BatteryConfig(_ConfigMapping):
    _config_label = "battery"
    _none_default = True

    charging: bool = True
    charging_time: float = 0.0
    discharging_time: float = float("inf")
    level: float = 1.0

    def __post_init__(self):
        object.__setattr__(self, "charging", bool(self.charging))
        object.__setattr__(self, "charging_time", _non_negative_float(self.charging_time, "battery.charging_time"))
        object.__setattr__(
            self, "discharging_time",
            _non_negative_float(self.discharging_time, "battery.discharging_time"),
        )
        level = float(self.level)
        if not 0.0 <= level <= 1.0:
            raise ValueError("battery.level must be between zero and one")
        object.__setattr__(self, "level", level)


@dataclass(frozen=True)
class StorageQuotaConfig(_ConfigMapping):
    _config_label = "storage quota"
    _none_default = True

    usage: int = 0
    quota: int = 0

    def __post_init__(self):
        usage = _non_negative_int(self.usage, "storage quota usage")
        quota = _non_negative_int(self.quota, "storage quota quota")
        if usage > quota:
            raise ValueError("storage quota usage must not exceed quota")
        object.__setattr__(self, "usage", usage)
        object.__setattr__(self, "quota", quota)


@dataclass(frozen=True)
class BrowserEnvironment:
    user_agent: str = DEFAULT_USER_AGENT
    app_version: str | None = None
    platform: str = "Win32"
    language: str = DEFAULT_LANGUAGES[0]
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    on_line: bool = True
    hardware_concurrency: int = 8
    device_memory: float = 8.0
    max_touch_points: int = 0
    webdriver: bool = False
    viewport: ViewportConfig = field(default_factory=ViewportConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    timezone: str = "UTC"
    geolocation: GeolocationConfig = field(default_factory=GeolocationConfig)
    connection: NetworkInformationConfig = field(default_factory=NetworkInformationConfig)
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    temporary_storage_quota: StorageQuotaConfig = field(default_factory=StorageQuotaConfig)
    persistent_storage_quota: StorageQuotaConfig = field(default_factory=StorageQuotaConfig)
    plugins: tuple[PluginConfig, ...] = DEFAULT_PLUGINS

    def __post_init__(self):
        object.__setattr__(self, "user_agent", str(self.user_agent))
        app_version = self.app_version
        if app_version is None:
            app_version = self.user_agent.removeprefix("Mozilla/")
        object.__setattr__(self, "app_version", str(app_version))
        object.__setattr__(self, "platform", str(self.platform))
        if not self.user_agent:
            raise ValueError("user_agent must not be empty")
        if not self.platform:
            raise ValueError("platform must not be empty")
        languages = self.languages
        if isinstance(languages, str):
            languages = (languages,)
        try:
            languages = tuple(str(value) for value in languages)
        except TypeError:
            raise TypeError("languages must be a sequence of strings") from None
        if not languages or any(not value for value in languages):
            raise ValueError("languages must contain at least one non-empty value")
        object.__setattr__(self, "languages", languages)
        language = self.language if self.language is not None else languages[0]
        language = str(language)
        if not language:
            raise ValueError("language must not be empty")
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "on_line", bool(self.on_line))
        object.__setattr__(
            self, "hardware_concurrency",
            _positive_int(self.hardware_concurrency, "hardware_concurrency"),
        )
        object.__setattr__(self, "device_memory", _positive_float(self.device_memory, "device_memory"))
        object.__setattr__(self, "max_touch_points", _non_negative_int(self.max_touch_points, "max_touch_points"))
        object.__setattr__(self, "webdriver", bool(self.webdriver))
        object.__setattr__(self, "viewport", ViewportConfig.from_value(self.viewport))
        object.__setattr__(self, "screen", ScreenConfig.from_value(self.screen))
        timezone = str(self.timezone)
        if not timezone:
            raise ValueError("timezone must not be empty")
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "geolocation", GeolocationConfig.from_value(self.geolocation))
        object.__setattr__(self, "connection", NetworkInformationConfig.from_value(self.connection))
        object.__setattr__(self, "battery", BatteryConfig.from_value(self.battery))
        object.__setattr__(
            self, "temporary_storage_quota",
            StorageQuotaConfig.from_value(self.temporary_storage_quota),
        )
        object.__setattr__(
            self, "persistent_storage_quota",
            StorageQuotaConfig.from_value(self.persistent_storage_quota),
        )
        plugins = self.plugins
        if isinstance(plugins, Mapping):
            # Runtime callers may pass a lone mapping for a single entry even
            # though the dataclass annotation says sequence.
            plugins = (plugins,)  # type: ignore[assignment]
        try:
            plugins = tuple(PluginConfig.from_value(plugin) for plugin in plugins)
        except TypeError:
            raise TypeError("plugins must be a sequence") from None
        if len({plugin.name for plugin in plugins}) != len(plugins):
            raise ValueError("plugins must not contain duplicate names")
        object.__setattr__(self, "plugins", plugins)

    @classmethod
    def from_value(cls, value=None, *, base=None):
        if value is None:
            return base if base is not None else cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("environment must be a BrowserEnvironment or mapping")
        values = asdict(base or cls())
        supplied = dict(value)
        # ``language`` follows the first entry when callers override only the
        # language list, matching browser Navigator semantics.
        if "languages" in supplied and "language" not in supplied:
            values["language"] = None
        if "user_agent" in supplied and "app_version" not in supplied:
            values["app_version"] = None
        for name in (
            "viewport", "screen", "geolocation", "connection", "battery",
            "temporary_storage_quota", "persistent_storage_quota",
        ):
            nested = supplied.get(name)
            if isinstance(nested, Mapping):
                merged = dict(values[name])
                merged.update(nested)
                supplied[name] = merged
        values.update(supplied)
        return cls(**values)

    def overlay(self, value=None):
        return self.from_value(value, base=self)

    def to_dict(self):
        return asdict(self)

    @property
    def readonly(self):
        """Return a read-only mapping useful for diagnostics and integrations."""
        return MappingProxyType(self.to_dict())


__all__ = [
    "BrowserEnvironment", "ViewportConfig", "ScreenConfig", "GeolocationConfig",
    "NetworkInformationConfig", "BatteryConfig", "StorageQuotaConfig",
    "PluginConfig", "MimeTypeConfig",
]
