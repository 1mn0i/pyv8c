"""V8 registration metadata for core Python error types."""

from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from .registry import interface, js_attributes
from ..foundation import errors


def register(registry):
    interface("DOMException", None, constructible=True, length=2, tag="DOMException")(errors.DOMException)
    errors.DOMException.__v8_attribute__ = js_attributes(
        ("name", "get_name", None, 0, 1),
        ("message", "get_message", None, 0, 1),
        ("code", "get_code", None, 0, 1),
    )
    ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)(errors.DOMException)
    interface("DOMError", None, constructible=True, length=2, tag="DOMError")(errors.DOMError)
    errors.DOMError.__v8_attribute__ = js_attributes(
        ("name", "get_name", None, 0, 1),
        ("message", "get_message", None, 0, 1),
    )
    ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)(errors.DOMError)
    registry.register_namespace(vars(errors))
