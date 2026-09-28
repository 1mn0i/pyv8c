"""Textual node types: ``CharacterData``, ``Text``, ``Comment``, ``DocumentType``."""

from html import escape

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, integer, missing_error
from .mixins import _ChildNodeMixin, _NonDocumentTypeChildNodeMixin
from .nodes import Node, _bump_style_version


@interface('CharacterData', 'Node')
class CharacterData(_ChildNodeMixin, _NonDocumentTypeChildNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="data", getter="get_data", side_effect=1),
            AttributeSpec(name="length", getter="get_length", side_effect=1),
        ),
        methods=(
            MethodSpec(name="substringData", callback="fn_substringData", length=2),
            MethodSpec(name="appendData", callback="fn_appendData", length=1),
            MethodSpec(name="insertData", callback="fn_insertData", length=2),
            MethodSpec(name="deleteData", callback="fn_deleteData", length=2),
            MethodSpec(name="replaceData", callback="fn_replaceData", length=3),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, data='', *_extra, services=None, **kw):
        if data is pyv8.Undefined:
            data = ''
        if data is not pyv8.Undefined:
            data = dom_string(data)
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services, node_type=Node.TEXT_NODE, node_name='#text', value=str(data), **kw)
    def get_data(self): return self.nodeValue or ""
    def set_data(self, value):
        self.nodeValue = str(value)
        _bump_style_version(self)
    def get_length(self): return len(self.nodeValue or "")
    def fn_substringData(self, offset=MISSING, count=MISSING, *_extra):
        if offset is MISSING:
            raise missing_error('CharacterData', 'substringData', 2, 0, constructor=False)
        if count is MISSING:
            raise missing_error('CharacterData', 'substringData', 2, 1, constructor=False)
        offset = integer(offset)
        count = integer(count)
        start, end = int(offset), int(offset) + int(count)
        return (self.nodeValue or "")[start:end]
    def fn_appendData(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('CharacterData', 'appendData', 1, 0, constructor=False)
        value = dom_string(value)
        self.nodeValue = (self.nodeValue or '') + str(value)
        _bump_style_version(self)
    def fn_insertData(self, offset=MISSING, value=MISSING, *_extra):
        if offset is MISSING:
            raise missing_error('CharacterData', 'insertData', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('CharacterData', 'insertData', 2, 1, constructor=False)
        offset = integer(offset)
        value = dom_string(value)
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + str(value) + data[start:]
        _bump_style_version(self)
    def fn_deleteData(self, offset=MISSING, count=MISSING, *_extra):
        if offset is MISSING:
            raise missing_error('CharacterData', 'deleteData', 2, 0, constructor=False)
        if count is MISSING:
            raise missing_error('CharacterData', 'deleteData', 2, 1, constructor=False)
        offset = integer(offset)
        count = integer(count)
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + data[start + int(count):]
        _bump_style_version(self)
    def fn_replaceData(self, offset=MISSING, count=MISSING, value=MISSING, *_extra):
        if offset is MISSING:
            raise missing_error('CharacterData', 'replaceData', 3, 0, constructor=False)
        if count is MISSING:
            raise missing_error('CharacterData', 'replaceData', 3, 1, constructor=False)
        if value is MISSING:
            raise missing_error('CharacterData', 'replaceData', 3, 2, constructor=False)
        offset = integer(offset)
        count = integer(count)
        value = dom_string(value)
        data, start = self.nodeValue or "", int(offset)
        self.nodeValue = data[:start] + str(value) + data[start + int(count):]
        _bump_style_version(self)
    def _clone_shallow(self):
        # Keep the concrete flavour so clones expose ``data`` and the right node type.
        return type(self)(self.nodeValue or "", services=self._services)
    def get_textContent(self): return self.nodeValue or ""
    def set_textContent(self, value): self.set_data(value)
    def outer_html(self): return escape(self.nodeValue or "")


@interface('Text', 'CharacterData')
class Text(CharacterData):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=1, has_constructor=1)
    pass


@interface('Comment', 'CharacterData')
class Comment(CharacterData):
    __v8_idl_spec__ = IdlSpec(constructor_behavior=1, has_constructor=1)
    def __init__(self, data='', *_extra, services=None, **kw):
        if data is pyv8.Undefined:
            data = ''
        if data is not pyv8.Undefined:
            data = dom_string(data)
        if services is pyv8.Undefined:
            services = None
        super().__init__(data, services=services, **kw)
        self.nodeType, self.nodeName = (Node.COMMENT_NODE, '#comment')
    def outer_html(self): return "<!--" + (self.nodeValue or "") + "-->"


@interface('DocumentType', 'Node')
class DocumentType(_ChildNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="name", getter="get_name", side_effect=1),
            AttributeSpec(name="publicId", getter="get_publicId", side_effect=1),
            AttributeSpec(name="systemId", getter="get_systemId", side_effect=1),
        ),
        constructor_behavior=0,
        has_constructor=1,
    )
    def __init__(self, name="html", public_id="", system_id="", services=None, **kw):
        super().__init__(services=services, node_type=Node.DOCUMENT_TYPE_NODE, node_name=name, value=None, **kw)
        self._public_id, self._system_id = str(public_id), str(system_id)
    def get_name(self): return self.nodeName
    def get_publicId(self): return self._public_id
    def get_systemId(self): return self._system_id
    def outer_html(self): return "<!DOCTYPE " + self.nodeName + ">"
