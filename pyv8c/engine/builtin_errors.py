"""Native declarations for browser exception objects."""

from pyv8 import IdlSpec, AttributeSpec

from .registry import interface
from ..foundation import errors


def register(registry):
    for name in ("DOMException", "DOMError"):
        cls = getattr(errors, name)
        if "__idl_spec__" not in cls.__dict__:
            interface(name, None)(cls)
            attributes = [AttributeSpec(name=field, getter="get_" + field, side_effect=1)
                          for field in (("name", "message", "code") if name == "DOMException" else ("name", "message"))]
            cls.__v8_idl_spec__ = IdlSpec(
                attributes=tuple(attributes), length=2, constructor_behavior=1,
                has_constructor=1, to_string_tag=name,
            )
        registry.register(cls)
