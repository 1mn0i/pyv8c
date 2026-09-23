"""Immutable browser environment: one flat table of dotted keys.

The defaults are a profile collected from a real Chrome
(``data/chrome-environment.json``), so an unconfigured browser presents what that
browser reported; a caller overrides any key by name, and the keys that follow
``user_agent`` move with it.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

SCHEMA_VERSION = 1
ENVELOPE_KEYS = ("schema", "source")
PROFILE = Path(__file__).parents[1] / "data" / "chrome-environment.json"

#: The version Chrome's frozen "Not_A Brand" entry carries.
_NOT_A_BRAND_VERSION = "8"


def _text(value):
    if value is None:
        raise TypeError("must be a string")
    return str(value)


def _integer(value):
    if isinstance(value, bool):
        raise TypeError("must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise TypeError("must be an integer") from None


def _number(value):
    if isinstance(value, bool):
        raise TypeError("must be a number")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise TypeError("must be a number") from None


def _optional_number(value):
    """A simulated coordinate: absent until a caller gives one."""
    return None if value is None else _number(value)


def _tokens(value):
    if isinstance(value, str):
        value = (value,)
    return tuple(str(item) for item in value)


def _enum(*allowed):
    def validate(value):
        text = str(value).lower()
        if text not in allowed:
            raise ValueError(f"must be one of {', '.join(allowed)}")
        return text
    return validate


def _brands(value):
    brands = tuple(_brand(entry) for entry in value)
    return brands


def _brand(entry):
    if isinstance(entry, Mapping):
        brand, version = entry.get("brand"), entry.get("version")
    else:
        brand, version = entry
    if brand is None or version is None:
        raise TypeError("entries need both a brand and a version")
    return {"brand": str(brand), "version": str(version)}


def _plugins(value):
    if isinstance(value, Mapping):
        value = (value,)
    plugins = tuple(_plugin(entry) for entry in value)
    if len({plugin["name"] for plugin in plugins}) != len(plugins):
        raise ValueError("must not repeat a plugin name")
    return plugins


def _plugin(entry):
    if not isinstance(entry, Mapping):
        raise TypeError("entries must be mappings")
    mime_types = tuple(_mime_type(mime) for mime in entry.get("mime_types", ()))
    if len({mime["type"] for mime in mime_types}) != len(mime_types):
        raise ValueError("must not repeat a mime type")
    return {
        "name": _text(entry.get("name")),
        "filename": str(entry.get("filename", "")),
        "description": str(entry.get("description", "")),
        "mime_types": mime_types,
    }


def _mime_type(entry):
    return {
        "type": _text(entry.get("type")),
        "suffixes": str(entry.get("suffixes", "")),
        "description": str(entry.get("description", "")),
    }


@dataclass(frozen=True)
class Field:
    """One setting: its dotted key, how a given value is checked, whether a browser reports it."""

    name: str
    validate: Callable[[object], object]
    collected: bool = False
    default: object = None


#: The keys a browser reports for itself, plus the simulation-only ones it cannot.
FIELDS: tuple[Field, ...] = (
    Field("user_agent", _text, True),
    Field("app_version", _text, True),
    Field("vendor", _text, True),
    Field("platform", _text, True),
    Field("language", _text, True),
    Field("languages", _tokens, True),
    Field("on_line", bool, True),
    Field("hardware_concurrency", _integer, True),
    Field("device_memory", _number, True),
    Field("max_touch_points", _integer, True),
    Field("webdriver", bool, True),
    Field("timezone", _text, True),
    Field("viewport.width", _integer, True),
    Field("viewport.height", _integer, True),
    Field("viewport.device_scale_factor", _number, True),
    Field("viewport.mobile", bool, default=False),
    Field("viewport.touch", bool, default=False),
    Field("screen.width", _integer, True),
    Field("screen.height", _integer, True),
    Field("screen.avail_width", _integer, True),
    Field("screen.avail_height", _integer, True),
    Field("screen.color_depth", _integer, True),
    Field("screen.pixel_depth", _integer, True),
    Field("geolocation.permission", _enum("denied", "granted"), default="denied"),
    Field("geolocation.latitude", _optional_number),
    Field("geolocation.longitude", _optional_number),
    Field("geolocation.accuracy", _optional_number),
    Field("connection.effective_type", _enum("slow-2g", "2g", "3g", "4g"), True),
    Field("connection.rtt", _integer, True),
    Field("connection.downlink", _number, True),
    Field("connection.save_data", bool, True),
    Field("connection.type", _enum("bluetooth", "cellular", "ethernet", "mixed", "none",
                                   "other", "unknown", "wifi", "wimax"), default="unknown"),
    Field("connection.downlink_max", _number, default=float("inf")),
    Field("battery.charging", bool, True),
    Field("battery.charging_time", _number, True),
    Field("battery.discharging_time", _number, True),
    Field("battery.level", _number, True),
    Field("storage.temporary.usage", _integer, True),
    Field("storage.temporary.quota", _integer, True),
    Field("storage.persistent.usage", _integer, default=0),
    Field("storage.persistent.quota", _integer, default=0),
    Field("client_hints.brands", _brands, True),
    Field("client_hints.full_version_list", _brands, True),
    Field("client_hints.mobile", bool, True),
    Field("client_hints.platform", _text, True),
    Field("client_hints.platform_version", _text, True),
    Field("client_hints.architecture", _text, True),
    Field("client_hints.bitness", _text, True),
    Field("client_hints.wow64", bool, True),
    Field("client_hints.model", _text, True),
    Field("client_hints.form_factors", _tokens, True),
    Field("plugins", _plugins, True),
)

_BY_NAME = {field.name: field for field in FIELDS}

#: The keys a collected profile carries, exactly.
COLLECTED_KEYS: tuple[str, ...] = tuple(field.name for field in FIELDS if field.collected)

_PROFILE = {name: value for name, value in
            json.loads(PROFILE.read_text(encoding="utf-8")).items()
            if name not in ENVELOPE_KEYS}

DEFAULTS = MappingProxyType({
    field.name: field.validate(_PROFILE[field.name]) if field.name in _PROFILE else field.default
    for field in FIELDS
})


@dataclass(frozen=True)
class ViewportView:
    width: int
    height: int
    device_scale_factor: float
    mobile: bool
    touch: bool


@dataclass(frozen=True)
class ScreenView:
    width: int
    height: int
    avail_width: int
    avail_height: int
    color_depth: int
    pixel_depth: int


@dataclass(frozen=True)
class GeolocationView:
    permission: str
    latitude: float | None
    longitude: float | None
    accuracy: float | None


@dataclass(frozen=True)
class ConnectionView:
    effective_type: str
    rtt: int
    downlink: float
    save_data: bool
    type: str
    downlink_max: float


@dataclass(frozen=True)
class BatteryView:
    charging: bool
    charging_time: float
    discharging_time: float
    level: float


@dataclass(frozen=True)
class QuotaView:
    usage: int
    quota: int


@dataclass(frozen=True)
class StorageView:
    temporary: QuotaView
    persistent: QuotaView


@dataclass(frozen=True)
class Brand:
    brand: str
    version: str


@dataclass(frozen=True)
class ClientHintsView:
    brands: tuple[Brand, ...]
    full_version_list: tuple[Brand, ...]
    mobile: bool
    platform: str
    platform_version: str
    architecture: str
    bitness: str
    wow64: bool
    model: str
    form_factors: tuple[str, ...]


@dataclass(frozen=True)
class MimeTypeView:
    type: str
    suffixes: str
    description: str


@dataclass(frozen=True)
class PluginView:
    name: str
    filename: str
    description: str
    mime_types: tuple[MimeTypeView, ...]


def _platform_name(user_agent, fallback):
    for token, name in (
        ("Windows", "Windows"), ("Android", "Android"), ("iPhone", "iOS"),
        ("iPad", "iOS"), ("CrOS", "Chrome OS"), ("Macintosh", "macOS"),
        ("Linux", "Linux"),
    ):
        if token in user_agent:
            return name
    return fallback


def _derive(values):
    """The keys that follow the user agent, so a changed one moves them too."""
    user_agent = str(values["user_agent"])
    version = re.search(r"Chrome/(\d+)", user_agent)
    major = version.group(1) if version is not None else ""
    brands = tuple(
        {"brand": brand, "version": _NOT_A_BRAND_VERSION if brand == "Not_A Brand" else major}
        for brand in ("Google Chrome", "Not_A Brand", "Chromium")
    )
    return {
        "app_version": user_agent.removeprefix("Mozilla/"),
        "language": str(tuple(values["languages"])[0]),
        "client_hints.brands": brands,
        "client_hints.mobile": "Mobile" in user_agent,
        "client_hints.platform": _platform_name(user_agent, str(values["client_hints.platform"])),
    }


def _resolved(explicit):
    values = dict(DEFAULTS)
    values.update(explicit)
    values.update(_derive(values))
    values.update(explicit)
    return values


def _json_value(value):
    """A non-finite number travels as its own name: ``Infinity`` is not JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        if value != value:
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _accepted(data):
    """Validate a mapping of dotted keys into the values it sets."""
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise TypeError("environment must be a mapping of dotted keys")
    accepted = {}
    for name, value in data.items():
        if name in ENVELOPE_KEYS:
            if name == "schema" and value != SCHEMA_VERSION:
                raise ValueError(f"environment schema {value!r} is not version {SCHEMA_VERSION}")
            continue
        field = _BY_NAME.get(name)
        if field is None:
            raise TypeError(f"unknown environment key: {name}")
        try:
            accepted[name] = field.validate(value)
        except (TypeError, ValueError) as error:
            raise type(error)(f"{name} {error}") from None
    return accepted


class BrowserEnvironment:
    """One flat ``dotted.key -> value`` configuration, frozen once built.

    ``to_dict()`` gives back only the keys this environment was given; the rest follow
    the shipped profile or the user agent, which ``resolved()`` and the views read.
    """

    __slots__ = (
        "_explicit", "_resolved",
        "viewport", "screen", "geolocation", "connection", "battery", "storage",
        "client_hints", "plugins",
    )

    def __init__(self, data=None, *, base=None):
        explicit = dict(base._explicit) if base is not None else {}
        if isinstance(data, BrowserEnvironment):
            explicit.update(data._explicit)
        else:
            explicit.update(_accepted(data))
        values = _resolved(explicit)
        for kind in ("temporary", "persistent"):
            if values[f"storage.{kind}.usage"] > values[f"storage.{kind}.quota"]:
                raise ValueError(f"storage.{kind}.usage must not exceed storage.{kind}.quota")
        object.__setattr__(self, "_explicit", MappingProxyType(explicit))
        object.__setattr__(self, "_resolved", MappingProxyType(values))
        object.__setattr__(self, "viewport", ViewportView(
            values["viewport.width"], values["viewport.height"],
            values["viewport.device_scale_factor"], values["viewport.mobile"],
            values["viewport.touch"],
        ))
        object.__setattr__(self, "screen", ScreenView(
            values["screen.width"], values["screen.height"],
            values["screen.avail_width"], values["screen.avail_height"],
            values["screen.color_depth"], values["screen.pixel_depth"],
        ))
        object.__setattr__(self, "geolocation", GeolocationView(
            values["geolocation.permission"], values["geolocation.latitude"],
            values["geolocation.longitude"], values["geolocation.accuracy"],
        ))
        object.__setattr__(self, "connection", ConnectionView(
            values["connection.effective_type"], values["connection.rtt"],
            values["connection.downlink"], values["connection.save_data"],
            values["connection.type"], values["connection.downlink_max"],
        ))
        object.__setattr__(self, "battery", BatteryView(
            values["battery.charging"], values["battery.charging_time"],
            values["battery.discharging_time"], values["battery.level"],
        ))
        object.__setattr__(self, "storage", StorageView(
            QuotaView(values["storage.temporary.usage"], values["storage.temporary.quota"]),
            QuotaView(values["storage.persistent.usage"], values["storage.persistent.quota"]),
        ))
        object.__setattr__(self, "client_hints", ClientHintsView(
            tuple(Brand(**entry) for entry in values["client_hints.brands"]),
            tuple(Brand(**entry) for entry in values["client_hints.full_version_list"]),
            values["client_hints.mobile"], values["client_hints.platform"],
            values["client_hints.platform_version"], values["client_hints.architecture"],
            values["client_hints.bitness"], values["client_hints.wow64"],
            values["client_hints.model"], tuple(values["client_hints.form_factors"]),
        ))
        object.__setattr__(self, "plugins", tuple(
            PluginView(entry["name"], entry["filename"], entry["description"],
                       tuple(MimeTypeView(**mime) for mime in entry["mime_types"]))
            for entry in values["plugins"]
        ))

    def __setattr__(self, name, value):
        raise AttributeError("BrowserEnvironment is immutable")

    def __getattr__(self, name):
        """A resolved key is also readable as an attribute."""
        values = object.__getattribute__(self, "_resolved")
        if name in values:
            return values[name]
        raise AttributeError(f"unknown environment key: {name}")

    @classmethod
    def from_dict(cls, data=None, *, base=None):
        """Build from a mapping of dotted keys, an instance, or nothing."""
        if isinstance(data, cls) and base is None:
            return data
        return cls(data, base=base)

    @classmethod
    def loads(cls, text, *, base=None):
        return cls.from_dict(json.loads(text), base=base)

    @classmethod
    def from_file(cls, path, *, base=None):
        return cls.loads(Path(path).read_text(encoding="utf-8"), base=base)

    def overlay(self, data=None):
        """This configuration with *data* applied on top of it."""
        return type(self).from_dict(data, base=self)

    def to_dict(self):
        """Only the keys this environment was given, in table order."""
        return {name: self._explicit[name] for name in _BY_NAME if name in self._explicit}

    def resolved(self):
        """Every key, with the profile and the user agent filled in."""
        return dict(self._resolved)

    def dumps(self, *, resolved=False, source=None):
        document = {"schema": SCHEMA_VERSION}
        if source is not None:
            document["source"] = dict(source)
        values = self.resolved() if resolved else self.to_dict()
        document.update({name: _json_value(value) for name, value in values.items()})
        return json.dumps(document, indent=2) + "\n"

    def to_file(self, path, *, resolved=True, source=None):
        Path(path).write_text(self.dumps(resolved=resolved, source=source), encoding="utf-8")

    def __eq__(self, other):
        if not isinstance(other, BrowserEnvironment):
            return NotImplemented
        return self._resolved == other._resolved

    def __repr__(self):
        keys = ", ".join(f"{name}={value!r}" for name, value in self.to_dict().items())
        return f"BrowserEnvironment({keys})"


__all__ = [
    "BrowserEnvironment", "Field", "FIELDS", "SCHEMA_VERSION", "COLLECTED_KEYS", "PROFILE",
    "ViewportView", "ScreenView", "GeolocationView", "ConnectionView", "BatteryView",
    "StorageView", "QuotaView", "ClientHintsView", "Brand", "PluginView", "MimeTypeView",
]
