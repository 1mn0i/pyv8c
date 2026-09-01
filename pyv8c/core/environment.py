"""Immutable browser-environment configuration shared by all realms."""

from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Mapping


DEFAULT_USER_AGENT = "'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'"
DEFAULT_LANGUAGES = ("zh-CN", "en", "en-US")


@dataclass(frozen=True)
class MimeTypeConfig:
    """A browser MIME type advertised by a built-in plugin."""

    type: str
    suffixes: str = ""
    description: str = ""

    def __post_init__(self):
        for name in ("type", "suffixes", "description"):
            value = str(getattr(self, name))
            if name == "type" and not value:
                raise ValueError("mime type must not be empty")
            object.__setattr__(self, name, value)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("plugin mime_types entries must be MimeTypeConfig instances or mappings")
        return cls(**dict(value))


@dataclass(frozen=True)
class PluginConfig:
    """A browser plugin and the MIME types it exposes."""

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
            values = (values,)
        try:
            values = tuple(MimeTypeConfig.from_value(value) for value in values)
        except TypeError:
            raise TypeError("plugin mime_types must be a sequence") from None
        if len({value.type for value in values}) != len(values):
            raise ValueError("plugin mime_types must not contain duplicate types")
        object.__setattr__(self, "mime_types", values)

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("plugins entries must be PluginConfig instances or mappings")
        return cls(**dict(value))


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

def _positive_int(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be an integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _non_negative_int(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be an integer") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def _positive_float(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class ViewportConfig:
    width: int = 1280
    height: int = 720
    device_scale_factor: float = 1.0
    mobile: bool = False
    touch: bool = False

    def __post_init__(self):
        object.__setattr__(self, "width", _positive_int(self.width, "viewport.width"))
        object.__setattr__(self, "height", _positive_int(self.height, "viewport.height"))
        object.__setattr__(self, "device_scale_factor", _positive_float(self.device_scale_factor, "viewport.device_scale_factor"))
        object.__setattr__(self, "mobile", bool(self.mobile))
        object.__setattr__(self, "touch", bool(self.touch))

    @classmethod
    def from_value(cls, value):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("viewport must be a ViewportConfig or mapping")
        return cls(**dict(value))


@dataclass(frozen=True)
class ScreenConfig:
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

    @classmethod
    def from_value(cls, value):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("screen must be a ScreenConfig or mapping")
        return cls(**dict(value))


@dataclass(frozen=True)
class GeolocationConfig:
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

    @classmethod
    def from_value(cls, value):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError("geolocation must be a GeolocationConfig or mapping")
        return cls(**dict(value))


def _non_negative_float(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{name} must be a number") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class BrowserEnvironment:
    user_agent: str = DEFAULT_USER_AGENT
    platform: str = "Win32"
    language: str = DEFAULT_LANGUAGES[0]
    languages: tuple[str, ...] = DEFAULT_LANGUAGES
    on_line: bool = True
    hardware_concurrency: int = 8
    device_memory: float = 8.0
    max_touch_points: int = 0
    viewport: ViewportConfig = field(default_factory=ViewportConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    timezone: str = "UTC"
    geolocation: GeolocationConfig = field(default_factory=GeolocationConfig)
    plugins: tuple[PluginConfig, ...] = DEFAULT_PLUGINS

    def __post_init__(self):
        object.__setattr__(self, "user_agent", str(self.user_agent))
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
        object.__setattr__(self, "hardware_concurrency", _positive_int(self.hardware_concurrency, "hardware_concurrency"))
        object.__setattr__(self, "device_memory", _positive_float(self.device_memory, "device_memory"))
        object.__setattr__(self, "max_touch_points", _non_negative_int(self.max_touch_points, "max_touch_points"))
        object.__setattr__(self, "viewport", ViewportConfig.from_value(self.viewport))
        object.__setattr__(self, "screen", ScreenConfig.from_value(self.screen))
        timezone = str(self.timezone)
        if not timezone:
            raise ValueError("timezone must not be empty")
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "geolocation", GeolocationConfig.from_value(self.geolocation))
        plugins = self.plugins
        if isinstance(plugins, Mapping):
            plugins = (plugins,)
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
        for name in ("viewport", "screen", "geolocation"):
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
    "PluginConfig", "MimeTypeConfig",
]
