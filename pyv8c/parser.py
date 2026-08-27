from html.parser import HTMLParser
from html import unescape
import re
from .dom import Document, Element, Text, Comment


class DOMParser(HTMLParser):
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    auto_close = {"li": {"li"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"}, "p": {"p", "div", "section", "article", "ul", "ol"}}
    raw_text_tags = {"script", "style", "textarea", "title"}
    def __init__(self, page, root):
        super().__init__(convert_charrefs=False); self.page, self.stack = page, [root]
    @property
    def current(self): return self.stack[-1]
    @property
    def target(self):
        return self.current.content if self.current.__class__.__name__ == "HTMLTemplateElement" else self.current
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.auto_close and isinstance(self.current, Element) and self.current.localName in self.auto_close[tag]: self.stack.pop()
        cls = self.page.element_class(tag)
        attributes = {k.lower(): (v or "") for k, v in attrs}
        node = cls(tag, page=self.page, attributes=attributes); node._raw_text = ""
        for name, value in attrs:
            if str(name).lower().startswith("on"):
                self.page.install_inline_handler(node, str(name).lower(), value or "")
        self.target.append_child(node)
        if tag not in ("script", "style", "textarea", "title"):
            self.page._discover_element_resource(node)
        if tag not in self.void: self.stack.append(node)
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs); self.handle_endtag(tag)
    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if isinstance(self.stack[i], Element) and self.stack[i].localName == tag:
                node = self.stack[i]
                if tag == "script":
                    self.page._parser_script_complete(node, getattr(node, "_raw_text", ""))
                    if getattr(node, "_raw_text", ""):
                        node.append_child(Text(node._raw_text, page=self.page))
                elif tag in self.raw_text_tags and getattr(node, "_raw_text", ""):
                    node.append_child(Text(node._raw_text, page=self.page))
                del self.stack[i:]; return
    def handle_data(self, data):
        if not data: return
        if self.current.__class__.__name__ in ("HTMLScriptElement", "HTMLStyleElement", "HTMLTextAreaElement") or getattr(self.current, "localName", "") == "title":
            self.current._raw_text = getattr(self.current, "_raw_text", "") + data
            return
        self.target.append_child(Text(data, page=self.page))
    def handle_entityref(self, name): self.handle_data(unescape("&" + name + ";"))
    def handle_charref(self, name): self.handle_data(unescape("&#" + name + ";"))
    def handle_comment(self, data): self.target.append_child(Comment(data, page=self.page))
    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            from .dom import DocumentType
            name = decl.split(None, 1)[1].strip() if len(decl.split(None, 1)) > 1 else "html"
            self.target.append_child(DocumentType(name=name, page=self.page))


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
        if tag in DOMParser.raw_text_tags and not token.rstrip().endswith("/>"): raw = tag


class _StartTagParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False); self.tag = None; self.attrs = []
    def handle_starttag(self, tag, attrs): self.tag, self.attrs = tag.lower(), attrs
    def handle_startendtag(self, tag, attrs): self.handle_starttag(tag, attrs)


class IncrementalDOMParser:
    """Resumable parser used for parser-blocking script execution."""
    void = DOMParser.void; auto_close = DOMParser.auto_close; raw_text_tags = DOMParser.raw_text_tags

    def __init__(self, page, root, source):
        self.page, self.root = page, root
        self.tokens = list(_iter_tokens(str(source)))
        self.index, self.stack, self.done = 0, [root], False

    @property
    def current(self): return self.stack[-1]
    @property
    def target(self): return self.current.content if self.current.__class__.__name__ == "HTMLTemplateElement" else self.current

    def _start(self, raw):
        helper = _StartTagParser(); helper.feed(raw); helper.close()
        tag, attrs = helper.tag, helper.attrs
        if not tag: return
        if tag in self.auto_close and isinstance(self.current, Element) and self.current.localName in self.auto_close[tag]: self.stack.pop()
        node = self.page.element_class(tag)(tag, page=self.page, attributes={k.lower(): (v or "") for k, v in attrs})
        node._raw_text = ""
        for name, value in attrs:
            if str(name).lower().startswith("on"): self.page.install_inline_handler(node, str(name).lower(), value or "")
        self.target.append_child(node)
        if tag == "script": self.page._parser_script_start(node)
        elif tag not in self.raw_text_tags: self.page._discover_element_resource(node)
        if tag not in self.void and not raw.rstrip().endswith("/>"): self.stack.append(node)

    def _end(self, tag):
        tag = str(tag).lower()
        for i in range(len(self.stack) - 1, 0, -1):
            node = self.stack[i]
            if isinstance(node, Element) and node.localName == tag:
                if tag == "script":
                    source = getattr(node, "_raw_text", "")
                    self.page._parser_script_complete(node, source)
                    if source: node.append_child(Text(source, page=self.page))
                elif tag in self.raw_text_tags:
                    source = getattr(node, "_raw_text", "")
                    if source: node.append_child(Text(source, page=self.page))
                del self.stack[i:]; return

    def run(self):
        while self.index < len(self.tokens):
            kind, value = self.tokens[self.index]; self.index += 1
            if kind == "start": self._start(value)
            elif kind == "end": self._end(value)
            elif kind == "data":
                if self.current.__class__.__name__ in ("HTMLScriptElement", "HTMLStyleElement", "HTMLTextAreaElement") or getattr(self.current, "localName", "") == "title": self.current._raw_text = getattr(self.current, "_raw_text", "") + value
                else: self.target.append_child(Text(unescape(value), page=self.page))
            elif kind == "comment": self.target.append_child(Comment(value, page=self.page))
            elif kind == "decl":
                from .dom import DocumentType
                parts = value.split(None, 1); self.target.append_child(DocumentType(name=parts[1].strip() if len(parts) > 1 else "html", page=self.page))
            if self.page._parser_is_blocked(): return False
        self.done = True; return True


def parse(html, page, document):
    parser = DOMParser(page, document); parser.feed(html); parser.close(); return document


def parse_fragment(html, page, parent):
    parser = DOMParser(page, parent); parser.feed(html); parser.close(); return parent
