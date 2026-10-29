"""Stylesheet collection, cascade and computed CSS values."""
import math
import re
from functools import lru_cache
from ...foundation.environment import environment_of
from ...foundation.font_sizes import font_size_px as _font_size_px, px_text as _px_text
from ...foundation.system_fonts import normalize
from ..page_errors import PageSyntaxError
from ..dom.utils import MATHML_NAMESPACE
from .cssom import CSSStyleDeclaration
from .properties import INHERITED_PROPERTIES, initial_values_for
from .colors import SYSTEM_COLORS
from .parser import _box_values, parse_declarations, valid_color
from .stylesheet import stylesheet_source
from .colors import NAMED_COLORS
from .selectors import matches
from .values import resolve as resolve_computed_values, length_math, absolute_length_px


def _environment_for(element):
    return environment_of(getattr(element, "_services", None))


def _number(value, base=0.0):
    match = re.match(r"^(-?(?:\d+(?:\.\d*)?|\.\d+))", str(value).strip())
    return float(match.group(1)) if match else base


@lru_cache(maxsize=4096)
def _px(value, reference=0.0):
    value = str(value).strip().lower()
    if value.startswith(("calc(", "min(", "max(", "clamp(")):
        computed = length_math(value, reference)
        if computed is not None:
            return computed
    if value.endswith("px"): return math.trunc(_number(value) * 64) / 64
    if value.endswith("%"): return reference * _number(value) / 100.0
    if value in ("0", "0px"): return 0.0
    absolute = absolute_length_px(value)
    if absolute is not None:
        return math.trunc(absolute * 64) / 64
    return _number(value, 0.0)


def _root_font_size(element, cache=None):
    """The root element's computed size, which is what ``rem`` is relative to."""
    document = getattr(element, "ownerDocument", None)
    get_root = getattr(document, "get_documentElement", None) if document is not None else None
    root = get_root() if get_root is not None else None
    if root is None or root is element:
        return 16.0
    if cache is not None and root in cache:
        return _px(cache[root].get("font-size", "16px"), 16) or 16
    return _px(computed_style(root).get("font-size", "16px"), 16) or 16

def _inline_declarations(element):
    text = str(element.attributes.get("style", ""))
    declarations = _parsed_inline(text)
    precise = getattr(element, '_inline_math_values', None)
    if precise is None or precise[0] != text or not precise[1]:
        return declarations
    return tuple((name, f'calc({precise[1][name][1]:.17g}px)', priority)
                 if name in precise[1] and value == precise[1][name][0]
                 else (name, value, priority)
                 for name, value, priority in declarations)


@lru_cache(maxsize=2048)
def _parsed_inline(raw):
    return tuple(parse_declarations(raw))


#: How CSS counts a selector: ids, then classes and attributes, then types.
_SPECIFICITY_PATTERNS = (
    (0, re.compile(r"#[\w-]+")),
    (1, re.compile(r"\.[\w-]+|\[[^\]]*\]|::?[\w-]+(?:\([^)]*\))?")),
    (2, re.compile(r"(?<![\w.#\[:-])[A-Za-z][\w-]*")),
)


def _specificity(selector):
    """The three numbers CSS orders two rules by when both match."""
    selector = re.sub(r":not\(([^)]*)\)", r"\1", str(selector))
    counts = [0, 0, 0]
    for slot, pattern in _SPECIFICITY_PATTERNS:
        counts[slot] = len(pattern.findall(selector))
    return tuple(counts)


def _cascade(element, rules, cache=None):
    if cache is not None and element in cache:
        return cache[element]
    values = initial_values_for(element)
    parent = getattr(element, "parentElement", None)
    if parent is None:
        root = element.root_node()
        parent = getattr(root, '_host', None)
    if parent is None:
        environment = _environment_for(element)
        family = getattr(getattr(environment, 'fonts', None), 'default_family', None)
        if family:
            values['font-family'] = f'"{family}"' if ' ' in family else family
    parent_size = 16.0
    if parent is not None:
        parent_rules = rules if parent.root_node() is element.root_node() else rules_for(parent)
        inherited = _cascade(parent, parent_rules, cache)
        # Inherit every inherited property, not only those with an initial value here.
        values.update({
            name: inherited.get(name, values.get(name, ""))
            for name in INHERITED_PROPERTIES
        })
        values.update({name: value for name, value in inherited.items() if name.startswith("--")})
        parent_size = _px(inherited.get("font-size", "16px"), 16) or 16
    if str(getattr(element, 'localName', '')).lower() == 'input':
        input_type = getattr(element, 'get_type', lambda: '')()
        if input_type in ('button', 'submit', 'reset', 'color', 'checkbox', 'radio', 'range', 'file',
                          'text', 'search', 'password', 'tel', 'email', 'url', 'time'):
            values.update({'font-family': 'Arial', 'font-size': '13.3333px',
                           'box-sizing': 'border-box'})
            if input_type in ('button', 'submit', 'reset'):
                values.update({'padding': '1px 6px', 'border-width': '2px'})
            elif input_type == 'color':
                values.update({'padding': '1px 2px', 'border-width': '1px'})
            elif input_type in ('text', 'search', 'password', 'tel', 'email', 'url'):
                values.update({'padding': '1px 2px', 'border-width': '2px'})
            elif input_type == 'time':
                values.update({'font-family': 'monospace', 'padding': '0px',
                               'padding-left': '1px', 'border-width': '2px'})
            else:
                values.update({'padding': '0px', 'border-width': '0px'})
    # MathML's presentational hint has lower priority than author CSS.
    math_color = (element.attributes.get('mathcolor')
                  if getattr(element, '_namespace', None) == MATHML_NAMESPACE else None)
    if math_color and valid_color(math_color):
        values['color'] = str(math_color).strip().lower()
    # Last applied wins, so the order is CSS's: important, inline, specificity, source.
    matched = []
    order = 0
    for selectors, declarations, counts in rules:
        best = None
        for part, count in zip(selectors, counts):
            if (best is None or count > best) and _selector_matches(element, part):
                best = count
        if best is None:
            continue
        for name, value, priority in declarations:
            matched.append(((priority == "important"), False, best, order, name, value))
            order += 1
    for name, value, priority in _inline_declarations(element):
        matched.append(((priority == "important"), True, (0, 0, 0), order, name, value))
        order += 1
    for _important, _inline, _count, _order, name, value in sorted(matched, key=lambda item: item[:4]):
        values[name] = value
        if name in ('margin', 'padding'):
            parts = _box_values(value)
            if parts is not None:
                for side, part in zip(('top', 'right', 'bottom', 'left'), parts):
                    values[name + '-' + side] = part
    # Chrome's default fixed font is 13px, even when the surrounding text is 16px.
    # An explicit font-size declaration still wins over this UA default.
    changed = {item[-2] for item in matched}
    def inherited_size_declared(node):
        while node is not None:
            if any(name in ('font-size', 'font') for name, _, _ in _inline_declarations(node)):
                return True
            for selectors, declarations, _counts in rules:
                if (any(name in ('font-size', 'font') for name, _, _ in declarations)
                        and any(_selector_matches(node, selector) for selector in selectors)):
                    return True
            node = getattr(node, 'parentElement', None)
        return False

    if ("font-family" in changed and "font-size" not in changed
            and normalize(values.get("font-family", "")) in ("monospace", "ui-monospace")
            and (parent is None or normalize(inherited.get("font-family", ""))
                 not in ("monospace", "ui-monospace"))
            and not inherited_size_declared(parent)):
        values["font-size"] = "13px"
    # A computed size is absolute in every browser, so a relative one is resolved here,
    # where the parent's own computed size -- and the root's -- are at hand.
    # Initial values and inherited computed values are already resolved. Only
    # declarations applied here need conversion, with the full map as var input.
    if matched:
        values = resolve_computed_values(values, changed)
    for name in ("color", "background-color", "border-top-color", "border-right-color",
                 "border-bottom-color", "border-left-color", "outline-color", "caret-color"):
        value = values.get(name)
        if isinstance(value, str) and value.lower() in SYSTEM_COLORS:
            values[name] = SYSTEM_COLORS[value.lower()]
        elif isinstance(value, str) and value.lower() in NAMED_COLORS:
            color = NAMED_COLORS[value.lower()]
            alpha = (color >> 24) & 255
            if alpha == 0:
                values[name] = 'rgba(0, 0, 0, 0)'
            elif alpha == 255:
                values[name] = f'rgb({(color >> 16) & 255}, {(color >> 8) & 255}, {color & 255})'
    values["font-size"] = _px_text(_font_size_px(values.get("font-size", "16px"),
                                                 parent_size, _root_font_size(element, cache)))
    if cache is not None:
        cache[element] = values
    return values


def _selector_matches(element, selector):
    """Match one stylesheet selector; unsupported syntax simply never matches."""
    try:
        return matches(element, selector)
    except PageSyntaxError:
        return False


def document_rules(document):
    """The document's ``<style>`` rules, cached on ``style_version`` as
    ``(selectors, declarations, counts)`` -- the specificity counted once, not per node."""
    if document is None:
        return ()
    version = getattr(document, "style_version", 0)
    cached = getattr(document, "_rules_cache", None)
    if cached is not None and cached[0] == version:
        return cached[1]
    rules = _parse_style_elements(document)
    document._rules_cache = (version, rules)
    return rules


def rules_for(element, fallback=None):
    """Style rules for the element's tree, without crossing a shadow boundary."""
    root = element.root_node()
    if getattr(root, '_host', None) is not None:
        document = getattr(element, 'ownerDocument', None)
        version = getattr(document, 'style_version', 0)
        cached = getattr(root, '_rules_cache', None)
        if cached is None or cached[0] != version:
            cached = (version, _parse_style_elements(root))
            root._rules_cache = cached
        return cached[1]
    return fallback if fallback is not None else document_rules(getattr(element, 'ownerDocument', None))


def _parse_style_elements(document):
    rules = []
    for node in document._descendants():
        source = stylesheet_source(node)
        if not source:
            continue
        for selector, block in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
            parts = selector.split(",")
            rules.append((parts, parse_declarations(block),
                          tuple(_specificity(part) for part in parts)))
    return tuple(rules)


def computed_style(element):
    return _cascade(element, rules_for(element))


def computed_style_object(element, *, rendered=True, layout_snapshot):
    """The cascade as a declaration block: the same interface, and nothing may write it back."""
    if not rendered:
        return CSSStyleDeclaration(element=element, computed={})
    document = getattr(element, 'ownerDocument', None)
    boxes, styles = layout_snapshot(document) if document is not None else ({}, {})
    values = dict(styles.get(element) or computed_style(element))
    box = boxes.get(element)
    if values.get('display') == 'inline':
        values.setdefault('width', 'auto')
        values.setdefault('height', 'auto')
    if (box is not None and element.isConnected and values.get('display') != 'none'
            and (values.get('display') != 'inline'
                 or str(getattr(element, 'localName', '')).lower() in
                 ('img', 'canvas', 'iframe', 'video', 'audio', 'input', 'object', 'embed'))):
        for name, extent, sides in (
            ('width', box.width, ('left', 'right')),
            ('height', box.height, ('top', 'bottom')),
        ):
            if values.get('box-sizing', 'content-box') != 'border-box':
                extent -= sum(box.padding.get(side, 0) + box.border.get(side, 0)
                              for side in sides)
            values[name] = f'{max(0.0, extent):.12g}px'
    return CSSStyleDeclaration(element=element, computed=values)

