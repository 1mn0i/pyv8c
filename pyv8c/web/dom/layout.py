"""Small CSS cascade, font measurement and layout facade for DOM geometry."""
import os
import re
from dataclasses import dataclass

try:
    from PIL import ImageFont
except Exception:  # pragma: no cover - optional dependency
    ImageFont = None

from .css import CSSStyleDeclaration
from .model import Element, Text

_INHERITED = {"color", "font-family", "font-size", "font-style", "font-weight", "line-height", "letter-spacing", "word-spacing", "visibility", "white-space"}
_DEFAULTS = {"display": "block", "position": "static", "visibility": "visible", "font-family": "sans-serif", "font-size": "16px", "font-weight": "400", "font-style": "normal", "line-height": "normal", "letter-spacing": "normal", "word-spacing": "normal", "box-sizing": "content-box"}


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
    roots = [os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts"), "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation2"]
    names = []
    for family in families:
        stem = re.sub(r"[^A-Za-z0-9]", "", family).lower()
        names.extend([f for root in roots if os.path.isdir(root) for f in (os.listdir(root)) if stem in re.sub(r"[^A-Za-z0-9]", "", f).lower() and f.lower().endswith((".ttf", ".otf"))])
    names.extend(["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    return [os.path.join(root, name) for root in roots for name in names if os.path.exists(os.path.join(root, name))]


def text_width(text, style):
    size = _px(style.get("font-size", "16px"), 16) or 16
    font = _font(_font_candidates([x.strip().strip('"\'') for x in style.get("font-family", "sans-serif").split(",")]), size, style.get("font-weight", "400"), style.get("font-style") == "italic")
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
    selector = selector.strip()
    if not selector or any(ch in selector for ch in ":[]>"):
        return False
    if selector.startswith("#"):
        return element.attributes.get("id") == selector[1:]
    if selector.startswith("."):
        return selector[1:] in str(element.attributes.get("class", "")).split()
    if "." in selector:
        tag, cls = selector.split(".", 1)
        return (not tag or element.localName == tag.lower()) and cls in str(element.attributes.get("class", "")).split()
    return selector == "*" or element.localName == selector.lower()

def _stylesheet_declarations(element):
    document = getattr(element, "ownerDocument", None)
    if document is None:
        return {}
    result = {}
    for node in document._walk():
        if getattr(node, "localName", "") != "style":
            continue
        source = getattr(node, "_stylesheet_text", "") or getattr(node, "_raw_text", "") or node.get_textContent()
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
            if not any(_selector_matches(element, part) for part in selector.split(",")):
                continue
            for name, value, priority in CSSStyleDeclaration._parse_text(block):
                result[name] = (value, priority)
    return result


def computed_style(element):
    values = dict(_DEFAULTS)
    parent = getattr(element, "parentElement", None)
    if parent is not None:
        values.update({key: computed_style(parent).get(key, default) for key, default in _DEFAULTS.items() if key in _INHERITED})
    for name, (value, priority) in _stylesheet_declarations(element).items():
        values[name] = value
    values.update(_inline_declarations(element))
    return values


@dataclass
class Box:
    width: float = 0
    height: float = 0
    left: float = 0
    top: float = 0


def layout(element, containing_width=None, top=0.0):
    style = computed_style(element)
    if style.get("display") == "none" or not getattr(element, "isConnected", False): return Box()
    if containing_width is None:
        realm = getattr(getattr(element, "_services", None), "realm_services", None)
        containing_width = getattr(getattr(realm, "environment", None), "viewport", None).width if getattr(getattr(realm, "environment", None), "viewport", None) else 1280
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
        boxes = [layout(child, content_width, top) for child in children if isinstance(child, Element)]
        if row:
            height += max((box.height for box in boxes), default=0)
        else:
            height += sum(box.height for box in boxes)
    elif style.get("display") == "grid":
        columns = max(1, len(str(style.get("grid-template-columns", "1fr")).split()))
        boxes = [layout(child, content_width / columns, top) for child in children if isinstance(child, Element)]
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
            child_style = computed_style(child)
            if child_style.get("display", "block") in ("inline", "inline-block"):
                child_box = layout(child, content_width, top + height)
                inline_width += child_box.width; inline_height = max(inline_height, child_box.height)
            else:
                child_box = layout(child, content_width, top + height)
                height += child_box.height
    if inline_height:
        height += inline_height
    explicit_height = style.get("height", "auto")
    if explicit_height != "auto": height = _px(explicit_height, containing_width)
    height += 2 * (padding + border)
    return Box(width=width, height=max(0, height), top=top)


class ComputedStyle(CSSStyleDeclaration):
    def __init__(self, element, values):
        super().__init__(element=element); self._computed_values = values
    def _declarations(self): return list((name, value, "") for name, value in self._computed_values.items() if value not in ("", None))
    def _write(self, declarations): return None


def computed_style_object(element): return ComputedStyle(element, computed_style(element))


__all__ = ["Box", "computed_style", "computed_style_object", "layout"]
