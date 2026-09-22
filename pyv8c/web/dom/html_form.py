"""``HTMLFormElement``: the controls it owns, and its legacy access surface.

``form[name]`` and ``form[index]`` are an independent legacy platform object in
Chromium, so the handlers and the ``RadioNodeList`` they hand out live here with
the element.
"""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import boolean, dom_string
from ...foundation.errors import LegacyPropertyError, V8TypeError
from .collections import DOMTokenList, HTMLFormControlsCollection, NodeList
from .html_elements import Element, HTMLElement, _document_url, _set_url_value, _url_value


@interface("RadioNodeList", "NodeList")
class RadioNodeList(NodeList):
    """The live collection returned for duplicate form names."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="value", getter="get_value", setter="set_value"),),
        to_string_tag="RadioNodeList",
        has_constructor=0,
        array=True,
    )

    def get_value(self):
        for item in self._items():
            if getattr(item, "localName", "") == "input" and item.get_type() in ("radio", "checkbox"):
                if item.get_checked():
                    return item.get_value()
        return ""

    def set_value(self, value):
        value = str(dom_string(value))
        for item in self._items():
            if getattr(item, "localName", "") == "input" and item.get_type() == "radio":
                item.set_checked(item.get_value() == value)


#: The tags a form collects: its listed elements.  An ``input`` in the
#: image-button state is submittable but is not one of them.
_LISTED_ELEMENT_TAGS = frozenset((
    "button", "fieldset", "input", "object", "output", "select", "textarea",
))


def _is_listed_element(node):
    """Whether ``node`` is a listed element, by tag and input type."""
    if not isinstance(node, Element) or node.localName not in _LISTED_ELEMENT_TAGS:
        return False
    if node.localName != "input":
        return True
    return str(node.attributes.get("type", "")).strip().lower() != "image"


@interface('HTMLFormElement', 'HTMLElement')
class HTMLFormElement(HTMLElement):
    """``<form>``: the controls it owns.

    ``elements`` holds the listed elements whose form owner this form is --
    descendants, and the controls elsewhere in the document that name it with
    ``form=`` -- which is why the list follows the document's order and not the
    form's.  A control's owner is the form its ``form`` attribute names, and the
    nearest ancestor form only when it has no such attribute, so a control naming a
    form that is not there belongs to no form at all.
    """

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="elements", getter="get_elements", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
            AttributeSpec(name="action", getter="get_action", setter="set_action"),
            AttributeSpec(name="acceptCharset", getter="get_acceptCharset", setter="set_acceptCharset"),
            AttributeSpec(name="autocomplete", getter="get_autocomplete", setter="set_autocomplete"),
            AttributeSpec(name="enctype", getter="get_enctype", setter="set_enctype"),
            AttributeSpec(name="encoding", getter="get_encoding", setter="set_encoding"),
            AttributeSpec(name="method", getter="get_method", setter="set_method"),
            AttributeSpec(name="name", getter="get_name", setter="set_name"),
            AttributeSpec(name="noValidate", getter="get_noValidate", setter="set_noValidate"),
            AttributeSpec(name="rel", getter="get_rel", setter="set_rel"),
            AttributeSpec(name="relList", getter="get_relList", side_effect=1),
            AttributeSpec(name="target", getter="get_target", setter="set_target"),
        ),
        methods=(
            MethodSpec(name="checkValidity", callback="fn_checkValidity"),
            MethodSpec(name="reportValidity", callback="fn_reportValidity"),
            MethodSpec(name="requestSubmit", callback="fn_requestSubmit", length=1),
            MethodSpec(name="reset", callback="fn_reset"),
            MethodSpec(name="submit", callback="fn_submit"),
        ),
        constructor_behavior=0,
        has_constructor=1,
        name_get="__v8_name_get__",
        name_set="__v8_name_set__",
        name_del="__v8_name_del__",
        name_enum="__v8_name_enum__",
        index_get="__v8_index_get__",
        index_set="__v8_index_set__",
        index_del="__v8_index_del__",
        index_enum="__v8_index_enum__",
    )
    def get_action(self):
        """The submission URL: the document's own URL, without its fragment, when
        the attribute is absent."""
        return _url_value(self, "action") or _document_url(self).split("#", 1)[0]
    def set_action(self, value): _set_url_value(self, "action", value)

    def get_acceptCharset(self): return self.attributes.get("accept-charset", "")
    def set_acceptCharset(self, value): self.attributes["accept-charset"] = str(dom_string(value))
    def get_autocomplete(self):
        value = str(self.attributes.get("autocomplete", "")).lower()
        return value if value in ("on", "off") else "on"
    def set_autocomplete(self, value):
        value = str(dom_string(value)).lower()
        self.attributes["autocomplete"] = value if value in ("on", "off") else "on"
    def get_enctype(self):
        value = str(self.attributes.get("enctype", "")).lower()
        allowed = ("application/x-www-form-urlencoded", "multipart/form-data", "text/plain")
        return value if value in allowed else allowed[0]
    def set_enctype(self, value):
        value = str(dom_string(value)).lower()
        self.attributes["enctype"] = value
    def get_encoding(self): return self.get_enctype()
    def set_encoding(self, value): self.set_enctype(value)
    def get_method(self):
        value = str(self.attributes.get("method", "")).lower()
        return value if value in ("get", "post", "dialog") else "get"
    def set_method(self, value): self.attributes["method"] = str(dom_string(value)).lower()
    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value): self.attributes["name"] = str(dom_string(value))
    def get_noValidate(self): return "novalidate" in self.attributes
    def set_noValidate(self, value):
        if boolean(value): self.attributes["novalidate"] = ""
        else: self.attributes.pop("novalidate", None)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(dom_string(value))
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel")
        return value
    def get_target(self): return self.attributes.get("target", "")
    def set_target(self, value): self.attributes["target"] = str(dom_string(value))
    def _owns(self, node):
        """Whether this form is ``node``'s form owner."""
        if "form" in node.attributes:
            form_id = str(node.attributes.get("form", "")).strip()
            lookup = getattr(self.root_node(), "fn_getElementById", None)
            owner = lookup(form_id) if lookup is not None and form_id else None
            return owner is self
        for ancestor in node._ancestors():
            if isinstance(ancestor, HTMLFormElement):
                return ancestor is self
        return False
    def _controls(self):
        return [node for node in self.root_node()._descendants()
                if _is_listed_element(node) and self._owns(node)]
    def get_elements(self):
        value = getattr(self, "_elements_collection", None)
        if value is None:
            value = self._elements_collection = HTMLFormControlsCollection(
                owner=self, getter=self._controls)
        return value
    def get_length(self):
        return len(self._controls())

    def _named_items(self, name):
        value = str(name)
        if not value:
            return []
        return [item for item in self._controls()
                if item.get_id() == value or str(item.attributes.get("name", "")) == value]

    def __v8_name_get__(self, name):
        items = self._named_items(name)
        if not items:
            return None
        if len(items) == 1:
            return items[0]
        view = getattr(self, "_radio_lists", None)
        if view is None:
            view = self._radio_lists = {}
        key = str(name)
        value = view.get(key)
        if value is None:
            value = view[key] = RadioNodeList(owner=self, getter=lambda: self._named_items(key))
        return value

    def __v8_name_set__(self, name, value):
        if self._named_items(name):
            raise LegacyPropertyError.named("set", str(name), "HTMLFormElement", "Named property setter is not supported.")
        return None

    def __v8_name_del__(self, name):
        if self._named_items(name):
            raise LegacyPropertyError.named("delete", str(name), "HTMLFormElement", "Named property deleter is not supported.")
        return None

    def __v8_name_enum__(self):
        return []

    def __v8_index_get__(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        items = self._controls()
        return items[index] if 0 <= index < len(items) else None

    def __v8_index_set__(self, index, value):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._controls()):
            raise LegacyPropertyError.indexed("set", index, "HTMLFormElement", "Indexed property setter is not supported.")
        return None

    def __v8_index_del__(self, index):
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        if 0 <= index < len(self._controls()):
            raise LegacyPropertyError.indexed("delete", index, "HTMLFormElement", "Indexed property deleter is not supported.")
        return None

    def __v8_index_enum__(self):
        return len(self._controls())

    def _validation_controls(self):
        return tuple(control for control in self._controls()
                     if bool(getattr(control, "get_willValidate", lambda: False)()))

    def _check_validity(self, report=False):
        valid = True
        for control in self._validation_controls():
            check = getattr(control, "get_validity", None)
            if check is None or check().get_valid():
                continue
            valid = False
            if report:
                dispatch = getattr(control, "fn_dispatchEvent", None)
                if dispatch is not None:
                    from .events import Event
                    event = Event("invalid", {"bubbles": False, "cancelable": True})
                    event._trusted = True
                    dispatch(event)
        return valid

    def fn_checkValidity(self, *_extra): return self._check_validity(report=True)
    def fn_reportValidity(self, *_extra): return self._check_validity(report=True)

    def fn_requestSubmit(self, submitter=pyv8.Undefined, *_extra):
        if submitter is not pyv8.Undefined and submitter is not pyv8.Null:
            if submitter not in self._controls() or getattr(submitter, "localName", "") not in ("button", "input"):
                raise V8TypeError("The specified value is not a submit button.")
            if getattr(submitter, "localName", "") == "input" and submitter.get_type() not in ("submit", "image"):
                raise V8TypeError("The specified value is not a submit button.")
            if getattr(submitter, "localName", "") == "button" and submitter.get_type() != "submit":
                raise V8TypeError("The specified value is not a submit button.")
        if not self.get_noValidate() and not self._check_validity(report=True):
            return pyv8.Undefined
        from .events import Event
        event = Event("submit", {"bubbles": True, "cancelable": True})
        event._trusted = True
        self.fn_dispatchEvent(event)
        return pyv8.Undefined

    def fn_submit(self, *_extra):
        """The DOM ``submit()`` method bypasses validation and submit events."""
        action = self.get_action()
        services = self._services
        if services is not None:
            services.document_services.request_navigation(action)

    def fn_reset(self, *_extra):
        from .html_input import HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement
        from .events import Event
        event = Event("reset", {"bubbles": True, "cancelable": True})
        event._trusted = True
        if not self.fn_dispatchEvent(event):
            return pyv8.Undefined
        for control in self._controls():
            if isinstance(control, HTMLInputElement):
                control._dirty_value = False
                control._raw_value = ""
                control._dirty_checked = False
                control._checked = "checked" in control.attributes
            elif isinstance(control, HTMLTextAreaElement):
                control._dirty_value = False
            elif isinstance(control, HTMLSelectElement):
                control.set_selectedIndex(control.get_selectedIndex())
        return pyv8.Undefined


__all__ = ["HTMLFormElement", "RadioNodeList"]
