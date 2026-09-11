"""Native declarations for browser exception objects."""

from pyv8 import AttributeSpec, IdlSpec

from ..foundation import errors
from .features import interface


def implementations():
    """Return the explicit error interface classes for the core Feature."""
    classes = []
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
        classes.append(cls)
    return tuple(classes)
