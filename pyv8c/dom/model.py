from html import escape
import time
from typing import Iterable

import pyv8
from pyv8.flag import FlagExposed, V8Attribute
from pyv8.tools import ConstructorConfig

from ..bindings.registry import interface, js_attributes, js_methods
from ..core.errors import V8TypeError, V8SyntaxError, HierarchyRequestError, NotFoundError, InvalidCharacterError


def _js_call(fn, this, *args):
    if hasattr(fn, "call"):
        return fn.call(this, *args)
    return fn(*args)


@interface("NodeList", None, constructible=False, tag="NodeList")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class NodeList:
    """A live list view. The owner remains the source of truth."""
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1))
    def __init__(self, owner=None, getter=None, values=None, **_):
        self._owner, self._getter, self._values = owner, getter, values
    def _list(self):
        return list(self._getter()) if self._getter else list(self._values or ())
    def get_length(self): return len(self._list())
    def fn_item(self, index):
        try: return self._list()[int(index)]
        except (IndexError, ValueError, TypeError): return pyv8.Null
    def __iter__(self): return iter(self._list())
    def __getitem__(self, index): return self._list()[index]
    def __len__(self): return len(self._list())
    __v8_index_get__ = lambda self, index: self.fn_item(index)
    __v8_index_query__ = lambda self, index: int(index) < len(self)
    __v8_index_enum__ = lambda self: list(range(len(self)))


@interface("HTMLCollection", None, constructible=False, tag="HTMLCollection")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLCollection(NodeList):
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("namedItem", "fn_namedItem", 1, 1))
    def fn_namedItem(self, name):
        value = str(name)
        return next((n for n in self._list() if getattr(n, "get_id", lambda: "")() == value or
                     getattr(n, "attributes", {}).get("name") == value), pyv8.Null)


@interface("DOMTokenList", None, constructible=False, tag="DOMTokenList")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class DOMTokenList:
    __v8_attribute__ = js_attributes(("length", "get_length", None, 0, 1), ("value", "get_value", "set_value", 0, 0))
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("contains", "fn_contains", 1, 1),
                               ("add", "fn_add", 1, 0), ("remove", "fn_remove", 1, 0),
                               ("toggle", "fn_toggle", 1, 0), ("replace", "fn_replace", 2, 0))
    def __init__(self, element=None, attr="class", **_): self._element, self._attr = element, attr
    def _tokens(self): return [x for x in str(self._element.attributes.get(self._attr, "")).split() if x]
    def _validate(self, token):
        token = str(token)
        if not token: raise V8TypeError("The token provided must not be empty.")
        if any(ch.isspace() for ch in token):
            raise InvalidCharacterError("The token provided contains HTML space characters, which are not valid in tokens.")
        return token
    def _set(self, values): self._element.attributes[self._attr] = " ".join(dict.fromkeys(values))
    def get_length(self): return len(self._tokens())
    def get_value(self): return " ".join(self._tokens())
    def set_value(self, value): self._set([self._validate(token) for token in str(value).split()])
    def fn_item(self, index):
        try: return self._tokens()[int(index)]
        except (IndexError, ValueError, TypeError): return pyv8.Null
    def fn_contains(self, token): return self._validate(token) in self._tokens()
    def fn_add(self, *tokens): self._set(self._tokens() + [self._validate(x) for x in tokens])
    def fn_remove(self, *tokens): self._set([x for x in self._tokens() if x not in {self._validate(t) for t in tokens}])
    def fn_toggle(self, token, force=pyv8.Undefined):
        token = self._validate(token); present = token in self._tokens()
        if force is not pyv8.Undefined: present = not bool(force)
        if present: self.fn_remove(token); return False
        self.fn_add(token); return True
    def fn_replace(self, old, new):
        values = self._tokens(); old, new = self._validate(old), self._validate(new)
        if old not in values: return False
        self._set([new if x == old else x for x in values]); return True
    def __iter__(self): return iter(self._tokens())


@interface("Event", None, constructible=True, length=1, tag="Event")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Event:
    __v8_attribute__ = js_attributes(
        ("type", "get_type", None, 0, 1), ("target", "get_target", None, 0, 1),
        ("currentTarget", "get_currentTarget", None, 0, 1), ("bubbles", "get_bubbles", None, 0, 1),
        ("cancelable", "get_cancelable", None, 0, 1), ("defaultPrevented", "get_defaultPrevented", None, 0, 1),
        ("eventPhase", "get_eventPhase", None, 0, 1), ("cancelBubble", "get_cancelBubble", "set_cancelBubble", 0, 0),
        ("timeStamp", "get_timeStamp", None, 0, 1), ("isTrusted", "get_isTrusted", None, 0, 1),
        ("composed", "get_composed", None, 0, 1), ("returnValue", "get_returnValue", "set_returnValue", 0, 0),
        ("persisted", "get_persisted", None, 0, 1),
    )
    __v8_method__ = js_methods(("preventDefault", "fn_preventDefault", 0, 0), ("stopPropagation", "fn_stopPropagation", 0, 0), ("stopImmediatePropagation", "fn_stopImmediatePropagation", 0, 0), ("composedPath", "fn_composedPath", 0, 1))
    def __init__(self, event_type="", options=None, **_):
        options = options if isinstance(options, dict) else {}
        self.type, self.bubbles, self.cancelable = str(event_type), bool(options.get("bubbles", False)), bool(options.get("cancelable", False))
        self.target = self.currentTarget = None; self.eventPhase = 0; self.defaultPrevented = False
        self._stop = self._immediate = False; self._path = []; self._passive = False
        self._time_stamp = time.time() * 1000.0; self._trusted = bool(options.get("isTrusted", False))
        self._composed = bool(options.get("composed", False)); self._persisted = bool(options.get("persisted", False)); self._return_value = True
    def get_type(self): return self.type
    def get_target(self): return self.target or pyv8.Null
    def get_currentTarget(self): return self.currentTarget or pyv8.Null
    def get_bubbles(self): return self.bubbles
    def get_cancelable(self): return self.cancelable
    def get_defaultPrevented(self): return self.defaultPrevented
    def get_eventPhase(self): return self.eventPhase
    def get_cancelBubble(self): return self._stop
    def set_cancelBubble(self, value): self._stop = bool(value)
    def get_timeStamp(self): return self._time_stamp
    def get_isTrusted(self): return self._trusted
    def get_composed(self): return self._composed
    def get_persisted(self): return self._persisted
    def get_returnValue(self): return not self.defaultPrevented
    def set_returnValue(self, value):
        self._return_value = bool(value)
        if not self._return_value and self.cancelable: self.defaultPrevented = True
    def fn_preventDefault(self):
        if self.cancelable and not self._passive: self.defaultPrevented = True
    def fn_stopPropagation(self): self._stop = True
    def fn_stopImmediatePropagation(self): self._stop = self._immediate = True
    def fn_composedPath(self): return list(self._path)


@interface("UIEvent", "Event", constructible=True, length=1, tag="UIEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class UIEvent(Event):
    pass


@interface("MouseEvent", "UIEvent", constructible=True, length=1, tag="MouseEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class MouseEvent(UIEvent):
    pass


@interface("KeyboardEvent", "UIEvent", constructible=True, length=1, tag="KeyboardEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class KeyboardEvent(UIEvent):
    pass


@interface("CustomEvent", "Event", constructible=True, length=1, tag="CustomEvent")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class CustomEvent(Event):
    __v8_attribute__ = js_attributes(("detail", "get_detail", None, 0, 1))
    def __init__(self, event_type="", options=None, **kw):
        super().__init__(event_type, options, **kw)
        options = options if isinstance(options, dict) else {}
        self.detail = options.get("detail", pyv8.Null)
    def get_detail(self): return self.detail


@interface("EventTarget", None, constructible=True)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class EventTarget:
    __v8_attribute__ = js_attributes(
        ("onload", "get_onload", "set_onload", 0, 0),
        ("onerror", "get_onerror", "set_onerror", 0, 0),
        ("onreadystatechange", "get_onreadystatechange", "set_onreadystatechange", 0, 0),
        ("onpageshow", "get_onpageshow", "set_onpageshow", 0, 0),
        ("onpagehide", "get_onpagehide", "set_onpagehide", 0, 0),
        ("onunload", "get_onunload", "set_onunload", 0, 0),
        ("onbeforeunload", "get_onbeforeunload", "set_onbeforeunload", 0, 0),
    )
    __v8_method__ = js_methods(
        ("addEventListener", "fn_addEventListener", 2, 0),
        ("removeEventListener", "fn_removeEventListener", 2, 0),
        ("dispatchEvent", "fn_dispatchEvent", 1, 0),
    )

    def __init__(self, page=None, **_):
        self._page = page
        self._listeners = {}
        self._listener_order = 0
        self._handlers = {}

    def _get_handler(self, name): return self._handlers.get(name, pyv8.Null)
    def _set_handler(self, name, value): self._handlers[name] = value
    def get_onload(self): return self._get_handler("onload")
    def set_onload(self, value): self._set_handler("onload", value)
    def get_onerror(self): return self._get_handler("onerror")
    def set_onerror(self, value): self._set_handler("onerror", value)
    def get_onreadystatechange(self): return self._get_handler("onreadystatechange")
    def set_onreadystatechange(self, value): self._set_handler("onreadystatechange", value)
    def get_onpageshow(self): return self._get_handler("onpageshow")
    def set_onpageshow(self, value): self._set_handler("onpageshow", value)
    def get_onpagehide(self): return self._get_handler("onpagehide")
    def set_onpagehide(self, value): self._set_handler("onpagehide", value)
    def get_onunload(self): return self._get_handler("onunload")
    def set_onunload(self, value): self._set_handler("onunload", value)
    def get_onbeforeunload(self): return self._get_handler("onbeforeunload")
    def set_onbeforeunload(self, value): self._set_handler("onbeforeunload", value)

    def fn_addEventListener(self, event_type, listener, options=False):
        capture = bool(options.get("capture", False)) if isinstance(options, dict) else bool(options)
        once = bool(options.get("once", False)) if isinstance(options, dict) else False
        passive = bool(options.get("passive", False)) if isinstance(options, dict) else False
        signal = options.get("signal") if isinstance(options, dict) else None
        bucket = self._listeners.setdefault(str(event_type), [])
        if not any(item[0] is listener and item[1] == capture for item in bucket):
            self._listener_order += 1; bucket.append((listener, capture, once, passive, signal, self._listener_order))

    def fn_removeEventListener(self, event_type, listener, options=False):
        capture = bool(options.get("capture", False)) if isinstance(options, dict) else bool(options)
        bucket = self._listeners.get(str(event_type), [])
        self._listeners[str(event_type)] = [x for x in bucket if not (x[0] is listener and x[1] == capture)]

    def fn_dispatchEvent(self, event):
        if not isinstance(event, Event):
            raise V8TypeError("Failed to execute 'dispatchEvent' on 'EventTarget': parameter 1 is not of type 'Event'.")
        event.target = event.target or self
        path = []
        node = self
        while node is not None:
            path.append(node)
            node = getattr(node, "parentNode", None)
            if node is None and getattr(path[-1], "nodeType", None) == getattr(Node, "DOCUMENT_NODE", 9):
                node = getattr(getattr(path[-1], "_page", None), "window", None)
        event._path = list(path)
        for target in reversed(path[1:]):
            event.eventPhase, event.currentTarget = 1, target
            target._invoke(event, capture=True)
            if event._stop:
                return not event.defaultPrevented
        event.eventPhase, event.currentTarget = 2, self
        self._invoke(event, capture=True)
        if not event._immediate: self._invoke(event, capture=False)
        if event.bubbles and not event._stop:
            for target in path[1:]:
                event.eventPhase, event.currentTarget = 3, target
                target._invoke(event, capture=False)
                if event._stop: break
        event.eventPhase, event.currentTarget = 0, None
        return not event.defaultPrevented

    def _invoke(self, event, capture):
        for listener, registered_capture, once, passive, signal, _order in list(self._listeners.get(event.type, ())):
            if registered_capture != capture:
                continue
            if signal is not None and getattr(signal, "aborted", False): continue
            event._passive = passive; _js_call(listener, self, event); event._passive = False
            if once: self._listeners[event.type] = [x for x in self._listeners.get(event.type, ()) if x[0] is not listener or x[1] != registered_capture]
            if event._immediate:
                return
        handler = self._handlers.get("on" + event.type)
        if not capture and handler not in (None, pyv8.Null, pyv8.Undefined):
            if event.type == "error" and self.__class__.__name__ == "Window":
                _js_call(handler, self, getattr(event, "message", ""), "", 0, 0, getattr(event, "error", pyv8.Null))
            else:
                result = _js_call(handler, self, event)
                if event.type == "beforeunload" and result not in (None, pyv8.Null, pyv8.Undefined, ""):
                    event._return_value = False; event.defaultPrevented = True

    def __getattr__(self, name):
        if name.startswith("on"):
            return self._handlers.get(name, pyv8.Null)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name.startswith("on") and name != "one" and "_handlers" in self.__dict__:
            self._handlers[name] = value
            return
        object.__setattr__(self, name, value)


@interface("Node", "EventTarget")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Node(EventTarget):
    ELEMENT_NODE, ATTRIBUTE_NODE, TEXT_NODE, COMMENT_NODE = 1, 2, 3, 8
    DOCUMENT_NODE, DOCUMENT_TYPE_NODE, DOCUMENT_FRAGMENT_NODE = 9, 10, 11
    DOCUMENT_POSITION_DISCONNECTED, DOCUMENT_POSITION_PRECEDING = 1, 2
    DOCUMENT_POSITION_FOLLOWING, DOCUMENT_POSITION_CONTAINS = 4, 8
    DOCUMENT_POSITION_CONTAINED_BY, DOCUMENT_POSITION_IMPLEMENTATION_SPECIFIC = 16, 32
    __v8_attribute__ = js_attributes(
        ("nodeType", "get_nodeType", None, V8Attribute.DontEnum, 1),
        ("nodeName", "get_nodeName", None, 0, 1),
        ("parentNode", "get_parentNode", None, 0, 1),
        ("parentElement", "get_parentElement", None, 0, 1),
        ("childNodes", "get_childNodes", None, 0, 1),
        ("firstChild", "get_firstChild", None, 0, 1),
        ("lastChild", "get_lastChild", None, 0, 1),
        ("textContent", "get_textContent", "set_textContent", 0, 0),
        ("nodeValue", "get_nodeValue", "set_nodeValue", 0, 0),
    )
    __v8_method__ = js_methods(
        ("appendChild", "fn_appendChild", 1, 0), ("insertBefore", "fn_insertBefore", 2, 0),
        ("removeChild", "fn_removeChild", 1, 0), ("replaceChild", "fn_replaceChild", 2, 0),
        ("cloneNode", "fn_cloneNode", 0, 0), ("contains", "fn_contains", 1, 1),
        ("hasChildNodes", "fn_hasChildNodes", 0, 1), ("isEqualNode", "fn_isEqualNode", 1, 0),
    )

    def __init__(self, page=None, node_type=0, node_name="", value=None, **kw):
        super().__init__(page=page, **kw)
        self.nodeType, self.nodeName, self.nodeValue = node_type, node_name, value
        self.parentNode = None
        self._children = []
        self._child_nodes_view = NodeList(owner=self, getter=lambda: self._children)

    @property
    def parentElement(self):
        return self.parentNode if isinstance(self.parentNode, Element) else None
    @property
    def firstChild(self): return self._children[0] if self._children else pyv8.Null
    @property
    def lastChild(self): return self._children[-1] if self._children else pyv8.Null

    def get_nodeType(self): return self.nodeType
    def get_nodeName(self): return self.nodeName
    def get_parentNode(self): return self.parentNode or pyv8.Null
    def get_parentElement(self): return self.parentElement or pyv8.Null
    def get_childNodes(self): return self._child_nodes_view
    def get_firstChild(self): return self.firstChild
    def get_lastChild(self): return self.lastChild
    def get_textContent(self): return "".join(n.get_textContent() or "" for n in self._children)
    def set_textContent(self, value):
        self._replace_children(())
        if value is not None:
            self.append_child(Text(str(value), page=self._page))
        if self._page is not None and getattr(self, "localName", "") == "script":
            callback = getattr(self._page, "_dynamic_script_updated", None)
            if callback is not None: callback(self)
    def get_nodeValue(self): return self.nodeValue if self.nodeValue is not None else pyv8.Null
    def set_nodeValue(self, value): self.nodeValue = value

    @staticmethod
    def _detach_from_parent(node):
        parent = node.parentNode
        if parent is not None:
            try:
                parent._children.remove(node)
            except ValueError:
                pass
            node.parentNode = None

    def _ensure_adoptable(self, node):
        if not isinstance(node, Node): raise V8TypeError("The new child is not a Node")
        if node is self or self.fn_contains(node): raise HierarchyRequestError("The new child element contains the parent.")

    def _adopt_child(self, node, index=None):
        self._ensure_adoptable(node)
        self._detach_from_parent(node)
        node.parentNode = self
        if index is None:
            self._children.append(node)
        else:
            self._children.insert(index, node)
        page = getattr(self, "_page", None)
        if page is not None and not getattr(page, "_parsing", False):
            callback = getattr(page, "_node_adopted", None)
            if callback is not None: callback(node)
        return node

    def _replace_children(self, nodes):
        for child in self._children:
            child.parentNode = None
        self._children = []
        for node in nodes:
            self._adopt_child(node)

    def append_child(self, node):
        if isinstance(node, DocumentFragment):
            for child in list(node._children):
                self._adopt_child(child)
            return node
        return self._adopt_child(node)
    def fn_appendChild(self, node): return self.append_child(node)
    def fn_insertBefore(self, node, reference):
        if reference in (None, pyv8.Null, pyv8.Undefined): return self.append_child(node)
        if reference not in self._children: raise NotFoundError("The reference node is not a child.")
        index = self._children.index(reference)
        if isinstance(node, DocumentFragment):
            for child in list(node._children):
                self._adopt_child(child, index)
                index += 1
            return node
        if node.parentNode is self and self._children.index(node) < index:
            index -= 1
        return self._adopt_child(node, index)
    def fn_removeChild(self, node):
        if node not in self._children: raise NotFoundError("The node to be removed is not a child.")
        self._children.remove(node); node.parentNode = None; return node
    def fn_replaceChild(self, new, old):
        if not isinstance(new, Node) or not isinstance(old, Node):
            raise V8TypeError("Failed to execute 'replaceChild' on 'Node': parameter is not of type 'Node'.")
        if old not in self._children: raise NotFoundError("The node to be replaced is not a child.")
        i = self._children.index(old)
        self.fn_removeChild(old)
        if isinstance(new, DocumentFragment):
            for child in list(new._children):
                self._adopt_child(child, i)
                i += 1
            return old
        if new.parentNode is self and self._children.index(new) < i:
            i -= 1
        self._adopt_child(new, i)
        return old
    def fn_cloneNode(self, deep=False):
        clone = self._clone_shallow()
        if deep:
            for child in self._children: clone.append_child(child.fn_cloneNode(True))
        return clone
    def _clone_shallow(self): return Node(page=self._page, node_type=self.nodeType, node_name=self.nodeName, value=self.nodeValue)
    def fn_contains(self, other): return other is self or any(c.fn_contains(other) for c in self._children)
    def fn_hasChildNodes(self): return bool(self._children)
    def fn_isEqualNode(self, other): return isinstance(other, Node) and self.outer_html() == other.outer_html()
    def outer_html(self): return self.get_textContent()


@interface("DocumentFragment", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class DocumentFragment(Node):
    def __init__(self, page=None, **kw):
        super().__init__(page=page, node_type=Node.DOCUMENT_FRAGMENT_NODE, node_name="#document-fragment", **kw)
    def _clone_shallow(self): return DocumentFragment(page=self._page)


@interface("Attr", "Node", constructible=False, tag="Attr")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Attr(Node):
    __v8_attribute__ = js_attributes(("name", "get_name", None, 0, 1), ("value", "get_value", "set_value", 0, 0),
                                     ("specified", "get_specified", None, 0, 1))
    def __init__(self, name="", value="", ownerElement=None, page=None, **kw):
        super().__init__(page=page, node_type=Node.ATTRIBUTE_NODE, node_name=str(name), value=str(value), **kw)
        self.ownerElement = ownerElement
    def get_name(self): return self.nodeName
    def get_value(self): return self.nodeValue or ""
    def set_value(self, value): self.nodeValue = str(value)
    def get_specified(self): return True


@interface("NamedNodeMap", None, constructible=False, tag="NamedNodeMap")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class NamedNodeMap(NodeList):
    __v8_method__ = js_methods(("item", "fn_item", 1, 1), ("getNamedItem", "fn_getNamedItem", 1, 1),
                               ("setNamedItem", "fn_setNamedItem", 1, 0), ("removeNamedItem", "fn_removeNamedItem", 1, 0))
    def __init__(self, element=None, **_):
        self._element = element
        super().__init__(getter=lambda: [Attr(k, v, ownerElement=element, page=element._page) for k, v in element.attributes.items()])
    def fn_getNamedItem(self, name): return next((x for x in self._list() if x.get_name() == str(name)), pyv8.Null)
    def fn_setNamedItem(self, attr):
        if not isinstance(attr, Attr): raise V8TypeError("parameter is not of type Attr")
        old = self.fn_getNamedItem(attr.get_name()); self._element.attributes[attr.get_name()] = attr.get_value(); return old
    def fn_removeNamedItem(self, name):
        name = str(name)
        if name not in self._element.attributes: raise NotFoundError("The attribute was not found.")
        return Attr(name, self._element.attributes.pop(name), ownerElement=self._element, page=self._element._page)


@interface("CharacterData", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class CharacterData(Node):
    def __init__(self, data="", page=None, **kw): super().__init__(page=page, node_type=Node.TEXT_NODE, node_name="#text", value=str(data), **kw)
    def get_textContent(self): return self.nodeValue or ""
    def set_textContent(self, value): self.nodeValue = str(value)
    def outer_html(self): return escape(self.nodeValue or "")


@interface("Text", "CharacterData")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Text(CharacterData):
    pass


@interface("Comment", "CharacterData")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Comment(CharacterData):
    def __init__(self, data="", page=None, **kw): super().__init__(data, page=page, **kw); self.nodeType, self.nodeName = Node.COMMENT_NODE, "#comment"
    def outer_html(self): return "<!--" + (self.nodeValue or "") + "-->"


@interface("DocumentType", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class DocumentType(Node):
    def __init__(self, name="html", page=None, **kw):
        super().__init__(page=page, node_type=Node.DOCUMENT_TYPE_NODE, node_name=name, value=None, **kw)
    def outer_html(self): return "<!DOCTYPE " + self.nodeName + ">"


@interface("Element", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Element(Node):
    __v8_attribute__ = js_attributes(
        ("namespaceURI", "get_namespaceURI", None, 0, 1), ("localName", "get_localName", None, 0, 1),
        ("tagName", "get_tagName", None, 0, 1), ("id", "get_id", "set_id", 0, 0),
        ("className", "get_className", "set_className", 0, 0), ("innerHTML", "get_innerHTML", "set_innerHTML", 0, 0),
        ("outerHTML", "get_outerHTML", "set_outerHTML", 0, 0), ("children", "get_children", None, 0, 1),
        ("classList", "get_classList", None, 0, 1), ("attributes", "get_attributes", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("getAttribute", "fn_getAttribute", 1, 1), ("setAttribute", "fn_setAttribute", 2, 0),
        ("removeAttribute", "fn_removeAttribute", 1, 0), ("hasAttribute", "fn_hasAttribute", 1, 1),
        ("querySelector", "fn_querySelector", 1, 1), ("querySelectorAll", "fn_querySelectorAll", 1, 1),
        ("matches", "fn_matches", 1, 1), ("closest", "fn_closest", 1, 1),
    )
    def __init__(self, tag_name="div", page=None, attributes=None, **kw):
        super().__init__(page=page, node_type=Node.ELEMENT_NODE, node_name=tag_name.upper(), **kw)
        self.localName, self.attributes = tag_name.lower(), dict(attributes or {})
    def get_namespaceURI(self): return "http://www.w3.org/1999/xhtml"
    def get_localName(self): return self.localName
    def get_tagName(self): return self.nodeName
    def get_id(self): return self.attributes.get("id", "")
    def set_id(self, value): self.attributes["id"] = str(value)
    def get_className(self): return self.attributes.get("class", "")
    def set_className(self, value): self.attributes["class"] = str(value)
    def get_innerHTML(self): return "".join(c.outer_html() for c in self._children)
    def set_innerHTML(self, value):
        self._replace_children(())
        if self._page: self._page.document._parse_fragment(str(value), self)
    def get_outerHTML(self): return self.outer_html()
    def set_outerHTML(self, value):
        if self.parentNode: self.parentNode.fn_replaceChild(self._page.document._parse_one(str(value)), self)
    def get_children(self): return HTMLCollection(owner=self, getter=lambda: [c for c in self._children if isinstance(c, Element)])
    def get_classList(self):
        value = getattr(self, "_class_list", None)
        if value is None: value = self._class_list = DOMTokenList(self)
        return value
    def get_attributes(self):
        value = getattr(self, "_attributes_view", None)
        if value is None: value = self._attributes_view = NamedNodeMap(self)
        return value
    def fn_getAttribute(self, name): return self.attributes.get(str(name), pyv8.Null)
    def fn_setAttribute(self, name, value):
        import re
        name = str(name)
        if not re.match(r"^[A-Za-z][A-Za-z0-9._:-]*$", name):
            raise InvalidCharacterError("The attribute name provided is invalid.")
        self.attributes[name.lower()] = str(value)
    def fn_removeAttribute(self, name): self.attributes.pop(str(name).lower(), None)
    def fn_hasAttribute(self, name): return str(name).lower() in self.attributes
    def fn_querySelector(self, selector): return self._page.document.query_selector(selector, self) if self._page else pyv8.Null
    def fn_querySelectorAll(self, selector): return self._page.document.query_selector_all(selector, self) if self._page else []
    def fn_matches(self, selector): return self._page.document.matches(self, selector) if self._page else False
    def fn_closest(self, selector):
        n = self
        while isinstance(n, Element):
            if n.fn_matches(selector): return n
            n = n.parentElement
        return pyv8.Null
    def outer_html(self):
        attrs = "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in self.attributes.items())
        return f"<{self.localName}{attrs}>{self.get_innerHTML()}</{self.localName}>"
    def _clone_shallow(self): return type(self)(self.localName, page=self._page, attributes=self.attributes.copy())


@interface("HTMLElement", "Element")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLElement(Element):
    __v8_attribute__ = js_attributes(("title", "get_title", "set_title", 0, 0), ("hidden", "get_hidden", "set_hidden", 0, 0))
    def get_title(self): return self.attributes.get("title", "")
    def set_title(self, v): self.attributes["title"] = str(v)
    def get_hidden(self): return "hidden" in self.attributes
    def set_hidden(self, v):
        if v: self.attributes["hidden"] = ""
        else: self.attributes.pop("hidden", None)


def _html_class(name):
    return interface(name, "HTMLElement")(type(name, (HTMLElement,), {"__module__": __name__}))


HTML_CLASSES = {name: _html_class(name) for name in (
    "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement",
    "HTMLAnchorElement", "HTMLInputElement", "HTMLFormElement",
    "HTMLCanvasElement", "HTMLMediaElement", "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement",
    "HTMLSelectElement", "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement", "HTMLUnknownElement")}


def _resource_attr(name, getter, setter=None):
    return (name, getter, setter, 0, 0)


@interface("HTMLImageElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLImageElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("src", "get_src", "set_src"), _resource_attr("alt", "get_alt", "set_alt"))
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value): self.attributes["src"] = str(value); self._page._discover_element_resource(self) if self._page else None
    def get_alt(self): return self.attributes.get("alt", "")
    def set_alt(self, value): self.attributes["alt"] = str(value)


@interface("HTMLScriptElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLScriptElement(HTMLElement):
    __v8_attribute__ = js_attributes(
        _resource_attr("src", "get_src", "set_src"), _resource_attr("type", "get_type", "set_type"),
        _resource_attr("async", "get_async", "set_async"), _resource_attr("defer", "get_defer", "set_defer"),
    )
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        if self._page and not getattr(self._page, "_parsing", False):
            callback = getattr(self._page, "_dynamic_script_updated", None)
            if callback is not None: callback(self)
    def get_type(self): return self.attributes.get("type", "")
    def set_type(self, value): self.attributes["type"] = str(value)
    def get_async(self): return "async" in self.attributes
    def set_async(self, value):
        if value: self.attributes["async"] = ""
        else: self.attributes.pop("async", None)
    def get_defer(self): return "defer" in self.attributes
    def set_defer(self, value):
        if value: self.attributes["defer"] = ""
        else: self.attributes.pop("defer", None)


@interface("HTMLLinkElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLLinkElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("href", "get_href", "set_href"), _resource_attr("rel", "get_rel", "set_rel"))
    def get_href(self): return self.attributes.get("href", "")
    def set_href(self, value): self.attributes["href"] = str(value)
    def get_rel(self): return self.attributes.get("rel", "")
    def set_rel(self, value): self.attributes["rel"] = str(value)


@interface("HTMLStyleElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLStyleElement(HTMLElement):
    __v8_attribute__ = js_attributes(_resource_attr("media", "get_media", "set_media"))
    def get_media(self): return self.attributes.get("media", "")
    def set_media(self, value): self.attributes["media"] = str(value)


HTML_CLASSES.update({
    "HTMLImageElement": HTMLImageElement, "HTMLScriptElement": HTMLScriptElement,
    "HTMLLinkElement": HTMLLinkElement, "HTMLStyleElement": HTMLStyleElement,
})


@interface("HTMLTemplateElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLTemplateElement(HTMLElement):
    __v8_attribute__ = js_attributes(("content", "get_content", None, 0, 1))
    def __init__(self, tag_name="template", page=None, **kw):
        super().__init__(tag_name, page=page, **kw)
        self.content = DocumentFragment(page=page)
    def get_content(self): return self.content


@interface("HTMLIFrameElement", "HTMLElement")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class HTMLIFrameElement(HTMLElement):
    __v8_attribute__ = js_attributes(("src", "get_src", "set_src", 0, 0),
                                     ("contentDocument", "get_contentDocument", None, 0, 1),
                                     ("contentWindow", "get_contentWindow", None, 0, 1))
    def __init__(self, tag_name="iframe", page=None, **kw):
        super().__init__(tag_name, page=page, **kw); self._child_page = None
    def get_src(self): return self.attributes.get("src", "")
    def set_src(self, value):
        self.attributes["src"] = str(value)
        if self._page and hasattr(self._page, "load_iframe"): self._page.load_iframe(self)
    def get_contentDocument(self): return self._child_page.document if self._child_page else pyv8.Null
    def get_contentWindow(self): return self._child_page.window if self._child_page else pyv8.Null

HTML_CLASSES["HTMLTemplateElement"] = HTMLTemplateElement
HTML_CLASSES["HTMLIFrameElement"] = HTMLIFrameElement
HTML_CLASSES["HTMLIframeElement"] = HTMLIFrameElement


@interface("Document", "Node")
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=0, has_constructor=1)
class Document(Node):
    __v8_attribute__ = js_attributes(
        ("URL", "get_URL", None, 0, 1), ("documentURI", "get_URL", None, 0, 1),
        ("documentElement", "get_documentElement", None, 0, 1), ("body", "get_body", None, 0, 1),
        ("head", "get_head", None, 0, 1), ("defaultView", "get_defaultView", None, 0, 1),
        ("childNodes", "get_childNodes", None, 0, 1), ("readyState", "get_readyState", None, 0, 1),
        ("hidden", "get_hidden", None, 0, 1), ("visibilityState", "get_visibilityState", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("createElement", "fn_createElement", 1, 0), ("createTextNode", "fn_createTextNode", 1, 0),
        ("createComment", "fn_createComment", 1, 0), ("createDocumentFragment", "fn_createDocumentFragment", 0, 0),
        ("createAttribute", "fn_createAttribute", 1, 0), ("getElementById", "fn_getElementById", 1, 1),
        ("getElementsByTagName", "fn_getElementsByTagName", 1, 1), ("getElementsByClassName", "fn_getElementsByClassName", 1, 1),
        ("querySelector", "fn_querySelector", 1, 1),
        ("querySelectorAll", "fn_querySelectorAll", 1, 1),
    )
    def __init__(self, page=None, url="about:blank", ready_state="complete", **kw):
        super().__init__(page=page, node_type=Node.DOCUMENT_NODE, node_name="#document", **kw)
        self.url = url; self._parser = None; self.ready_state = str(ready_state); self.hidden = False
    def get_URL(self): return self.url
    def get_documentElement(self): return next((x for x in self._children if isinstance(x, Element) and x.localName == "html"), pyv8.Null)
    def get_body(self):
        html = self.get_documentElement()
        if html is not pyv8.Null: return html.fn_querySelector("body")
        return next((x for x in self._walk() if isinstance(x, Element) and x.localName == "body"),
                    next((x for x in self._children if isinstance(x, Element)), pyv8.Null))
    def get_head(self):
        html = self.get_documentElement()
        if html is not pyv8.Null: return html.fn_querySelector("head")
        return next((x for x in self._walk() if isinstance(x, Element) and x.localName == "head"), pyv8.Null)
    def get_defaultView(self): return self._page.window if self._page else pyv8.Null
    def get_readyState(self): return self.ready_state
    def get_hidden(self): return self.hidden
    def get_visibilityState(self): return "hidden" if self.hidden else "visible"
    def fn_createElement(self, name): return self._new_element(str(name))
    def fn_createTextNode(self, value): return Text(str(value), page=self._page)
    def fn_createComment(self, value): return Comment(str(value), page=self._page)
    def fn_createDocumentFragment(self): return DocumentFragment(page=self._page)
    def fn_createAttribute(self, name): return Attr(self._validate_name(name), "", page=self._page)
    def fn_getElementById(self, value): return next((x for x in self._walk() if isinstance(x, Element) and x.get_id() == str(value)), pyv8.Null)
    def fn_getElementsByTagName(self, value):
        value = str(value).lower()
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._walk() if isinstance(x, Element) and (value == "*" or x.localName == value)])
    def fn_getElementsByClassName(self, value):
        names = set(str(value).split())
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._walk() if isinstance(x, Element) and names.issubset(set(x.get_className().split()))])
    def fn_querySelector(self, selector): return self.query_selector(selector, self)
    def fn_querySelectorAll(self, selector): return self.query_selector_all(selector, self)
    def _validate_name(self, name):
        import re
        value = str(name)
        if not re.match(r"^[A-Za-z][A-Za-z0-9._:-]*$", value):
            raise InvalidCharacterError("The qualified name provided is invalid.")
        return value
    def _new_element(self, tag):
        tag = self._validate_name(tag)
        return HTML_CLASSES.get("HTML" + tag.capitalize() + "Element", HTML_CLASSES["HTMLUnknownElement"])(tag, page=self._page)
    def _walk(self, root=None):
        root = root or self
        for child in root._children:
            yield child
            if isinstance(child, Node): yield from self._walk(child)
    def query_selector(self, selector, root):
        from .selectors import select
        return next(iter(select(root, selector)), pyv8.Null)
    def query_selector_all(self, selector, root):
        from .selectors import select
        return list(select(root, selector))
    def matches(self, node, selector):
        from .selectors import matches
        return matches(node, selector)
    def _parse_fragment(self, html, parent):
        from .parser import parse_fragment
        parse_fragment(html, self._page, parent)
    def _parse_one(self, html):
        tmp = Document(page=self._page, url=self.url)
        self._parse_fragment(html, tmp)
        return next(iter(tmp._children), Text("", page=self._page))


def serialize(node): return node.outer_html() if isinstance(node, Node) else ""
