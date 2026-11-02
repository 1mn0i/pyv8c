"""HTML element implementations: the tag-specific interfaces and the tag-to-class
factory."""
import re
import math
from urllib.parse import urljoin, urlsplit, urlunsplit

import pyv8
from pyv8 import AttributeSpec

from ...idl import interface
from ...foundation.arguments import boolean, dom_string, integer, is_nullish
from ..page_errors import dom_exception
from ...foundation.urls import credentials_netloc
from ...foundation.urls import origin_string
from ..dom.character_data import Text
from ..dom.collections import (
    DOMStringMap,
    DOMTokenList,
    HTMLCollection,
    HYPERLINK_REL_TOKENS,
    LINK_REL_TOKENS,
)
from . import attributes as html_props
from ..css.cssom import CSSStyleDeclaration
from ..css.typed import StylePropertyMap
from ..dom.nodes import DocumentFragment


from ..dom.element import Element, _unrendered

@interface('HTMLElement', 'Element')
class HTMLElement(Element):
    #: The attributes this interface reflects in a shape of its own, from a table measured on
    #: Chrome; see web.html.attributes for what each shape does. Reading them changes nothing.
    __idl_overrides__ = {name: {"side_effect": 1} for name in (
        "accessKey", "autocapitalize", "autocorrect", "autofocus", "dir", "draggable",
        "editContext", "enterKeyHint", "focusGroup", "focusGroupStart", "inert", "inputMode",
        "lang", "outerText", "popover", "spellcheck", "translate", "virtualKeyboardPolicy",
        "writingSuggestions",
    )}

    #: The tags whose boundaries innerText turns into newlines. Chrome decides this
    #: from the rendered display, which this tree does not have, so the block-level
    #: HTML elements stand in for it.
    _BLOCK_LEVEL = frozenset((
        "address", "article", "aside", "blockquote", "details", "dialog", "dd", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr",
        "ul"))

    #: What the contenteditable attribute reads back as. An empty attribute and
    #: "true" both mean true, and anything the IDL enum does not hold -- an unknown
    #: keyword or no attribute at all -- reads as "inherit".
    _EDITABLE_VALUES = {"": "true", "true": "true", "false": "false",
                        "plaintext-only": "plaintext-only", "inherit": "inherit"}

    def get_contentEditable(self):
        value = self.attributes.get("contenteditable")
        if value is None:
            return "inherit"
        return self._EDITABLE_VALUES.get(str(value).lower(), "inherit")

    def set_contentEditable(self, value):
        """Set the keyword, or refuse what the IDL enum does not hold.

        Chrome throws a SyntaxError for anything but the four keywords, writes the
        keyword in lower case, and takes the attribute away for "inherit".
        """
        text = str(dom_string(value))
        keyword = text.lower()
        if keyword not in ("true", "false", "plaintext-only", "inherit"):
            raise dom_exception(
                "SyntaxError",
                "Failed to set the 'contentEditable' property on 'HTMLElement': The value"
                f" provided ('{text}') is not one of 'true', 'false', 'plaintext-only', or"
                " 'inherit'.")
        if keyword == "inherit":
            self.attributes.pop("contenteditable", None)
        else:
            self.attributes["contenteditable"] = keyword

    def get_isContentEditable(self):
        """Whether this element is editable: the document's design mode, or the
        nearest contenteditable at or above it. A detached tree is included, as in
        Chrome, while design mode reaches only what is in a document.
        """
        document = self.ownerDocument
        if self.isConnected and getattr(document, "design_mode", "off") == "on":
            return True
        node = self
        while node is not None:
            if isinstance(node, HTMLElement):
                value = node.attributes.get("contenteditable")
                if value is not None:
                    keyword = self._EDITABLE_VALUES.get(str(value).lower(), "inherit")
                    if keyword != "inherit":
                        return keyword != "false"
            node = node.parentNode
        return False

    def get_innerText(self):
        """The text as it would be shown: whitespace collapsed, a block boundary and a
        ``br`` written as newlines, and the ends trimmed -- except in ``pre`` and
        ``textarea``, where the spaces at either end are content rather than layout.
        """
        text = self._rendered_text(self, self.isConnected)
        if str(getattr(self, "localName", "") or "").lower() in ("pre", "textarea"):
            return text
        return text.strip()

    def _rendered_text(self, node, laid_out):
        """The text of *node* and its descendants as it would be shown.

        Chrome decides the line breaks from what it rendered, so a block boundary
        only becomes a newline once the element is in a document: a tree nobody
        attached reads as one run of text, which is what a detached element's
        innerText does in Chrome too. Whitespace collapses unless the element keeps
        it, as ``pre`` and ``textarea`` do. What the page lays out away -- a script
        or style element, a hidden one, anything ``display: none`` reaches -- takes
        its whole subtree out of the result.
        """
        if isinstance(node, Text):
            return node.get_data()
        name = str(getattr(node, "localName", "") or "").lower()
        if name == "br":
            return "\n" if laid_out else ""
        joined = "".join(self._rendered_text(child, laid_out)
                         for child in getattr(node, "_children", ())
                         if not (laid_out and _unrendered(child)))
        if laid_out and name in self._BLOCK_LEVEL:
            joined = "\n" + joined + "\n"
        if name in ("pre", "textarea"):
            return joined
        collapsed = re.sub(r"[ \t\r\f\v]+", " ", joined)
        return re.sub(r" *\n *", "\n", collapsed)

    def set_innerText(self, value):
        """Replace the children with the text, one line break at a time.

        The text is taken as written -- markup in it is not parsed -- and a newline
        becomes a ``br`` rather than a character in the text, which is what makes the
        setter and the getter round-trip. Null clears the element.
        """
        from .registry import class_for_tag

        self._replace_children(())
        if is_nullish(value):
            return
        for index, line in enumerate(re.split(r"\r\n|\r|\n", str(dom_string(value)))):
            if index:
                self.append_child(class_for_tag("br")("br", services=self._services))
            if line:
                self.append_child(Text(line, services=self._services))

    def get_nonce(self): return self._attr("nonce")
    def set_nonce(self, value): self._set_string("nonce", value)
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v):
        v = dom_string(v)
        self.attributes['title'] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        v = boolean(v)
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)
    def _attr(self, name):
        """The value of a content attribute as a string, ``""`` when absent."""
        value = self.attributes.get(name)
        return "" if value is None else str(value)
    def _set_string(self, name, value):
        self.attributes[name] = str(dom_string(value))
    def _bool(self, name): return name in self.attributes
    def _set_bool(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)
    def set_style(self, value):
        """``style`` is ``[PutForwards=cssText]``: an assignment lands on the declaration."""
        self.get_style().set_cssText(dom_string(value))
    def get_style(self):
        value = getattr(self, "_style_declaration", None)
        if value is None:
            value = self._style_declaration = CSSStyleDeclaration(element=self)
        return value
    def get_attributeStyleMap(self):
        value = getattr(self, "_attribute_style_map", None)
        if value is None:
            value = self._attribute_style_map = StylePropertyMap(element=self)
        return value
    def get_dataset(self):
        value = getattr(self, "_dataset_map", None)
        if value is None:
            value = self._dataset_map = DOMStringMap(self)
        return value
    def get_offsetParent(self):
        from ..dom.layout import offset_parent
        return offset_parent(self) or pyv8.Null
    def _offset_box(self):
        """This element's box, and where its ``offsetParent`` measures from."""
        from ..dom.layout import layout, layout_snapshot, offset_parent
        document = self.ownerDocument
        boxes, styles = layout_snapshot(document) if document is not None else ({}, {})
        parent = offset_parent(self, styles=styles)
        box = boxes.get(self)
        if box is None:
            box = layout(self)
        if parent is None:
            return box, 0.0, 0.0
        parent_box = boxes.get(parent)
        if parent_box is None:
            parent_box = layout(parent)
        return box, parent_box.padding_box_left, parent_box.padding_box_top
    def get_offsetTop(self):
        box, _left, top = self._offset_box()
        return int(round(box.top - top))
    def get_offsetLeft(self):
        box, left, _top = self._offset_box()
        return int(round(box.left - left))
    def get_offsetWidth(self):
        from ..dom.layout import layout
        return math.floor(layout(self).width + 0.5)
    def get_offsetHeight(self):
        from ..dom.layout import layout
        return math.floor(layout(self).height + 0.5)

    def get_tabIndex(self):
        """The element's tab index: the attribute when present, else Chromium's default
        -- 0 for a focusable area, -1 otherwise; an unparseable value reads as 0."""
        from ..dom.focus import focusable
        value = self.attributes.get("tabindex")
        if value is None:
            return 0 if focusable(self) else -1
        digits = str(value).strip().lstrip("+-")
        return int(digits) if digits.isdigit() else 0

    def set_tabIndex(self, value):
        self.attributes["tabindex"] = str(integer(value, signed=True))

    def fn_focus(self, *_extra):
        """Focus this element when it is a focusable area; Chrome ignores the call on
        anything else, so the check lives in :func:`dom.focus.focus`."""
        from ..dom.focus import focus
        focus(self)

    def fn_blur(self, *_extra):
        """Drop focus, when this element is the one holding it."""
        from ..dom.focus import blur
        document = self.ownerDocument
        if document is not None and getattr(document, "_focused_element", None) is self:
            blur(document)

    def fn_click(self, *_extra):
        """Run the synthetic click activation steps a script asked for: an untrusted
        ``click`` at the origin, then the behavior unless a listener cancelled it."""
        from ..dom.activation import click_element
        click_element(self)





#: The accessors for the reflected attributes above; ``html_props`` gives the class one
#: getter/setter pair per member, and the convention binds them to the members it states.
html_props.install(HTMLElement)


@interface('HTMLUnknownElement', 'HTMLElement')
class HTMLUnknownElement(HTMLElement):
    """The interface Chromium exposes for tags with no dedicated class: obsolete
    tags, custom element names and anything else unmodelled.  ``allow_new=false``,
    so the constructor throws; the host still builds instances through ``__init__``.
    """


#: The states ``button.type`` reports; a missing or unknown value is ``submit``.
_BUTTON_TYPES = frozenset(("submit", "reset", "button"))


@interface('HTMLButtonElement', 'HTMLElement')
class HTMLButtonElement(HTMLElement):
    """``<button>``: its name and value, and the state ``type`` reports."""

    def get_name(self): return self._attr("name")
    def set_name(self, value): self._set_string("name", value)
    def get_value(self): return self._attr("value")
    def set_value(self, value): self._set_string("value", value)
    def get_type(self):
        value = self._attr("type").lower()
        return value if value in _BUTTON_TYPES else "submit"
    def set_type(self, value): self._set_string("type", value)
    def get_form(self):
        from .forms import HTMLFormElement
        form_id = self.attributes.get("form")
        document = getattr(getattr(self._services, "document_services", None), "document", None)
        if form_id and document is not None:
            owner = document.fn_getElementById(form_id)
            if isinstance(owner, HTMLFormElement):
                return owner
        node = self.parentElement
        while isinstance(node, Element):
            if isinstance(node, HTMLFormElement):
                return node
            node = node.parentElement
        return pyv8.Null

    def _activatable(self):
        """A submit or reset button clicks into its form; a plain one does nothing."""
        return self.get_type() in ("submit", "reset") and not self._bool("disabled")

    def _run_activation(self, event=None):
        """Submit or reset the form this button belongs to; this button is the
        submission's submitter, so its name and value join the entries."""
        form = self.get_form()
        if form is None or form is pyv8.Null:
            return
        if self.get_type() == "submit":
            from ..dom.activation import user_activated
            form._submit(self, user_activated=user_activated(event))
        else:
            form.fn_reset()


def _owning_table(row):
    """The nearest ``table`` ancestor of *row*, which is the table that counts it."""
    parent = getattr(row, "parentElement", None)
    while parent is not None:
        if isinstance(parent, HTMLTableElement):
            return parent
        parent = getattr(parent, "parentElement", None)
    return None


@interface('HTMLTableElement', 'HTMLElement')
class HTMLTableElement(HTMLElement):
    """``<table>``: the rows it owns -- those in its sections, in document order --
    but not the rows of a table nested inside it, which belong to that table."""

    def get_rows(self):
        view = getattr(self, "_rows_view", None)
        if view is None:
            view = self._rows_view = HTMLCollection(owner=self, getter=self._rows)
        return view

    def _rows(self):
        return [node for node in self._descendants()
                if isinstance(node, HTMLTableRowElement) and _owning_table(node) is self]


@interface('HTMLTableRowElement', 'HTMLElement')
class HTMLTableRowElement(HTMLElement):
    """``<tr>``: the cells it holds and where the row sits in its table."""

    def get_cells(self):
        view = getattr(self, "_cells_view", None)
        if view is None:
            view = self._cells_view = HTMLCollection(owner=self, getter=self._cells)
        return view

    def _cells(self):
        """A row's own cells: its ``td`` and ``th`` children, in document order."""
        return [child for child in self._children if isinstance(child, HTMLTableCellElement)]

    def get_rowIndex(self):
        """The row's position in its table's ``rows``, or -1 when no table owns it."""
        table = _owning_table(self)
        if table is None:
            return -1
        return next((index for index, row in enumerate(table._rows()) if row is self), -1)


@interface('HTMLTableCellElement', 'HTMLElement')
class HTMLTableCellElement(HTMLElement):
    """``<td>`` and ``<th>``: where the cell sits in its row."""

    def get_cellIndex(self):
        """The cell's position in its row's ``cells``, or -1 when no row holds it."""
        row = getattr(self, "parentElement", None)
        if not isinstance(row, HTMLTableRowElement):
            return -1
        return next((index for index, cell in enumerate(row._cells()) if cell is self), -1)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


def _attribute(name, getter, setter=None):
    return AttributeSpec(name=name, getter=getter, setter=setter)


def _document_url(element):
    """The URL a relative attribute on *element* resolves against."""
    return str(getattr(getattr(element, "ownerDocument", None), "url", "") or "")


def _url_value(element, name):
    """A URL attribute's value, resolved against the element's document: the content
    attribute keeps what the page wrote, and absent or empty reads back as ``""``."""
    raw = str(element.attributes.get(name, ""))
    return urljoin(_document_url(element), raw) if raw else ""


def _set_url_value(element, name, value):
    element.attributes[name] = str(dom_string(value))


def _url_attr(name, getter, setter):
    """Declare a URL-reflecting attribute; pair it with the two helpers above."""
    return _attribute(name, getter, setter)


#: The elements a ``label`` may point at, by the HTML definition.
_LABELABLE_TAGS = frozenset(("button", "meter", "output", "progress", "select", "textarea"))


def _is_labelable(node):
    """Whether *node* is a labelable element, the kind a ``label`` can label."""
    if not isinstance(node, Element):
        return False
    if node.localName == "input":
        return str(node.get_type()) != "hidden"
    return node.localName in _LABELABLE_TAGS


@interface('HTMLLabelElement', 'HTMLElement')
class HTMLLabelElement(HTMLElement):
    """``<label>``: the control it labels (by ``for`` id, or its first labelable
    descendant) and the click it forwards to it -- a focus, then a click event."""

    def get_htmlFor(self): return self._attr("for")
    def set_htmlFor(self, value): self._set_string("for", value)
    def get_control(self):
        control = self._labeled_control()
        return pyv8.Null if control is None else control
    def _labeled_control(self):
        """The control this label labels, or ``None`` when it labels nothing."""
        target_id = self.attributes.get("for")
        if target_id:
            document = getattr(getattr(self._services, "document_services", None), "document", None)
            found = document.fn_getElementById(target_id) if document is not None else pyv8.Null
            return found if _is_labelable(found) else None
        return next((node for node in self._descendants() if _is_labelable(node)), None)
    def get_form(self):
        """The labeled control's form, so ``label.form`` answers what Chromium's does."""
        control = self._labeled_control()
        finder = getattr(control, "get_form", None)
        if finder is not None:
            form = finder()
            if form is not None and form is not pyv8.Null:
                return form
        node = self.parentElement
        while isinstance(node, Element):
            if node.localName == "form":
                return node
            node = node.parentElement
        return pyv8.Null

    def _activatable(self):
        """A label clicks into the control it labels."""
        return self._labeled_control() is not None

    def _run_activation(self, event=None):
        """Focus the labeled control and click it, the order Chrome uses.  The
        click repeats the label's own press (same coordinates, count and trust)."""
        control = self._labeled_control()
        if control is None:
            return
        from ..dom.activation import click_element, repeat_click
        from ..dom.focus import focus
        focus(control)
        if event is None:
            click_element(control)
        else:
            repeat_click(control, event)


@interface('HTMLImageElement', 'HTMLElement')
class HTMLImageElement(HTMLElement):
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value):
        _set_url_value(self, "src", value)
        self._reset_image_load()
        self._restart_resource()
    def _reset_image_load(self):
        # A new URL is a new load: nothing has settled and no size is known until it does.
        self._image_complete = False
        self._image_bytes = None
        self._image_type = ""
        self.__dict__.pop("_natural_cache", None)
    def _attribute_changed_here(self, name, old, new):
        super()._attribute_changed_here(name, old, new)
        if name == "src":
            self._reset_image_load()
            self._restart_resource()
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value):
        value = dom_string(value)
        self.attributes['alt'] = str(value)
    def _dimension(self, name):
        """Rendered content size, or the dimension attribute/intrinsic fallback."""
        if self.isConnected:
            from ..dom.layout import layout_snapshot

            boxes, styles = layout_snapshot(self.ownerDocument)
            box = boxes.get(self)
            if box is not None and styles.get(self, {}).get("display") != "none":
                sides = ("left", "right") if name == "width" else ("top", "bottom")
                content = getattr(box, name) - sum(
                    box.padding.get(side, 0) + box.border.get(side, 0) for side in sides)
                return max(0, int(content + 0.5))
        digits = ""
        for character in str(self.attributes.get(name, "")).strip():
            if character not in "0123456789":
                break
            digits += character
        return int(digits) if digits else self._natural_size()[name == "height"]
    def _set_dimension(self, name, value):
        # Not an unsigned long: a negative dimension stores as 0 rather than wrapping.
        self.attributes[name] = str(max(0, integer(value, signed=True)))
    def get_width(self): return self._dimension("width")
    def set_width(self, value): self._set_dimension("width", value)
    def get_height(self): return self._dimension("height")
    def set_height(self, value): self._set_dimension("height", value)
    def get_complete(self):
        """Whether the element is waiting on anything: no source, or a load that has
        settled either way -- Chrome answers false only while one is in flight.
        """
        if not self.attributes.get("src"):
            return True
        if not getattr(self, "_resource_started", False):
            return True
        return bool(getattr(self, "_image_complete", False))

    def _natural_size(self):
        """The size the loaded file declares, read once and kept.

        Chromium answers ``0`` until the image has settled, and the file's own header is what
        it answers with after: no decoded bitmap is needed for the question a page asks.
        """
        cached = getattr(self, "_natural_cache", None)
        if cached is not None:
            return cached
        data = getattr(self, "_image_bytes", None)
        if not isinstance(data, (bytes, bytearray)):
            return (0, 0)
        from ..dom.images import image_size

        size = image_size(data, getattr(self, "_image_type", ""))
        if size != (0, 0):
            self._natural_cache = size
        return size

    def get_naturalWidth(self): return self._natural_size()[0]
    def get_naturalHeight(self): return self._natural_size()[1]


@interface('HTMLScriptElement', 'HTMLElement')
class HTMLScriptElement(HTMLElement):
    __idl_overrides__ = {
        "text": {
            "getter": "get_textContent",
            "setter": "set_textContent"
        }
    }
    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_type(self): return self.attributes.get("type", "")
    def set_type(self, value):
        value = dom_string(value)
        self.attributes['type'] = str(value)
    def get_charset(self): return self.attributes.get("charset", "")
    def set_charset(self, value): self.attributes['charset'] = str(dom_string(value))
    def get_async(self): return "async" in self.attributes
    def set_async(self, value):
        value = boolean(value)
        if value: self.attributes["async"] = ""
        else: self.attributes.pop("async", None)
    def get_defer(self): return "defer" in self.attributes
    def set_defer(self, value):
        value = boolean(value)
        if value: self.attributes["defer"] = ""
        else: self.attributes.pop("defer", None)

    def _clone_shallow(self):
        clone = super()._clone_shallow()
        # HTML script cloning carries its already executed state to the clone.
        clone._script_executed = getattr(self, "_script_executed", False)
        return clone



#: The destinations ``link.as`` may report; anything else reads back as empty.
LINK_AS_VALUES = frozenset({
    "audio", "audioworklet", "document", "embed", "fetch", "font", "frame", "iframe",
    "image", "json", "manifest", "object", "paintworklet", "report", "script",
    "serviceworker", "sharedworker", "style", "track", "video", "webidentity", "worker",
    "xslt",
})


@interface('HTMLLinkElement', 'HTMLElement')
class HTMLLinkElement(HTMLElement):
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", LINK_REL_TOKENS)
        return value
    def get_href(self): return _url_value(self, "href")
    def set_href(self, value):
        _set_url_value(self, "href", value)
        self._restart_resource()
    #: ``rel`` and ``as`` decide whether an ``href`` is a resource at all.
    _RESOURCE_ATTRIBUTES = ("href", "rel", "as")
    def _attribute_changed_here(self, name, old, new):
        super()._attribute_changed_here(name, old, new)
        if name in self._RESOURCE_ATTRIBUTES:
            self._restart_resource()
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value):
        value = dom_string(value)
        self.attributes['rel'] = str(value)
        self._restart_resource()
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)
    def get_as(self):
        value = str(self.attributes.get("as", "")).lower()
        return value if value in LINK_AS_VALUES else ""
    def set_as(self, value):
        value = dom_string(value)
        self.attributes['as'] = str(value)
        self._restart_resource()


@interface('HTMLAnchorElement', 'HTMLElement')
class HTMLAnchorElement(HTMLElement):
    """``<a>``: the URL decomposition members ``href`` carries.  Assigning one parses
    the current href, replaces that component and writes the result back, per URLUtils."""

    def _href(self):
        """The href every URL member below is derived from."""
        return _url_value(self, "href")

    def _parts(self):
        return urlsplit(self._href())

    def _set_component(self, **changes):
        self.attributes['href'] = urlunsplit(self._parts()._replace(**changes))

    def get_href(self): return self._href()
    def set_href(self, value): _set_url_value(self, "href", value)
    def get_origin(self): return origin_string(self._href()) if self._href() else ""
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ":"
    def set_protocol(self, value): self._set_component(scheme=str(dom_string(value)).rstrip(":"))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        self._set_credentials(username=str(dom_string(value)), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        self._set_credentials(username=self.get_username(), password=str(dom_string(value)))
    def _set_credentials(self, *, username, password):
        parts = self._parts()
        self._set_component(
            netloc=credentials_netloc(parts.hostname or "", parts.port, username, password))
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value): self._set_component(netloc=str(dom_string(value)))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._set_component(netloc=credentials + str(dom_string(value)) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        parts = self._parts()
        credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        value = str(dom_string(value))
        self._set_component(netloc=credentials + (parts.hostname or "") + ((":" + value) if value else ""))
    def get_pathname(self):
        parts = self._parts()
        return parts.path or ("/" if parts.netloc else "")
    def set_pathname(self, value): self._set_component(path=str(dom_string(value)) or "/")
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value): self._set_component(query=str(dom_string(value)).lstrip("?"))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value): self._set_component(fragment=str(dom_string(value)).lstrip("#"))
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(dom_string(value))
    def get_target(self): return self.attributes.get("target", "")
    def set_target(self, value): self.attributes["target"] = str(dom_string(value))
    def get_relList(self):
        value = getattr(self, "_rel_list", None)
        if value is None:
            value = self._rel_list = DOMTokenList(self, "rel", HYPERLINK_REL_TOKENS)
        return value
    def fn_toString(self, *_extra): return self._href()

    def _activatable(self):
        """A link with an ``href`` navigates when clicked; a ``download`` link is
        declined (Chromium saves it, and this DOM has nowhere to save it)."""
        return bool(self._href()) and "download" not in self.attributes

    def _run_activation(self, event=None):
        """Follow the link via the location object, so a ``#fragment`` link stays a
        same-document navigation instead of a reload, as Chromium treats it."""
        href = self._href()
        if not href:
            return
        window = getattr(getattr(self._services, "document_services", None), "window", None)
        if window is not None:
            window.location.set_href(href)


@interface('HTMLStyleElement', 'HTMLElement')
class HTMLStyleElement(HTMLElement):
    def get_sheet(self):
        """The sheet this element owns, which is the one its document's list holds for
        it, and null while it is in no document: the list is what owns a sheet."""
        document = self.ownerDocument
        if document is None:
            return pyv8.Null
        for owner in document.get_styleSheets()._owners():
            if owner is self:
                return document.get_styleSheets()._sheet_for(owner)
        return pyv8.Null
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = str(value)
    def get_type(self):
        """Plain reflection, not a filtered one: whatever the page wrote reads back
        as it was written, and an absent attribute is the empty string."""
        return str(self.attributes.get("type", ""))
    def set_type(self, value):
        self.attributes["type"] = str(dom_string(value))


@interface('HTMLMetaElement', 'HTMLElement')
class HTMLMetaElement(HTMLElement):
    """The HTML ``meta`` element's reflected string attributes."""

    def get_name(self): return self.attributes.get("name", "")
    def set_name(self, value):
        value = dom_string(value)
        self.attributes['name'] = dom_string(value)
    def get_httpEquiv(self): return self.attributes.get("http-equiv", "")
    def set_httpEquiv(self, value):
        value = dom_string(value)
        self.attributes['http-equiv'] = dom_string(value)
    def get_content(self): return self.attributes.get("content", "")
    def set_content(self, value):
        value = dom_string(value)
        self.attributes['content'] = dom_string(value)
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value):
        value = dom_string(value)
        self.attributes['media'] = dom_string(value)
    def get_scheme(self): return self.attributes.get("scheme", "")
    def set_scheme(self, value):
        value = dom_string(value)
        self.attributes['scheme'] = dom_string(value)


@interface('HTMLTemplateElement', 'HTMLElement')
class HTMLTemplateElement(HTMLElement):
    def __init__(self, tag_name="template", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self.content = DocumentFragment(services=services)
    def get_content(self): return self.content

__all__ = [
    "HTMLElement", "HTMLUnknownElement", "HTMLImageElement", "HTMLScriptElement",
    "HTMLLinkElement", "HTMLStyleElement", "HTMLMetaElement", "HTMLTemplateElement",
]
