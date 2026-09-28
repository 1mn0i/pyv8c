"""Small CSS cascade, font measurement and layout facade for DOM geometry.

The property tables (initial values, inherited properties) live in :mod:`dom.css`.
"""
import os
import re
from dataclasses import dataclass

try:
    from PIL import ImageFont
except Exception:  # optional dependency  # pragma: no cover
    ImageFont = None

from ...foundation.errors import V8SyntaxError
from .character_data import Text
from .css import (
    INHERITED_PROPERTIES,
    CSSStyleDeclaration,
    initial_values_for,
    parse_declarations,
)
from .html_elements import Element
from .selectors import matches


def _number(value, base=0.0):
    match = re.match(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))", str(value).strip())
    return float(match.group(1)) if match else base


def _px(value, reference=0.0):
    value = str(value).strip().lower()
    if value.endswith("px"): return _number(value)
    if value.endswith("%"): return reference * _number(value) / 100.0
    if value in ("0", "0px"): return 0.0
    return _number(value, 0.0)


def _font(path_candidates, size, weight="400", italic=False):
    if ImageFont is None: return None
    for path in path_candidates:
        try: return ImageFont.truetype(path, max(1, int(round(size))))
        except Exception: pass
    return None


def _font_candidates(families):
    roots = [
        os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"),
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation2",
    ]
    names = []
    for family in families:
        stem = re.sub(r"[^A-Za-z0-9]", "", family).lower()
        names.extend(
            f for root in roots if os.path.isdir(root)
            for f in os.listdir(root)
            if stem in re.sub(r"[^A-Za-z0-9]", "", f).lower() and f.lower().endswith((".ttf", ".otf"))
        )
    names.extend(["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    return [os.path.join(root, name) for root in roots for name in names if os.path.exists(os.path.join(root, name))]


def text_width(text, style):
    size = _px(style.get("font-size", "16px"), 16) or 16
    families = [x.strip().strip('"\'') for x in style.get("font-family", "sans-serif").split(",")]
    font = _font(
        _font_candidates(families), size,
        style.get("font-weight", "400"), style.get("font-style") == "italic",
    )
    if font is not None:
        try: width = font.getlength(text)
        except AttributeError: width = font.getbbox(text)[2]
    else:
        width = sum(size * (0.34 if ch in "ilI.,' !" else 0.56 if ord(ch) < 128 else 1.0) for ch in text)
    if style.get("letter-spacing", "normal") != "normal": width += max(0, len(text) - 1) * _px(style["letter-spacing"])
    return width


def _inline_declarations(element):
    return {name: value for name, value, _ in CSSStyleDeclaration(element=element)._declarations()}

def _selector_matches(element, selector):
    """Match one stylesheet selector; unsupported syntax simply never matches."""
    try:
        return matches(element, selector)
    except V8SyntaxError:
        return False

def document_rules(document):
    """The document's ``<style>`` rules as ``(selectors, declarations)`` pairs.

    Reading the sheet walks the document and every layout query re-reads it, so
    the result is cached on the document, keyed on ``style_version`` (bumped
    whenever a ``<style>`` is added, removed or edited).
    """
    if document is None:
        return ()
    version = getattr(document, "style_version", 0)
    cached = getattr(document, "_rules_cache", None)
    if cached is not None and cached[0] == version:
        return cached[1]
    rules = _parse_style_elements(document)
    document._rules_cache = (version, rules)
    return rules


def _parse_style_elements(document):
    rules = []
    for node in document._descendants():
        if getattr(node, "localName", "") != "style":
            continue
        # The element's text is authoritative; ``_raw_text`` only covers a style
        # the parser never closed (see ``parser._close_element``).
        source = node.get_textContent() or getattr(node, "_raw_text", "")
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
            rules.append((selector.split(","), parse_declarations(block)))
    return tuple(rules)


def _cascade(element, rules):
    values = initial_values_for(element)
    parent = getattr(element, "parentElement", None)
    if parent is not None:
        inherited = _cascade(parent, rules)
        # Inherit every inherited property, not only those with an initial value here.
        values.update({
            name: inherited.get(name, values.get(name, ""))
            for name in INHERITED_PROPERTIES
        })
    for selectors, declarations in rules:
        if not any(_selector_matches(element, part) for part in selectors):
            continue
        for name, value, _priority in declarations:
            values[name] = value
    values.update(_inline_declarations(element))
    return values


def computed_style(element):
    return _cascade(element, document_rules(getattr(element, "ownerDocument", None)))


@dataclass
class Box:
    width: float = 0
    height: float = 0
    left: float = 0
    top: float = 0


def layout(element, containing_width=None, top=0.0, rules=None, style=None, collected=None):
    """The box *element* measures, laying out its children to find its height.

    The offset travels down the recursion, so a nested element asked for on its
    own starts at ``top`` 0; callers needing real positions pass *collected*, a
    mapping the walk fills with every box it builds (see :func:`positioned_box`).
    """
    if rules is None:
        rules = document_rules(getattr(element, "ownerDocument", None))
    if style is None:
        style = _cascade(element, rules)
    if style.get("display") == "none" or not getattr(element, "isConnected", False):
        box = Box()
        if collected is not None:
            collected[element] = box
        return box

    def record(box):
        if collected is not None:
            collected[element] = box
        return box

    if containing_width is None:
        realm = getattr(getattr(element, "_services", None), "realm_services", None)
        viewport = getattr(getattr(realm, "environment", None), "viewport", None)
        containing_width = viewport.width if viewport else 1280
    padding = _px(style.get("padding", "0"), containing_width)
    border = _px(style.get("border-width", "0"), containing_width)
    width_value = style.get("width", "auto")
    width = containing_width if width_value == "auto" else _px(width_value, containing_width)
    width = max(0, width)
    content_width = max(0, width - 2 * (padding + border))
    children = [child for child in getattr(element, "_children", ()) if isinstance(child, (Element, Text))]
    height = 0.0
    inline_width = 0.0
    inline_height = 0.0
    if style.get("display") == "flex":
        row = style.get("flex-direction", "row") not in ("column", "column-reverse")
        boxes = [layout(child, content_width, top, rules, collected=collected)
                 for child in children if isinstance(child, Element)]
        if row:
            height += max((box.height for box in boxes), default=0)
        else:
            height += sum(box.height for box in boxes)
    elif style.get("display") == "grid":
        columns = max(1, len(str(style.get("grid-template-columns", "1fr")).split()))
        boxes = [layout(child, content_width / columns, top, rules, collected=collected)
                 for child in children if isinstance(child, Element)]
        for index in range(0, len(boxes), columns):
            height += max((box.height for box in boxes[index:index + columns]), default=0)
    else:
        for child in children:
            if isinstance(child, Text):
                text = child.get_textContent() or ""
                inline_width += text_width(text, style)
                line = _px(style.get("line-height", "normal"), 0) or _px(style.get("font-size", "16px"), 16) * 1.2
                inline_height = max(inline_height, line)
            else:
                child_style = _cascade(child, rules)
                child_box = layout(child, content_width, top + height, rules, child_style,
                                   collected=collected)
                if child_style.get("display", "block") in ("inline", "inline-block"):
                    inline_width += child_box.width; inline_height = max(inline_height, child_box.height)
                else:
                    height += child_box.height
    if inline_height:
        height += inline_height
    explicit_height = style.get("height", "auto")
    if explicit_height != "auto": height = _px(explicit_height, containing_width)
    height += 2 * (padding + border)
    return record(Box(width=width, height=max(0, height), top=top))


def positioned_box(element):
    """*element*'s box with the offset its ancestors put it at.

    Laying the document out once and reading the element's box from that pass is
    what gives ``getBoundingClientRect`` -- and the hit test that must agree with
    it -- viewport coordinates, as Chromium reports them.
    """
    document = getattr(element, "ownerDocument", None)
    if document is None:
        return Box()
    collected = {}
    root = document.get_documentElement()
    if isinstance(root, Element):
        layout(root, collected=collected)
    return collected.get(element, Box())


class ComputedStyle(CSSStyleDeclaration):
    def __init__(self, element, values):
        super().__init__(element=element); self._computed_values = values
    def _declarations(self):
        return [(name, value, "") for name, value in self._computed_values.items() if value not in ("", None)]
    def _write(self, declarations): return None


def computed_style_object(element): return ComputedStyle(element, computed_style(element))


__all__ = [
    "Box", "computed_style", "computed_style_object", "document_rules", "layout",
    "positioned_box",
]
