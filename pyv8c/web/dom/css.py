"""CSSOM support for inline ``HTMLElement.style`` declarations."""
import re

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec
from pyv8.flag import V8Attribute

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from .events import EventTarget

_NAME_RE = re.compile(r"^(?:--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*)$")
_OBJECT_NAMES = frozenset(("constructor", "__defineGetter__", "__defineSetter__", "hasOwnProperty", "__lookupGetter__", "__lookupSetter__", "isPrototypeOf", "propertyIsEnumerable", "toString", "valueOf", "__proto__", "toLocaleString", "cssText", "length", "parentRule", "cssFloat", "getPropertyPriority", "getPropertyValue", "item", "removeProperty", "setProperty"))

def _property_name(value):
    value = str(dom_string(value)).strip()
    if value.startswith("--"):
        return value if _NAME_RE.match(value) else ""
    value = value.lower()
    return value if _NAME_RE.match(value) else ""

def _camel_name(name):
    if name.startswith("--"): return name
    result = name
    while "-" in result:
        left, right = result.split("-", 1)
        if not right: return ""
        result = left + right[0].upper() + right[1:]
    return result

def _dash_name(name):
    if name.startswith("--"): return name
    return re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), name)

def _normalize_value(value):
    value = str(dom_string(value)).strip()
    if not value or any(ch in value for ch in "{};\x00"): return ""
    return re.sub(r"[ \t\r\n]+", " ", value)

def _split_priority(value):
    match = re.search(r"\s*!\s*important\s*$", value, re.I)
    return (value[:match.start()].rstrip(), "important") if match else (value, "")


@interface("CSSStyleDeclaration", None, exposed_in=("Window",))
class CSSStyleDeclaration:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="cssText", getter="get_cssText", setter="set_cssText"), AttributeSpec(name="length", getter="get_length", side_effect=1), AttributeSpec(name="parentRule", getter="get_parentRule", side_effect=1), AttributeSpec(name="cssFloat", getter="get_cssFloat", setter="set_cssFloat")),
        methods=(MethodSpec(name="getPropertyPriority", callback="fn_getPropertyPriority", length=1, side_effect=1), MethodSpec(name="getPropertyValue", callback="fn_getPropertyValue", length=1, side_effect=1), MethodSpec(name="item", callback="fn_item", length=1, side_effect=1), MethodSpec(name="removeProperty", callback="fn_removeProperty", length=1), MethodSpec(name="setProperty", callback="fn_setProperty", length=2)),
        constructor_behavior=0, has_constructor=1, to_string_tag="CSSStyleDeclaration", array=True,
        name_get="__v8_name_get__", name_set="__v8_name_set__", name_query="__v8_name_query__", name_del="__v8_name_del__", name_enum="__v8_name_enum__",
        index_get="__v8_index_get__", index_set="__v8_index_set__", index_query="__v8_index_query__", index_del="__v8_index_del__", index_enum="__v8_index_enum__",
    )
    def __init__(self, element=None, **_): self._element = element
    @staticmethod
    def _parse_text(raw):
        result = []
        for chunk in str(raw).split(";"):
            if ":" not in chunk: continue
            name, value = chunk.split(":", 1); name = _property_name(name)
            if not name: continue
            value, priority = _split_priority(_normalize_value(value)); value = _normalize_value(value)
            if value: result.append((name, value, priority))
        return result
    def _declarations(self):
        if hasattr(self, "_computed_values"):
            return [(name, value, "") for name, value in self._computed_values.items() if value not in ("", None)]
        raw = str(self._element.attributes.get("style", "")) if self._element is not None else ""
        result, positions = [], {}
        for chunk in raw.split(";"):
            if ":" not in chunk: continue
            name, value = chunk.split(":", 1); name = _property_name(name)
            if not name: continue
            value, priority = _split_priority(_normalize_value(value)); value = _normalize_value(value)
            if not value: continue
            item = (name, value, priority)
            if name in positions: result[positions[name]] = item
            else: positions[name] = len(result); result.append(item)
        return result
    def _write(self, declarations):
        if self._element is None: return
        text = "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in declarations).strip()
        if text: self._element.attributes["style"] = text
        else: self._element.attributes.pop("style", None)
    def _set(self, name, value, priority=""):
        name = _property_name(name)
        if not name: return
        priority = str(dom_string(priority)).strip().lower()
        if priority and priority != "important": return
        value = _normalize_value(value); priority = "important" if priority == "important" else ""
        declarations = [item for item in self._declarations() if item[0] != name]
        if value: declarations.append((name, value, priority))
        self._write(declarations)
    def _value(self, name):
        name = _property_name(name)
        return next((v for n, v, _ in self._declarations() if n == name), "")
    def get_cssText(self): return "".join(f"{n}: {v}{' !important' if p else ''}; " for n, v, p in self._declarations()).strip()
    def set_cssText(self, value):
        raw = str(dom_string(value)); declarations, positions = [], {}
        for chunk in raw.split(";"):
            if ":" not in chunk: continue
            name, value = chunk.split(":", 1); name = _property_name(name)
            if not name: continue
            value, priority = _split_priority(_normalize_value(value)); value = _normalize_value(value)
            if not value: continue
            item = (name, value, priority)
            if name in positions: declarations[positions[name]] = item
            else: positions[name] = len(declarations); declarations.append(item)
        self._write(declarations)
    def get_length(self): return len(self._declarations())
    def get_parentRule(self): return pyv8.Null
    def get_cssFloat(self): return self._value("float")
    def set_cssFloat(self, value): self._set("float", value)
    def fn_getPropertyPriority(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyPriority", 1, 0)
        name = _property_name(name); return next((p for n, _, p in self._declarations() if n == name), "")
    def fn_getPropertyValue(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "getPropertyValue", 1, 0)
        return self._value(name)
    def fn_item(self, index=MISSING, *_extra):
        if index is MISSING: raise missing_error("CSSStyleDeclaration", "item", 1, 0)
        index = integer(index); declarations = self._declarations()
        return declarations[index][0] if 0 <= index < len(declarations) else ""
    def fn_removeProperty(self, name=MISSING, *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "removeProperty", 1, 0)
        name = _property_name(name); declarations = self._declarations(); old = next((v for n, v, _ in declarations if n == name), "")
        self._write([item for item in declarations if item[0] != name]); return old
    def fn_setProperty(self, name=MISSING, value=MISSING, priority="", *_extra):
        if name is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 0)
        if value is MISSING: raise missing_error("CSSStyleDeclaration", "setProperty", 2, 1)
        self._set(name, value, priority)
    def _named(self, name):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name); return self._value(dashed) if _NAME_RE.match(dashed) else None
    def __v8_name_get__(self, name): return self._named(name)
    def __v8_name_set__(self, name, value):
        if not isinstance(name, str) or name in _OBJECT_NAMES: return None
        dashed = _dash_name(name)
        if not _NAME_RE.match(dashed): return None
        self._set(dashed, value); return True
    def __v8_name_query__(self, name): return 0 if self._named(name) is not None else None
    def __v8_name_del__(self, name):
        if isinstance(name, str) and name not in _OBJECT_NAMES: self._set(_dash_name(name), "")
        return True
    def __v8_name_enum__(self): return [_camel_name(name) for name, _, _ in self._declarations()]
    def __v8_index_get__(self, index): return self.fn_item(index)
    def __v8_index_set__(self, index, value): return None
    def __v8_index_query__(self, index): return V8Attribute.ReadOnly if self.fn_item(index) else None
    def __v8_index_del__(self, index): return None
    def __v8_index_enum__(self): return self.get_length()

__all__ = ["CSSStyleDeclaration"]
