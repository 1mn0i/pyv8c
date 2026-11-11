"""HTML button and label elements."""
from pyv8c import pyv8
from ...idl import interface
from ..dom.element import Element
from .elements import HTMLElement
from .utils import form_owner, url_value

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
        return form_owner(self) or pyv8.Null

    def get_formAction(self):
        return url_value(self, 'formaction')
    def set_formAction(self, value): self._set_string('formaction', value)
    def get_formMethod(self): return self._attr('formmethod')
    def set_formMethod(self, value): self._set_string('formmethod', value)
    def get_formEnctype(self): return self._attr('formenctype')
    def set_formEnctype(self, value): self._set_string('formenctype', value)
    def get_formNoValidate(self): return self._bool('formnovalidate')
    def set_formNoValidate(self, value): self._set_bool('formnovalidate', value)
    def get_formTarget(self): return self._attr('formtarget')
    def set_formTarget(self, value): self._set_string('formtarget', value)

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


