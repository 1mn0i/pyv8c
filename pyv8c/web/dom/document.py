"""The ``Document`` interface: collection accessors, factories and parsing hooks."""

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, is_nullish, missing_error, number
from ...foundation.errors import InvalidCharacterError, V8TypeError, dom_exception
from .character_data import Comment, DocumentType, Text
from .collections import HTMLAllCollection, HTMLCollection, NodeList, tag_name_ns_collection
from .css import StyleSheetList
from .html_classes import class_for_tag
from .html_elements import Element
from .mixins import _ParentNodeMixin
from .nodes import Attr, DocumentFragment, Node
from .utils import _VALID_NAME_RE


@interface('Document', 'Node')
class Document(_ParentNodeMixin, Node):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, has_constructor=1,
        attributes=(
            AttributeSpec(name="URL", getter="get_URL", side_effect=1),
            AttributeSpec(name="documentURI", getter="get_URL", side_effect=1),
            AttributeSpec(name="referrer", getter="get_referrer", side_effect=1),
            AttributeSpec(name="compatMode", getter="get_compatMode", side_effect=1),
            AttributeSpec(name="contentType", getter="get_contentType", side_effect=1),
            AttributeSpec(name="dir", getter="get_dir", setter="set_dir"),
            AttributeSpec(name="styleSheets", getter="get_styleSheets", side_effect=1),
            AttributeSpec(name="documentElement", getter="get_documentElement", side_effect=1),
            AttributeSpec(name="scrollingElement", getter="get_scrollingElement", side_effect=1),
            AttributeSpec(name="body", getter="get_body", side_effect=1),
            AttributeSpec(name="head", getter="get_head", side_effect=1),
            AttributeSpec(name="images", getter="get_images", side_effect=1),
            AttributeSpec(name="embeds", getter="get_embeds", side_effect=1),
            AttributeSpec(name="plugins", getter="get_plugins", side_effect=1),
            AttributeSpec(name="links", getter="get_links", side_effect=1),
            AttributeSpec(name="forms", getter="get_forms", side_effect=1),
            AttributeSpec(name="scripts", getter="get_scripts", side_effect=1),
            AttributeSpec(name="defaultView", getter="get_defaultView", side_effect=1),
            AttributeSpec(name="location", getter="get_location", setter="set_location", side_effect=1),
            AttributeSpec(name="childNodes", getter="get_childNodes", side_effect=1),
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="hidden", getter="get_hidden", side_effect=1),
            AttributeSpec(name="visibilityState", getter="get_visibilityState", side_effect=1),
            AttributeSpec(name="cookie", getter="get_cookie", setter="set_cookie"),
            AttributeSpec(name="anchors", getter="get_anchors", side_effect=1),
            AttributeSpec(name="all", getter="get_all", side_effect=1),
            AttributeSpec(name="characterSet", getter="get_characterSet", side_effect=1),
            AttributeSpec(name="charset", getter="get_charset", side_effect=1),
            AttributeSpec(name="title", getter="get_title", setter="set_title"),
            AttributeSpec(name="implementation", getter="get_implementation", side_effect=1),
            AttributeSpec(name="currentScript", getter="get_currentScript", side_effect=1),
            AttributeSpec(name="activeElement", getter="get_activeElement", side_effect=1),
        ),
        methods=(
            MethodSpec(name="createElement", callback="fn_createElement", length=1),
            MethodSpec(name="createTextNode", callback="fn_createTextNode", length=1),
            MethodSpec(name="createComment", callback="fn_createComment", length=1),
            MethodSpec(name="createDocumentFragment", callback="fn_createDocumentFragment"),
            MethodSpec(name="createAttribute", callback="fn_createAttribute", length=1),
            MethodSpec(name="createEvent", callback="fn_createEvent", length=1),
            MethodSpec(name="createExpression", callback="fn_createExpression", length=1),
            MethodSpec(name="evaluate", callback="fn_evaluate", length=2, side_effect=1),
            MethodSpec(name="getElementById", callback="fn_getElementById", length=1, side_effect=1),
            MethodSpec(name="getElementsByName", callback="fn_getElementsByName", length=1, side_effect=1),
            MethodSpec(name="getElementsByTagName", callback="fn_getElementsByTagName", length=1, side_effect=1),
            MethodSpec(name="getElementsByClassName", callback="fn_getElementsByClassName", length=1, side_effect=1),
            MethodSpec(name="getElementsByTagNameNS", callback="fn_getElementsByTagNameNS", length=2, side_effect=1),
            MethodSpec(name="querySelector", callback="fn_querySelector", length=1, side_effect=1),
            MethodSpec(name="querySelectorAll", callback="fn_querySelectorAll", length=1, side_effect=1),
            MethodSpec(name="write", callback="fn_write"),
            MethodSpec(name="writeln", callback="fn_writeln"),
            MethodSpec(name="hasFocus", callback="fn_hasFocus"),
            MethodSpec(name="elementFromPoint", callback="fn_elementFromPoint", length=2, side_effect=1),
            MethodSpec(name="elementsFromPoint", callback="fn_elementsFromPoint", length=2, side_effect=1),
        ),
    )
    def __init__(self, services=None, url="about:blank", ready_state="complete", **kw):
        super().__init__(services=services, node_type=Node.DOCUMENT_NODE, node_name="#document", **kw)
        self.url = url; self._parser = None; self.ready_state = str(ready_state); self.hidden = False
        #: The element focus is on, maintained by :mod:`dom.focus`.
        self._focused_element = None
        #: The script element whose code is running, maintained by the runner.
        self.current_script = None
        #: Bumped when a ``<style>`` changes; layout keys its sheet cache on it.
        self.style_version = 0
    def get_URL(self): return self.url
    def _first_element(self, local_name):
        return next((node for node in self._descendants()
                     if isinstance(node, Element) and node.localName == local_name), None)
    def get_title(self):
        """The first ``title`` element's text, collapsed as the HTML title algorithm
        does: runs of ASCII whitespace fold to single spaces, ends stripped."""
        title = self._first_element("title")
        if title is None:
            return ""
        return " ".join(str(title.get_textContent()).split())
    def set_title(self, value):
        value = dom_string(value)
        title = self._first_element("title")
        if title is None:
            parent = self.get_head()
            if parent is pyv8.Null:
                parent = self.get_documentElement()
            if parent is pyv8.Null:
                return
            title = self._new_element("title")
            parent.append_child(title)
        title.set_textContent(str(value))
    def get_implementation(self):
        """The one ``DOMImplementation`` of this document, like Chromium."""
        implementation = getattr(self, "_implementation", None)
        if implementation is None:
            implementation = self._implementation = DOMImplementation(services=self._services)
        return implementation
    def get_currentScript(self):
        return self.current_script if self.current_script is not None else pyv8.Null
    def get_activeElement(self):
        """The focused element, or the body while nothing was focused -- Chromium
        falls back to the body again once the focused element leaves the document."""
        from .focus import active_element
        return active_element(self)
    def fn_hasFocus(self, *_extra):
        """A page in this DOM is always the focused document: one document per realm
        and no window manager to take focus away."""
        return True
    def fn_elementFromPoint(self, x=MISSING, y=MISSING, *_extra):
        if x is MISSING:
            raise missing_error('Document', 'elementFromPoint', 2, 0, constructor=False)
        if y is MISSING:
            raise missing_error('Document', 'elementFromPoint', 2, 1, constructor=False)
        from .hit_testing import element_from_point
        found = element_from_point(self, number(x), number(y))
        return pyv8.Null if found is None else found
    def fn_elementsFromPoint(self, x=MISSING, y=MISSING, *_extra):
        if x is MISSING:
            raise missing_error('Document', 'elementsFromPoint', 2, 0, constructor=False)
        if y is MISSING:
            raise missing_error('Document', 'elementsFromPoint', 2, 1, constructor=False)
        from .hit_testing import elements_from_point
        return elements_from_point(self, number(x), number(y))
    def get_documentElement(self):
        """The document's first element child, whatever its local name -- not
        necessarily ``html``, since ``createDocument`` builds the requested root."""
        return next((x for x in self._children if isinstance(x, Element)), pyv8.Null)
    def _html_element(self):
        """The ``html`` root, when this document is a full HTML document; a fragment
        has no wrapper, so ``body``/``head`` fall back to searching the tree."""
        return next((x for x in self._children
                     if isinstance(x, Element) and x.localName == "html"), None)
    def get_body(self):
        """The body element, per the HTML algorithm: the ``body`` child of the
        ``html`` root, or for a fragment the first ``body`` (else first element)."""
        html = self._html_element()
        if html is not None:
            body = html.fn_querySelector("body")
            if body is not pyv8.Null:
                return body
        return next((x for x in self._descendants() if isinstance(x, Element) and x.localName == "body"),
                    next((x for x in self._children if isinstance(x, Element)), pyv8.Null))
    def get_head(self):
        html = self._html_element()
        if html is not None:
            head = html.fn_querySelector("head")
            if head is not pyv8.Null:
                return head
        return next((x for x in self._descendants() if isinstance(x, Element) and x.localName == "head"), pyv8.Null)
    def _first_named(self, parent, name):
        return next((child for child in parent._children
                     if isinstance(child, Element) and child.localName == name), None)
    def fn_createExpression(self, expression=MISSING, resolver=None, *_extra):
        """An expression a page compiles once and evaluates against each node."""
        from .xpath import create_expression
        return create_expression(expression)

    def fn_evaluate(self, expression=MISSING, context=MISSING, resolver=None, type=0,
                    result=None, *_extra):
        """The same evaluation in one step, as ``XPathEvaluator`` offers it."""
        from .xpath import create_expression
        if expression is MISSING:
            raise missing_error('Document', 'evaluate', 2, 0, constructor=False)
        if context is MISSING:
            raise missing_error('Document', 'evaluate', 2, 1, constructor=False)
        return create_expression(expression).fn_evaluate(context, type, result)

    def fn_createEvent(self, type=MISSING, *_extra):
        """An uninitialised event of the legacy interface name asked for."""
        if type is MISSING:
            raise missing_error('Document', 'createEvent', 1, 0, constructor=False)
        from .events import CustomEvent, Event, KeyboardEvent, MouseEvent, UIEvent
        kinds = {
            "event": Event, "events": Event, "htmlevents": Event, "uievents": UIEvent,
            "mouseevent": MouseEvent, "mouseevents": MouseEvent,
            "keyboardevent": KeyboardEvent, "keyevents": KeyboardEvent,
            "customevent": CustomEvent,
        }
        kind = kinds.get(dom_string(type).lower())
        if kind is None:
            raise dom_exception(
                "NotSupportedError",
                "Failed to execute 'createEvent' on 'Document': The provided event type"
                f" ('{type}') is invalid.")
        return kind("")

    def get_scrollingElement(self):
        """The element that scrolls the view: the root, as a standards document has it."""
        return self.get_documentElement()

    def get_styleSheets(self):
        view = getattr(self, "_style_sheets", None)
        if view is None:
            view = self._style_sheets = StyleSheetList(self)
        return view

    def get_referrer(self):
        """The page that led here; a navigation nothing linked from reads as empty."""
        return str(getattr(self, "_referrer", "") or "")

    def get_compatMode(self):
        """``CSS1Compat`` under a doctype and ``BackCompat`` without one, as Chrome reads it."""
        return "CSS1Compat" if any(getattr(child, "nodeType", None) == 10
                                   for child in self._children) else "BackCompat"

    def get_contentType(self): return "text/html"

    def get_dir(self):
        html = self.get_documentElement()
        value = str(getattr(html, "attributes", {}).get("dir", "")).lower()
        return value if value in ("ltr", "rtl", "auto") else ""

    def set_dir(self, value):
        html = self.get_documentElement()
        if html is not pyv8.Null:
            html.attributes["dir"] = str(dom_string(value))

    def _browsing_window(self):
        """The window this document is the active document of, or ``None``."""
        services = getattr(self._services, "document_services", None)
        if services is None or getattr(services, "document", None) is not self:
            return None
        return getattr(services, "window", None)
    def get_defaultView(self):
        target = self._browsing_window()
        if target is None:
            return pyv8.Null
        from ..bom.window import Window
        return Window._accessor_window(target)._view(target)
    def get_location(self):
        """The document's ``Location``: the object ``window.location`` is."""
        window = self._browsing_window()
        return pyv8.Null if window is None else window.location
    def set_location(self, value):
        """Assigning to it assigns ``location.href``; without a window it throws."""
        window = self._browsing_window()
        if window is None:
            raise V8TypeError("Failed to set the 'location' property on 'Document': "
                              "The attribute value is not an object")
        window.location.set_href(value)
    def get_readyState(self): return self.ready_state
    def get_hidden(self): return self.hidden
    def get_characterSet(self): return 'UTF-8'
    def get_charset(self): return 'UTF-8'
    def get_visibilityState(self): return "hidden" if self.hidden else "visible"
    def _collect(self, names):
        """A live HTMLCollection over the elements matching one of ``names``."""
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendants() if isinstance(node, Element) and node.localName in names])
    def get_forms(self): return self._collect({"form"})
    def get_images(self): return self._collect({"img"})
    def get_embeds(self):
        """The embed/object collection, cached: ``embeds``/``plugins`` are
        ``[SameObject]`` in Chromium, so both reads share one identity."""
        view = getattr(self, "_embeds_view", None)
        if view is None:
            view = self._embeds_view = self._collect({"embed", "object"})
        return view
    def get_plugins(self): return self.get_embeds()
    def get_links(self):
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendants()
            if isinstance(node, Element) and node.localName in ("a", "area") and "href" in node.attributes])
    def get_scripts(self): return self._collect({"script"})
    def get_anchors(self):
        return HTMLCollection(owner=self, getter=lambda: [
            node for node in self._descendants()
            if isinstance(node, Element) and node.localName == "a" and "name" in node.attributes])
    def _cookie_jar(self):
        return getattr(getattr(self._services, "context_services", None), "cookie_jar", None)
    def get_cookie(self):
        context = self._cookie_jar()
        return context.document_for(self.url) if context is not None else ""
    def set_cookie(self, value):
        value = dom_string(value)
        context = self._cookie_jar()
        if context is not None:
            context.set_from_document(self.url, value)
    def get_all(self):
        # Cached like Chromium: stable identity, element list recomputed each read.
        value = getattr(self, "_all_view", None)
        if value is None:
            self._materialize_interface("HTMLAllCollection")
            value = self._all_view = HTMLAllCollection(owner=self, getter=self._all_elements)
        return value
    def _all_elements(self):
        return [x for x in self._descendants() if isinstance(x, Element)]
    def _materialize_interface(self, name):
        """Build a lazily declared interface before handing out one of its objects;
        ``document.all`` needs the native template of an eagerly-deferred interface."""
        realm = getattr(getattr(self, "_services", None), "realm_services", None)
        if realm is None or realm.interface_materialized(name):
            return
        realm.materialize_interface(name)
    def fn_createElement(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Document', 'createElement', 1, 0, constructor=False)
        name = dom_string(name)
        return self._new_element(str(name))
    def fn_createTextNode(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'createTextNode', 1, 0, constructor=False)
        value = dom_string(value)
        return Text(str(value), services=self._services)
    def fn_createComment(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'createComment', 1, 0, constructor=False)
        value = dom_string(value)
        return Comment(str(value), services=self._services)
    def fn_createDocumentFragment(self, *_extra):
        return DocumentFragment(services=self._services)
    def fn_createAttribute(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Document', 'createAttribute', 1, 0, constructor=False)
        name = dom_string(name)
        return Attr(self._validate_name(name), '', services=self._services)
    def fn_getElementById(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'getElementById', 1, 0, constructor=False)
        value = dom_string(value)
        return next((x for x in self._descendants() if isinstance(x, Element) and x.get_id() == str(value)), pyv8.Null)
    def fn_getElementsByName(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'getElementsByName', 1, 0, constructor=False)
        value = str(dom_string(value))
        return NodeList(owner=self, getter=lambda: [
            node for node in self._descendants()
            if isinstance(node, Element) and node.attributes.get("name") == value])
    def fn_getElementsByTagName(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'getElementsByTagName', 1, 0, constructor=False)
        value = dom_string(value)
        value = str(value).lower()
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._descendants() if isinstance(x, Element) and (value == "*" or x.localName == value)])
    def fn_getElementsByClassName(self, value=MISSING, *_extra):
        if value is MISSING:
            raise missing_error('Document', 'getElementsByClassName', 1, 0, constructor=False)
        value = dom_string(value)
        names = set(str(value).split())
        return HTMLCollection(owner=self, getter=lambda: [x for x in self._descendants() if isinstance(x, Element) and names.issubset(set(x.get_className().split()))])
    def fn_getElementsByTagNameNS(self, namespace=MISSING, local_name=MISSING, *_extra):
        return tag_name_ns_collection(self, 'Document', namespace, local_name)
    def fn_querySelector(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Document', 'querySelector', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self.query_selector(selector, self)
    def fn_querySelectorAll(self, selector=MISSING, *_extra):
        if selector is MISSING:
            raise missing_error('Document', 'querySelectorAll', 1, 0, constructor=False)
        selector = dom_string(selector)
        return self.query_selector_all(selector, self)
    def fn_write(self, *markup):
        return self._write_markup("".join(dom_string(item) for item in markup))
    def fn_writeln(self, *markup):
        return self._write_markup("".join(dom_string(item) for item in markup) + "\n")
    def _write_markup(self, markup):
        """Hand written markup to the document's parser, like ``document.write``.
        A document without services has no parser, so it writes nowhere."""
        services = getattr(self, "_services", None)
        if services is not None:
            services.document_services.write_markup(markup)
        return None
    def _validate_name(self, name):
        value = str(name)
        if not _VALID_NAME_RE.match(value):
            raise InvalidCharacterError("The qualified name provided is invalid.")
        return value
    def _new_element(self, tag):
        tag = self._validate_name(tag)
        return class_for_tag(tag)(tag, services=self._services)
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
        parse_fragment(html, self._services, parent)
    def _parse_one(self, html):
        tmp = Document(services=self._services, url=self.url)
        self._parse_fragment(html, tmp)
        return next(iter(tmp._children), Text("", services=self._services))


@interface('DOMImplementation', None)
class DOMImplementation:
    """``document.implementation``: the document factories.  ``createHTMLDocument``
    builds the skeleton Chromium builds -- ``html`` with ``head``/``body`` and an
    optional ``title``; the others return a document holding the requested root."""

    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="createDocument", callback="fn_createDocument", length=2),
            MethodSpec(name="createDocumentType", callback="fn_createDocumentType", length=3),
            MethodSpec(name="createHTMLDocument", callback="fn_createHTMLDocument"),
            MethodSpec(name="hasFeature", callback="fn_hasFeature"),
        ),
        constructor_behavior=0,
        has_constructor=1,
        to_string_tag="DOMImplementation",
    )

    def __init__(self, services=None, **_):
        self._services = services

    def _document(self):
        return Document(services=self._services, url="about:blank", ready_state="complete")

    def fn_createHTMLDocument(self, title=pyv8.Undefined, *_extra):
        document = self._document()
        root, head, body = (document._new_element(tag) for tag in ("html", "head", "body"))
        document.append_child(root)
        root.append_child(head)
        root.append_child(body)
        if not is_nullish(title):
            element = document._new_element("title")
            head.append_child(element)
            element.set_textContent(str(dom_string(title)))
        return document

    def fn_createDocument(self, namespace=MISSING, qualified_name=MISSING, doctype=None, *_extra):
        if namespace is MISSING:
            raise missing_error('DOMImplementation', 'createDocument', 2, 0, constructor=False)
        if qualified_name is MISSING:
            raise missing_error('DOMImplementation', 'createDocument', 2, 1, constructor=False)
        document = self._document()
        if not is_nullish(doctype):
            if not isinstance(doctype, DocumentType):
                raise V8TypeError("Failed to execute 'createDocument' on 'DOMImplementation': "
                                  "parameter 3 is not of type 'DocumentType'.")
            document.append_child(doctype)
        if not is_nullish(qualified_name) and str(dom_string(qualified_name)):
            document.append_child(document._new_element(str(dom_string(qualified_name))))
        return document

    def fn_createDocumentType(self, qualified_name=MISSING, public_id=MISSING, system_id=MISSING, *_extra):
        if qualified_name is MISSING:
            raise missing_error('DOMImplementation', 'createDocumentType', 3, 0, constructor=False)
        if public_id is MISSING:
            raise missing_error('DOMImplementation', 'createDocumentType', 3, 1, constructor=False)
        if system_id is MISSING:
            raise missing_error('DOMImplementation', 'createDocumentType', 3, 2, constructor=False)
        return DocumentType(str(dom_string(qualified_name)),
                            public_id=str(dom_string(public_id)),
                            system_id=str(dom_string(system_id)),
                            services=self._services)

    def fn_hasFeature(self, *_extra):
        """Removed by the DOM standard but kept by the baseline; Chromium still
        answers ``true`` for every document."""
        return True
