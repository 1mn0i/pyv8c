from html.parser import HTMLParser
from html import unescape
from .dom import Document, Element, Text, Comment, HTML_CLASSES


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


def parse(html, page, document):
    parser = DOMParser(page, document); parser.feed(html); parser.close(); return document


def parse_fragment(html, page, parent):
    parser = DOMParser(page, parent); parser.feed(html); parser.close(); return parent
