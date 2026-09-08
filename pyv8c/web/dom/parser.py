from html.parser import HTMLParser
from html import unescape
import re
from .model import Element, Text, Comment


class _TreeBuilderMixin:
    """Node-tree construction shared by the two DOM parser front-ends.

    :class:`DOMParser` streams tokens out of :mod:`html.parser`, while
    :class:`IncrementalDOMParser` replays a token list so it can suspend and
    resume on a parser-blocking script. Both build the same tree, so the
    shared rules -- auto-closing, attribute normalization, inline handlers,
    raw-text elements and the end-tag algorithm -- live here exactly once.
    Each front-end keeps its own entry points and its own script hook.
    """

    void = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
    auto_close = {"li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "p": {"p", "div", "section", "article", "ul", "ol"}}
    raw_text_tags = {"script", "style", "textarea", "title"}
    # Element classes whose text content is raw text, plus ``title`` which is
    # matched through ``localName`` because it has no dedicated class.
    _raw_text_elements = ("HTMLScriptElement", "HTMLStyleElement", "HTMLTextAreaElement")

    @property
    def current(self): return self.stack[-1]
    @property
    def target(self):
        return self.current.content if self.current.__class__.__name__ == "HTMLTemplateElement" else self.current

    @classmethod
    def _is_raw_text_container(cls, node):
        return (node.__class__.__name__ in cls._raw_text_elements
                or getattr(node, "localName", "") == "title")

    def _pop_auto_closing(self, tag):
        # The parsing-context root (stack[0]) is never auto-closed: when a
        # fragment is being parsed, the root is the innerHTML/outerHTML
        # context element and is already implicitly closed outside the
        # fragment (auto-closing it here would empty the stack).
        if (len(self.stack) > 1 and tag in self.auto_close
                and isinstance(self.current, Element)
                and self.current.localName in self.auto_close[tag]):
            self.stack.pop()

    def _create_element(self, tag, attrs):
        """Build the element for ``tag``, install inline handlers and attach it."""
        node = self.services.document_services.element_class(tag)(
            tag,
            services=self.services,
            attributes={name.lower(): (value or "") for name, value in attrs},
        )
        node._raw_text = ""
        for name, value in attrs:
            name = str(name).lower()
            if name.startswith("on"):
                self.services.document_services.install_inline_handler(node, name, value or "")
        self.target.append_child(node)
        return node

    def _close_element(self, tag):
        """Run the end-tag algorithm for ``tag``; True when a node matched."""
        for index in range(len(self.stack) - 1, 0, -1):
            node = self.stack[index]
            if not isinstance(node, Element) or node.localName != tag:
                continue
            source = getattr(node, "_raw_text", "")
            if tag == "script":
                self.services.document_services.parser_script_complete(node, source)
            if source and (tag == "script" or tag in self.raw_text_tags):
                node.append_child(Text(source, services=self.services))
            del self.stack[index:]
            return True
        return False


class DOMParser(_TreeBuilderMixin, HTMLParser):
    """Streaming parser for ``innerHTML``/``outerHTML`` fragments."""

    def __init__(self, services, root):
        super().__init__(convert_charrefs=False); self.services, self.stack = services, [root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._pop_auto_closing(tag)
        node = self._create_element(tag, attrs)
        if tag not in self.raw_text_tags:
            self.services.resource_services.discover_element_resource(node)
        if tag not in self.void: self.stack.append(node)

    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs); self.handle_endtag(tag)
    def handle_endtag(self, tag): self._close_element(tag.lower())
    def handle_data(self, data):
        if not data: return
        if self._is_raw_text_container(self.current):
            self.current._raw_text = getattr(self.current, "_raw_text", "") + data
            return
        self.target.append_child(Text(data, services=self.services))
    def handle_entityref(self, name): self.handle_data(unescape("&" + name + ";"))
    def handle_charref(self, name): self.handle_data(unescape("&#" + name + ";"))
    def handle_comment(self, data): self.target.append_child(Comment(data, services=self.services))
    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            from .model import DocumentType
            parts = decl.split(None, 1)
            name = parts[1].strip() if len(parts) > 1 else "html"
            self.target.append_child(DocumentType(name=name, services=self.services))


def _iter_tokens(source):
    """Yield coarse HTML tokens while preserving script/style raw text."""
    pos, size, raw = 0, len(source), None
    while pos < size:
        if raw:
            match = re.search(r"</\s*" + re.escape(raw) + r"\s*>", source[pos:], re.I)
            if not match:
                yield ("data", source[pos:]); return
            end = pos + match.start()
            if end > pos: yield ("data", source[pos:end])
            yield ("end", raw); pos += match.end(); raw = None; continue
        if source.startswith("<!--", pos):
            end = source.find("-->", pos + 4)
            if end < 0: yield ("comment", source[pos + 4:]); return
            yield ("comment", source[pos + 4:end]); pos = end + 3; continue
        if source[pos] != "<":
            end = source.find("<", pos)
            end = size if end < 0 else end
            yield ("data", source[pos:end]); pos = end; continue
        if re.match(r"<!doctype\b", source[pos:], re.I):
            end = source.find(">", pos + 2)
            if end < 0: yield ("data", source[pos:]); return
            yield ("decl", source[pos + 2:end]); pos = end + 1; continue
        close = re.match(r"</\s*([A-Za-z][\w:-]*)[^>]*>", source[pos:])
        if close:
            yield ("end", close.group(1)); pos += close.end(); continue
        i, quote = pos + 1, None
        while i < size:
            ch = source[i]
            if quote:
                if ch == quote: quote = None
            elif ch in "\"'": quote = ch
            elif ch == ">": break
            i += 1
        if i >= size: yield ("data", source[pos:]); return
        token = source[pos:i + 1]
        opening = re.match(r"<\s*([A-Za-z][\w:-]*)", token)
        if not opening:
            yield ("data", "<"); pos += 1; continue
        tag = opening.group(1).lower()
        yield ("start", token); pos = i + 1
        if tag in _TreeBuilderMixin.raw_text_tags and not token.rstrip().endswith("/>"): raw = tag


class _StartTagParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False); self.tag = None; self.attrs = []
    def handle_starttag(self, tag, attrs): self.tag, self.attrs = tag.lower(), attrs
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs)


class IncrementalDOMParser(_TreeBuilderMixin):
    """Resumable parser used for parser-blocking script execution."""

    def __init__(self, services, root, source):
        self.services, self.root = services, root
        self.tokens = list(_iter_tokens(str(source)))
        self.index, self.stack, self.done = 0, [root], False

    def _start(self, raw):
        helper = _StartTagParser(); helper.feed(raw); helper.close()
        tag, attrs = helper.tag, helper.attrs
        if not tag: return
        self._pop_auto_closing(tag)
        node = self._create_element(tag, attrs)
        if tag == "script": self.services.document_services.parser_script_start(node)
        elif tag not in self.raw_text_tags: self.services.resource_services.discover_element_resource(node)
        if tag not in self.void and not raw.rstrip().endswith("/>"): self.stack.append(node)

    def _end(self, tag): self._close_element(str(tag).lower())

    def run(self):
        while self.index < len(self.tokens):
            kind, value = self.tokens[self.index]; self.index += 1
            if kind == "start": self._start(value)
            elif kind == "end": self._end(value)
            elif kind == "data":
                if self._is_raw_text_container(self.current): self.current._raw_text = getattr(self.current, "_raw_text", "") + value
                else: self.target.append_child(Text(unescape(value), services=self.services))
            elif kind == "comment": self.target.append_child(Comment(value, services=self.services))
            elif kind == "decl":
                from .model import DocumentType
                parts = value.split(None, 1); self.target.append_child(DocumentType(name=parts[1].strip() if len(parts) > 1 else "html", services=self.services))
            if self.services.document_services.parser_is_blocked(): return False
        self.done = True; return True


def parse(html, services, document):
    parser = DOMParser(services, document); parser.feed(html); parser.close(); return document


def parse_fragment(html, services, parent):
    parser = DOMParser(services, parent); parser.feed(html); parser.close(); return parent
