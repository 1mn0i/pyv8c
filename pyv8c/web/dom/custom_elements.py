"""``customElements``: the definitions a page registers.  Defining one never
upgrades an element -- this DOM has no reaction stack."""

import pyv8
from pyv8 import IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from ...foundation.errors import dom_exception


@interface('CustomElementRegistry', None)
class CustomElementRegistry:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="define", callback="fn_define", length=2),
            MethodSpec(name="get", callback="fn_get", length=1, side_effect=1),
            MethodSpec(name="getName", callback="fn_getName", length=1, side_effect=1),
            MethodSpec(name="whenDefined", callback="fn_whenDefined", length=1),
            MethodSpec(name="upgrade", callback="fn_upgrade", length=1),
            MethodSpec(name="initialize", callback="fn_initialize", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="CustomElementRegistry",
        has_constructor=1,
    )

    def __init__(self, services=None, **_):
        self._services = services
        self._definitions = {}
        self._waiting = {}

    def fn_define(self, name=MISSING, constructor=MISSING, options=None, *_extra):
        if name is MISSING:
            raise missing_error('CustomElementRegistry', 'define', 2, 0, constructor=False)
        if constructor is MISSING:
            raise missing_error('CustomElementRegistry', 'define', 2, 1, constructor=False)
        name = dom_string(name)
        if "-" not in name:
            raise dom_exception(
                "SyntaxError",
                f"Failed to execute 'define' on 'CustomElementRegistry': \"{name}\""
                " is not a valid custom element name")
        if name in self._definitions:
            raise dom_exception(
                "NotSupportedError",
                f"Failed to execute 'define' on 'CustomElementRegistry': the name"
                f" \"{name}\" has already been used with this registry")
        self._definitions[name] = constructor
        for resolve in self._waiting.pop(name, ()):
            resolve(constructor)

    def fn_get(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('CustomElementRegistry', 'get', 1, 0, constructor=False)
        return self._definitions.get(dom_string(name), pyv8.Undefined)

    def fn_getName(self, constructor=MISSING, *_extra):
        if constructor is MISSING:
            raise missing_error('CustomElementRegistry', 'getName', 1, 0, constructor=False)
        return next((name for name, held in self._definitions.items()
                     if held == constructor), pyv8.Null)

    def fn_whenDefined(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('CustomElementRegistry', 'whenDefined', 1, 0, constructor=False)
        name = dom_string(name)
        promise, resolve, _reject = self._services.realm_services.make_promise()
        if name in self._definitions:
            resolve(self._definitions[name])
        else:
            self._waiting.setdefault(name, []).append(resolve)
        return promise

    def fn_upgrade(self, *_extra):
        """Definitions do not upgrade elements here."""
        return None

    def fn_initialize(self, *_extra):
        return None


__all__ = ["CustomElementRegistry"]
