"""Pure resource discovery from an owner-thread node snapshot."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .local import PRELOAD_KINDS


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    tag: str
    attributes: Mapping
    script_src: str
    async_script: bool
    sandbox_tokens: frozenset | None

    @classmethod
    def capture(cls, node, sandbox_tokens):
        attributes = MappingProxyType(dict(node.attributes))
        tag = getattr(node, "localName", "")
        src = node.get_src() if tag == "script" and hasattr(node, "get_src") else attributes.get("src", "")
        asynchronous = bool(node.get_async()) if tag == "script" and hasattr(node, "get_async") else "async" in attributes
        return cls(tag, attributes, src, asynchronous, sandbox_tokens)


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    url: str
    options: Mapping
    kind: str
    async_script: bool = False
    media_start: bool = False


def discover(snapshot):
    tag, attrs = snapshot.tag, snapshot.attributes
    if tag == "script":
        if snapshot.sandbox_tokens is not None and "allow-scripts" not in snapshot.sandbox_tokens:
            return None
        if not snapshot.script_src:
            return None
        options = {"method": "GET", "resource_type": "script"}
        if str(attrs.get("type", "")).lower() == "module" or attrs.get("crossorigin") is not None:
            options["mode"] = "cors"
        return ResourceSpec(snapshot.script_src, MappingProxyType(options), "script", snapshot.async_script)
    preload = None
    if tag == "link":
        rel = str(attrs.get("rel", "")).lower().split()
        if "stylesheet" not in rel:
            if "preload" not in rel and "modulepreload" not in rel:
                return None
            preload = PRELOAD_KINDS.get(str(attrs.get("as", "fetch")).lower())
            if preload is None:
                return None
        src = attrs.get("href", "")
    elif tag in ("img", "audio", "video", "source"):
        src = attrs.get("src", "")
    elif tag == "iframe":
        if attrs.get("srcdoc") is not None:
            return None
        src = attrs.get("src", "") or "about:blank"
    else:
        return None
    if not src:
        return None
    resource_type = {"link": "style", "img": "image", "audio": "media", "video": "media", "source": "media"}.get(tag, tag)
    destination = {"link": "style", "img": "image", "audio": "audio", "video": "video", "source": "video"}.get(tag, tag)
    kind = tag
    if preload is not None:
        resource_type, destination, kind = preload
    options = {"method": "GET", "resource_type": resource_type, "destination": destination}
    if tag == "iframe":
        options["mode"] = "navigate"
    elif attrs.get("crossorigin") is not None:
        options["mode"] = "cors"
    return ResourceSpec(src, MappingProxyType(options), kind, media_start=tag in ("audio", "video"))
