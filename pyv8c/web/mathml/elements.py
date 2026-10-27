"""MathML element interfaces shared by parsed and explicitly created nodes."""

from ...idl import interface
from ..css.cssom import CSSStyleDeclaration
from ..css.typed import StylePropertyMap
from ..dom.element import Element


@interface('MathMLElement', 'Element')
class MathMLElement(Element):
    def get_style(self):
        value = getattr(self, '_style_declaration', None)
        if value is None:
            value = self._style_declaration = CSSStyleDeclaration(element=self)
        return value

    def set_style(self, value):
        self.get_style().set_cssText(value)

    def get_attributeStyleMap(self):
        value = getattr(self, '_attribute_style_map', None)
        if value is None:
            value = self._attribute_style_map = StylePropertyMap(element=self)
        return value
