"""Small CSS cascade, font measurement and layout facade for DOM geometry.

The property tables (initial values, inherited properties) live in :mod:`dom.css`.

Boxes are the *border* boxes in viewport coordinates, which is what
``getBoundingClientRect`` reports and what the hit test walks.  Margins place a box
inside its containing block, ``position: relative`` slides it afterwards, and
``position: absolute`` takes it out of flow and measures it against the padding box
of the nearest positioned ancestor.
"""
import re
from dataclasses import dataclass, field

from ...foundation import text as text_engine
from ...foundation.environment import environment_of
from ...foundation.font_sizes import (
    font_size_px as _font_size_px, font_size_term as _font_size_term, px_text as _px_text,
)
from ...foundation.system_fonts import file_for, normalize, table_of
from ..page_errors import PageSyntaxError
from .character_data import Text
from .css import (
    INHERITED_PROPERTIES,
    CSSStyleDeclaration,
    initial_values_for,
    parse_declarations,
    stylesheet_source,
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


def _root_font_size(element):
    """The root element's computed size, which is what ``rem`` is relative to."""
    document = getattr(element, "ownerDocument", None)
    get_root = getattr(document, "get_documentElement", None) if document is not None else None
    root = get_root() if get_root is not None else None
    if root is None or root is element:
        return 16.0
    return _px(computed_style(root).get("font-size", "16px"), 16) or 16

#: What CSS's generic families mean here: the platform's own default for each, in the
#: order a name would be tried, because a page asking for ``sans-serif`` is asking for
#: whatever this browser would have used.
_GENERIC_FAMILIES = {
    "serif": ("Times New Roman", "Times", "Liberation Serif", "DejaVu Serif"),
    "sans-serif": ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"),
    "monospace": ("Consolas", "Courier New", "Liberation Mono", "DejaVu Sans Mono"),
    "cursive": ("Comic Sans MS", "Apple Chancery", "DejaVu Sans"),
    "fantasy": ("Impact", "Papyrus", "DejaVu Sans"),
    "system-ui": ("Segoe UI", "Helvetica Neue", "DejaVu Sans"),
    "ui-serif": ("Times New Roman", "Liberation Serif", "DejaVu Serif"),
    "ui-sans-serif": ("Arial", "Liberation Sans", "DejaVu Sans"),
    "ui-monospace": ("Consolas", "Liberation Mono", "DejaVu Sans Mono"),
    "ui-rounded": ("Segoe UI", "DejaVu Sans"),
    "emoji": ("Segoe UI Emoji", "Apple Color Emoji"),
    "math": ("Cambria Math", "DejaVu Sans"),
    "fangsong": ("FangSong", "DejaVu Serif"),
}

#: What a name no face and no table knows falls back to: CSS's standard font, which is the
#: serif family and not the sans-serif one -- Chrome measures such a family with Times.
_DEFAULT_FAMILIES = _GENERIC_FAMILIES["serif"]

#: The families a character the page's own ones do not have is drawn by, in the order this
#: platform would look for them.  A CJK character in an Arial run is the case that matters:
#: without these the character would be measured as a missing-glyph box.
_FALLBACK_FAMILIES = (
    "Microsoft YaHei", "Malgun Gothic", "Meiryo", "SimSun", "MS Gothic",
    "Noto Sans CJK SC", "PingFang SC", "Hiragino Sans", "Apple SD Gothic Neo",
    "DejaVu Sans", "Segoe UI Symbol",
)

#: What ``line-height: normal`` falls back to when no face can be read, and the weight CSS
#: starts calling bold at.
_LINE_HEIGHT_FALLBACK = 1.2
_BOLD_WEIGHT = 600


def _environment_for(element):
    """The environment a box's text is measured in, which a profile may have set."""
    return environment_of(getattr(element, "_services", None))


def _families(style):
    """The family list a style asks for, with any quoting taken off."""
    return [name.strip().strip('"\'')
            for name in str(style.get("font-family", "sans-serif")).split(",")]


def _wants_bold(weight):
    """Whether a ``font-weight`` asks for a bold face."""
    text = str(weight).strip().lower()
    if text in ("bold", "bolder"):
        return True
    if text in ("normal", "lighter", ""):
        return False
    return _number(text, 400.0) >= _BOLD_WEIGHT


def _face_in(table, family, weight, style):
    """A family's face at one weight and slant, chosen by subfamily; a match beats none."""
    key = normalize(family)
    faces = [record for record in table if normalize(record.family) == key]
    if not faces:
        return None
    slant_wanted = str(style).strip().lower() in ("italic", "oblique")
    bold_wanted = _wants_bold(weight)

    def score(record):
        name = normalize(record.style)
        slant = "italic" in name or "oblique" in name
        heavy = any(word in name for word in ("bold", "black", "heavy"))
        return ((slant == slant_wanted) + (heavy == bold_wanted), len(name))

    return max(faces, key=score)


def _face_for(families, environment=None, weight="400", style="normal"):
    """The face a family list resolves to: the first family known here, then the default.

    It is the table ``FontFace`` answers ``local()`` from, so "the page said this font is
    here" and "measuring text works" stay in step.
    """
    table = table_of(environment)
    for family in families:
        for name in _GENERIC_FAMILIES.get(normalize(family), (family,)):
            record = _face_in(table, name, weight, style)
            if record is not None:
                return record
    for name in _DEFAULT_FAMILIES:
        record = _face_in(table, name, weight, style)
        if record is not None:
            return record
    return None


def _font_size(style):
    """The size a style asks for, absolute by the time the cascade is through with it."""
    return _px(style.get("font-size", "16px"), 16) or 16


def _face_path(record):
    """The file a record's face lives in here, or ``None`` when this machine lacks it."""
    return None if record is None else file_for(record)


def _face_chain(families, environment, weight, style):
    """The faces a family list names, then this platform's fallbacks, in tried order."""
    table = table_of(environment)
    chain = []
    for family in families:
        for name in _GENERIC_FAMILIES.get(normalize(family), (family,)):
            record = _face_in(table, name, weight, style)
            if record is not None and record not in chain:
                chain.append(record)
    for name in _DEFAULT_FAMILIES + _FALLBACK_FAMILIES:
        record = _face_in(table, name, weight, style)
        if record is not None and record not in chain:
            chain.append(record)
    return chain


def _shaped(text, style, environment=None):
    """``(run, record)`` for one string; ``None`` when no face here has a file for it."""
    chain = _face_chain(_families(style), environment, style.get("font-weight", "400"),
                        style.get("font-style", "normal"))
    faces = []
    for record in chain:
        path = _face_path(record)
        if path is not None:
            faces.append((path, getattr(record, "index", 0)))
    if not faces:
        return None, (chain[0] if chain else None)
    run = text_engine.shape_selection(text, faces, size=_font_size(style))
    return (run if run.width else None), chain[0]


def _run_metrics(run, style):
    """The extents a shaped run asks a line box for: those of the tallest face it used."""
    best = None
    for path, index in getattr(run, "faces", ()):
        found = text_engine.metrics(path, index=index, size=_font_size(style))
        if found.height and (best is None or found.height > best.height):
            best = found
    return best


def _spacing(text, style, size):
    """What letter and word spacing add: every character, and every space.

    Chrome reads 25.797 + 3 x 2 = 31.797 for ``abc`` at 2px, and the flow charges the same
    way line by line, so a line's width is what a box reports.
    """
    extra = 0.0
    letter = str(style.get("letter-spacing", "normal")).strip().lower()
    if letter not in ("", "normal"):
        extra += len(text) * _px(letter, size)
    word = str(style.get("word-spacing", "normal")).strip().lower()
    if word not in ("", "normal"):
        extra += text.count(" ") * _px(word, size)
    return extra


def text_width(text, style, environment=None):
    """The advance width of *text* in *style*: shaped, then spaced the way CSS spaces it.

    A box and a canvas both measure through here, so ``measureText`` and a box agree by
    construction; text no face can be found for is estimated, which is the only fallback.
    """
    text = "" if text is None else str(text)
    size = _font_size(style)
    run, _record = _shaped(text, style, environment)
    width = run.width if run is not None else text_engine.estimate(text, size)
    return width + _spacing(text, style, size)


@dataclass(frozen=True)
class _LineStyle:
    """One line box: how tall it is, and where its baseline sits inside it."""

    height: float
    ascent: float
    descent: float


def _font_metrics(style, environment=None):
    """The face's own extents for a style, or ``None`` when no face can be read."""
    record = _face_for(_families(style), environment, style.get("font-weight", "400"),
                       style.get("font-style", "normal"))
    path = _face_path(record)
    if path is None:
        return None
    found = text_engine.metrics(path, index=getattr(record, "index", 0), size=_font_size(style))
    return found if found.height else None


def _line_style(style, environment=None, root=16.0, found=None):
    """The line box a style asks for, and the baseline inside it.

    ``normal`` is the face's extents, a unitless number is a multiple of the font size, a
    percentage is one of it too, and a length is itself; the leading splits evenly above and
    below, which is where the baseline comes from.  *found* is the face the run really used.
    """
    size = _font_size(style)
    if found is None:
        found = _font_metrics(style, environment)
    ascent = found.ascent if found else size * 0.8
    descent = found.descent if found else size * 0.2
    gap = found.line_gap if found else 0.0
    content = ascent + descent + gap
    declared = str(style.get("line-height", "normal")).strip().lower()
    value = None
    if declared in ("", "normal"):
        value = content
    elif re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)", declared):
        value = float(declared) * size
    else:
        value = _font_size_term(declared, size, root)
    if value is None or value <= 0:
        # A line height CSS could not parse is a declaration it dropped, so what is left is
        # the face's own spacing -- and only a face that cannot be read falls back further.
        height = content if content > 0 else size * _LINE_HEIGHT_FALLBACK
    else:
        height = value
    baseline = ascent + (height - content) / 2.0
    return _LineStyle(height, baseline, height - baseline)


def _inline_declarations(element):
    return tuple(CSSStyleDeclaration(element=element)._declarations())


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


def _cascade(element, rules):
    values = initial_values_for(element)
    parent = getattr(element, "parentElement", None)
    parent_size = 16.0
    if parent is not None:
        inherited = _cascade(parent, rules)
        # Inherit every inherited property, not only those with an initial value here.
        values.update({
            name: inherited.get(name, values.get(name, ""))
            for name in INHERITED_PROPERTIES
        })
        parent_size = _px(inherited.get("font-size", "16px"), 16) or 16
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
    # A computed size is absolute in every browser, so a relative one is resolved here,
    # where the parent's own computed size -- and the root's -- are at hand.
    values["font-size"] = _px_text(_font_size_px(values.get("font-size", "16px"),
                                                 parent_size, _root_font_size(element)))
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
    return _cascade(element, document_rules(getattr(element, "ownerDocument", None)))


@dataclass
class Box:
    """One element's boxes, in viewport coordinates.

    ``left``/``top``/``width``/``height`` are the border box, and the padding and
    border widths say where the padding box and content box sit inside it.
    """
    width: float = 0
    height: float = 0
    left: float = 0
    top: float = 0
    padding: dict = field(default_factory=dict)
    border: dict = field(default_factory=dict)
    margin: dict = field(default_factory=dict)
    #: How far the content reaches past the border box's left and top edges.
    overflow_right: float = 0.0
    overflow_bottom: float = 0.0

    @property
    def padding_box_left(self): return self.left + self.border.get("left", 0)
    @property
    def padding_box_top(self): return self.top + self.border.get("top", 0)
    @property
    def padding_box_width(self):
        return self.width - self.border.get("left", 0) - self.border.get("right", 0)
    @property
    def padding_box_height(self):
        return self.height - self.border.get("top", 0) - self.border.get("bottom", 0)


#: The four sides a shorthand covers, clockwise from the top.
_SIDES = ("top", "right", "bottom", "left")


#: What an inline-level box takes part in without leaving the line it is on.
_INLINE = ("inline", "inline-block", "inline-flex", "inline-grid", "inline-table", "ruby")


#: The default object size a replaced element falls back to when its content has no
#: intrinsic size of its own, which is what an empty iframe, canvas or video measures.
_DEFAULT_OBJECT_SIZE = {
    "iframe": (300.0, 150.0), "canvas": (300.0, 150.0), "video": (300.0, 150.0),
    "object": (300.0, 150.0), "svg": (300.0, 150.0),
}


def _expand(value):
    """The four lengths a CSS shorthand holds."""
    parts = str(value).split()
    if not parts:
        return ["0"] * 4
    if len(parts) == 1:
        return parts * 4
    if len(parts) == 2:
        return [parts[0], parts[1], parts[0], parts[1]]
    if len(parts) == 3:
        return [parts[0], parts[1], parts[2], parts[1]]
    return parts[:4]


def _sides(style, name, reference, default="0"):
    """Per-side lengths, the shorthand standing in for whatever is not set."""
    values = _expand(style.get(name, default))
    return {side: _px(style.get(f"{name}-{side}", values[index]), reference)
            for index, side in enumerate(_SIDES)}


def _auto(value):
    return value is None or str(value).strip().lower() in ("", "auto")


def _margin_is_auto(style, side):
    """Whether one horizontal margin asked for ``auto``: its own, else the shorthand."""
    declared = style.get(f"margin-{side}")
    if declared is None:
        declared = _expand(style.get("margin", "0"))[_SIDES.index(side)]
    return _auto(declared)


def _style_of(element, rules):
    return _cascade(element, rules)


@dataclass(frozen=True)
class _Containing:
    """The block a box is measured against, in viewport coordinates."""
    left: float = 0.0
    top: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass(frozen=True)
class _Walk:
    """One layout pass: the rules to cascade, where boxes go, the viewport, and what is filled
    in as it runs -- ``baselines`` per element, ``overflows`` for ``scrollWidth``.
    """
    rules: tuple
    collected: dict | None
    viewport: tuple
    baselines: dict = field(default_factory=dict)
    overflows: dict = field(default_factory=dict)


def _viewport_width(element):
    realm = getattr(getattr(element, "_services", None), "realm_services", None)
    viewport = getattr(getattr(realm, "environment", None), "viewport", None)
    width = getattr(viewport, "width", None) or 1280
    height = getattr(viewport, "height", None) or 720
    return float(width), float(height)


def _offset(style, forward, backward, size):
    """A relative offset: the first side set wins, and ``auto`` means none."""
    if not _auto(style.get(forward)):
        return _px(style[forward], size)
    if not _auto(style.get(backward)):
        return -_px(style[backward], size)
    return 0.0


def _place(element, style, walk, containing, positioned):
    """Lay *element* out inside *containing* and answer its border box.

    *positioned* is the padding box of the nearest positioned ancestor, which is what
    an absolutely positioned box measures against; ``None`` means the viewport, the
    initial containing block of a page.
    """
    def record(box):
        if walk.collected is not None:
            walk.collected[element] = box
        return box

    if style.get("display") == "none" or not getattr(element, "isConnected", False):
        return record(Box())

    padding = _sides(style, "padding", containing.width)
    border = _sides(style, "border-width", containing.width)
    margin = _sides(style, "margin", containing.width)
    horizontal = padding["left"] + padding["right"] + border["left"] + border["right"]
    vertical = padding["top"] + padding["bottom"] + border["top"] + border["bottom"]

    declared_width = style.get("width", "auto")
    available = max(0.0, containing.width - margin["left"] - margin["right"] - horizontal)
    default_size = _DEFAULT_OBJECT_SIZE.get(str(getattr(element, "localName", "")).lower())
    if _auto(declared_width):
        if default_size is not None:
            # A replaced element with no intrinsic size of its own takes the default
            # object size rather than shrinking to a content it does not have.
            content_width = default_size[0]
        else:
            # An inline-level box shrinks around its content and an empty one stays
            # empty; a block-level box fills what is left of its containing block.
            content_width = (_shrink_to_fit(element, style, walk.rules, available)
                             if style.get("display", "block") in _INLINE else available)
    else:
        width = _px(declared_width, containing.width)
        content_width = max(0.0, width - horizontal
                            if style.get("box-sizing", "content-box") == "border-box"
                            else width)
    border_width = content_width + horizontal
    if not _auto(declared_width) and style.get("display", "block") not in _INLINE:
        # ``auto`` shares the free space; a side with a length keeps it.
        auto_sides = [side for side in ("left", "right") if _margin_is_auto(style, side)]
        if auto_sides:
            free = max(0.0, containing.width - border_width - sum(
                margin[side] for side in ("left", "right") if side not in auto_sides))
            for side in auto_sides:
                margin[side] = free / len(auto_sides)
            left = containing.left + margin["left"]

    position = style.get("position", "static")
    left = containing.left + margin["left"]
    top = containing.top + margin["top"]
    declared_height = style.get("height", "auto")
    if not _auto(declared_height):
        content_height = max(0.0, _px(declared_height, containing.height))
    elif default_size is not None:
        content_height = default_size[1]
    else:
        content_height = None
    if position in ("absolute", "fixed"):
        # A fixed box measures against the viewport, an absolute one against its ancestor.
        block = (_Containing(0.0, 0.0, *walk.viewport) if position == "fixed"
                 else positioned or _Containing(0.0, 0.0, *walk.viewport))
        if not _auto(style.get("left")):
            left = block.left + _px(style["left"], block.width) + margin["left"]
        elif not _auto(style.get("right")):
            left = (block.left + block.width - _px(style["right"], block.width)
                    - margin["right"] - border_width)
        if not _auto(style.get("top")):
            top = block.top + _px(style["top"], block.height) + margin["top"]
        elif not _auto(style.get("bottom")) and content_height is not None:
            top = (block.top + block.height - _px(style["bottom"], block.height)
                   - margin["bottom"] - content_height - vertical)

    content_left = left + border["left"] + padding["left"]
    content_top = top + border["top"] + padding["top"]
    inner = _Containing(content_left, content_top, content_width,
                        content_height if content_height is not None else 0.0)
    # A positioned box becomes the containing block for its own absolute descendants,
    # measured on the padding box, which is why the offsets exclude the border.
    child_positioned = positioned
    if position != "static":
        child_positioned = _Containing(
            left + border["left"], top + border["top"],
            max(0.0, border_width - horizontal),
            content_height + vertical if content_height is not None else 0.0)
    used_height = _children(element, style, walk, inner, child_positioned)
    recorded = walk.baselines.get(element)
    if recorded is not None:
        # The flow measures from the content's top; a box on a line from its border box.
        walk.baselines[element] = recorded + (content_top - top)
    if content_height is None:
        content_height = used_height
    border_height = content_height + vertical

    if position == "relative":
        offset_x = _offset(style, "left", "right", containing.width)
        offset_y = _offset(style, "top", "bottom", containing.height)
        if offset_x or offset_y:
            left += offset_x
            top += offset_y
            _shift(element, walk, offset_x, offset_y)

    overflow = walk.overflows.get(element, (0.0, 0.0))
    return record(Box(width=border_width, height=border_height, left=left, top=top,
                      padding=padding, border=border, margin=margin,
                      overflow_right=(border["left"] + padding["left"]
                                      + max(content_width, overflow[0])),
                      overflow_bottom=(border["top"] + padding["top"]
                                       + max(content_height, overflow[1]))))


def _shift(element, walk, x, y):
    """Move everything the subtree recorded, for a relative offset applied afterwards."""
    if walk.collected is None or (not x and not y):
        return
    for node, box in list(walk.collected.items()):
        if node is element or _contains(element, node):
            box.left += x
            box.top += y


def _contains(ancestor, node):
    current = getattr(node, "parentElement", None)
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "parentElement", None)
    return False


def _children(element, style, walk, containing, positioned):
    """Lay out *element*'s children and answer the content height they take."""
    display = style.get("display", "block")
    if display in ("flex", "inline-flex"):
        return _flex_children(element, style, walk, containing, positioned)
    if display in ("grid", "inline-grid"):
        return _grid_children(element, style, walk, containing, positioned)
    return _flow_children(element, style, walk, containing, positioned)


def _child_elements(element):
    return [child for child in getattr(element, "_children", ())
            if isinstance(child, (Element, Text))]


def _text_points(content, white_space, word_break, overflow_wrap):
    """``(points, loose)`` for the line ends of *content*.

    ``points`` are UAX#14's opportunities, which ``nowrap`` and ``pre`` cut back to the
    newlines alone.  ``loose`` are grapheme boundaries, offered only on an empty line, which
    is where ``break-word`` cuts a word that does not fit even on one.
    """
    points = text_engine.breaks(content)
    if white_space in ("nowrap", "pre"):
        return tuple(point for point in points if point.forced), ()
    if word_break == "break-all" or overflow_wrap == "anywhere":
        loose = tuple(text_engine.BreakPoint(offset) for offset in text_engine.graphemes(content))
        return points + loose, ()
    if overflow_wrap == "break-word":
        return points, tuple(text_engine.BreakPoint(offset)
                             for offset in text_engine.graphemes(content))
    return points, ()


def _collapse(content, white_space):
    """A text node's content after ``white-space``: pre keeps all, pre-line keeps newlines."""
    if white_space in ("pre", "pre-wrap"):
        return content
    if white_space == "pre-line":
        return re.sub(r"[^\S\n]+", " ", content)
    return re.sub(r"\s+", " ", content)


def _box_content_widths(element, style, rules, depth):
    """The content widths one child asks for: what it declares, its object size, or its content."""
    declared = style.get("width", "auto")
    if not _auto(declared):
        return _px(declared, 0.0), _px(declared, 0.0)
    intrinsic = _DEFAULT_OBJECT_SIZE.get(str(getattr(element, "localName", "")).lower())
    if intrinsic is not None:
        return intrinsic[0], intrinsic[0]
    return _content_widths(element, style, rules, depth + 1)


def _content_widths(element, style, rules, depth=0):
    """``(min, max)`` content widths: the widest unbreakable run, and one line's worth.

    Inline-level pieces have no opportunity between them, so their minima add up; a
    block-level child stands on its own line and asks for its own content instead.
    """
    if depth > 32:
        return 0.0, 0.0
    white_space = style.get("white-space", "normal")
    word_break = str(style.get("word-break", "normal")).strip().lower()
    overflow_wrap = str(style.get("overflow-wrap", "normal")).strip().lower()
    environment = _environment_for(element)
    inline_min = inline_max = 0.0
    widest_min = widest_max = 0.0
    for child in _child_elements(element):
        if isinstance(child, Text):
            content = _collapse(child.get_textContent() or "", white_space)
            if not content.strip():
                continue
            run, _record = _shaped(content, style, environment)
            if run is None:
                # No face to shape with: the estimate stands in, unbroken.
                width = text_engine.estimate(content, _font_size(style))
                inline_min += width
                inline_max += width
                continue
            inline_max += run.width + _spacing(content, style, _font_size(style))
            points, _loose = _text_points(content, white_space, word_break, overflow_wrap)
            pieces = text_engine.wrap(run, 0.0, points)
            inline_min += max((piece.advance for piece in pieces), default=run.width)
            continue
        child_style = _style_of(child, rules)
        if child_style.get("display") == "none":
            continue
        edges = _sides(child_style, "margin", 0.0)
        edges.update(_sides(child_style, "padding", 0.0))
        edges.update(_sides(child_style, "border-width", 0.0))
        extra = sum(edges.values())
        minimum, maximum = _box_content_widths(child, child_style, rules, depth)
        if child_style.get("display", "block") in _INLINE:
            inline_min += minimum + extra
            inline_max += maximum + extra
        else:
            widest_min = max(widest_min, minimum + extra)
            widest_max = max(widest_max, maximum + extra)
    return max(inline_min, widest_min), max(inline_max, widest_max)


def _shrink_to_fit(element, style, rules, available, depth=0):
    """CSS's ``min(max(min_content, available), max_content)``."""
    minimum, maximum = _content_widths(element, style, rules, depth)
    return min(max(minimum, available), maximum)


class _Line:
    """A line box while it is being filled: what is on it, and how far the text has run."""

    __slots__ = ("width", "ascent", "descent", "items", "pending")

    def __init__(self):
        self.width = 0.0
        self.ascent = 0.0
        self.descent = 0.0
        self.items: list = []
        #: Collapsible spaces before the next opportunity: they count once something follows.
        self.pending = 0.0

    @property
    def started(self):
        return bool(self.items) or self.width > 0 or self.ascent > 0

    @property
    def height(self):
        return self.ascent + self.descent

    def add(self, width, ascent, descent):
        """Put a piece on the line: its width, and how far it reaches above and below."""
        self.width += self.pending + width
        self.pending = 0.0
        self.ascent = max(self.ascent, ascent)
        self.descent = max(self.descent, descent)

    def hang(self, width):
        """Leave spaces at the end of the line: they count only if something follows."""
        self.pending += width


def _flow_children(element, style, walk, containing, positioned):
    """Block boxes stacked, and inline-level content wrapped into line boxes.

    A line ends where the next piece would not fit or where the text forces it, and is as
    tall as the tallest thing on it; its baseline follows from its items, which is what puts
    an inline block and the text beside it on one line of type.
    """
    environment = _environment_for(element)
    root = _root_font_size(element)
    white_space = style.get("white-space", "normal")
    word_break = str(style.get("word-break", "normal")).strip().lower()
    overflow_wrap = str(style.get("overflow-wrap", "normal")).strip().lower()
    text_size = _font_size(style)
    cursor = 0.0
    line = _Line()
    first_baseline = None
    widest = 0.0
    leftmost = 0.0
    deepest = 0.0

    def flush():
        """End the line: put its items where its baseline and ``text-align`` say they go."""
        nonlocal cursor, line, first_baseline, widest
        if not line.started:
            return
        if first_baseline is None:
            first_baseline = cursor + line.ascent
        baseline = containing.top + cursor + line.ascent
        free = max(0.0, containing.width - line.width)
        align = str(style.get("text-align", "start")).strip().lower()
        shift = free / 2.0 if align == "center" else free if align in ("right", "end") else 0.0
        for child, box, item_ascent in line.items:
            _shift(child, walk, shift, baseline - item_ascent - box.top)
        widest = max(widest, line.width + shift)
        cursor += line.height
        line = _Line()

    def break_line(br_style=None):
        """End the current line; a break with nothing before it leaves an empty one behind."""
        nonlocal cursor
        if line.started:
            flush()
        else:
            cursor += _line_style(br_style or style, environment, root).height

    def add_text(content, text_style, points, loose=()):
        """Wrap one text node into the current line, ending lines as they fill."""
        nonlocal line
        if not content:
            return
        run, _record = _shaped(content, text_style, environment)
        line_style = _line_style(text_style, environment, root,
                                 None if run is None else _run_metrics(run, text_style))
        if run is None:
            # No face to shape with: the estimate stands in, unbroken.
            if content.strip() or line.started:
                line.add(text_engine.estimate(content, text_size),
                         line_style.ascent, line_style.descent)
            return
        start = 0
        while start < len(content):
            available = max(0.0, containing.width - line.width)
            span = text_engine.next_line(run, start, available, points)
            if span is not None and span.advance > available and not line.started and loose:
                # Only a word that does not fit on a line of its own may be cut inside.
                span = text_engine.next_line(run, start, available, points + loose)
            if span is None:
                break
            if span.end == span.start:
                if "\n" in content[span.start:span.resume]:
                    # A line the text asked for and left empty, which ``pre`` keeps.
                    break_line(text_style)
                elif line.started:
                    # Spaces only: they widen a line that has begun, and never make one.
                    line.add(run.advance_between(span.start, span.resume), 0.0, 0.0)
                start = span.resume
                continue
            piece = content[span.start:span.end]
            last = span.end >= len(content)
            advance = span.advance + _spacing(piece, text_style, text_size)
            if line.started and span.start == start and advance > available:
                # No room for this piece here: try it on a line of its own.
                flush()
                continue
            line.add(advance, line_style.ascent, line_style.descent)
            if last:
                break
            rest = content[span.resume:]
            if not rest.strip() and "\n" not in rest:
                # Only spaces are left: they hang here and the line goes on.
                line.hang(run.advance_between(span.end, span.resume))
                break
            flush()
            start = span.resume if span.resume > span.start else span.end

    for child in _child_elements(element):
        if isinstance(child, Text):
            content = _collapse(child.get_textContent() or "", white_space)
            if not content.strip() and not line.started:
                # Whitespace with nothing before it makes no line box.
                continue
            points, loose = _text_points(content, white_space, word_break, overflow_wrap)
            add_text(content, style, points, loose)
            continue
        if str(getattr(child, "localName", "")).lower() == "br":
            # A line break of its own: it ends the current line, and makes one when empty.
            break_line()
            continue
        child_style = _style_of(child, walk.rules)
        if child_style.get("display") == "none":
            continue
        if child_style.get("position", "static") in ("absolute", "fixed"):
            # Out of flow: it keeps the cursor as its static position and leaves it alone.
            flush()
            _place(child, child_style, walk,
                   _Containing(containing.left, containing.top + cursor, containing.width,
                               containing.height),
                   positioned)
            continue
        child_margin = _sides(child_style, "margin", containing.width)
        if child_style.get("display", "block") in _INLINE:
            placed = _place(child, child_style, walk,
                            _Containing(containing.left + line.width, containing.top + cursor,
                                        containing.width, containing.height),
                            positioned)
            outer = placed.width + child_margin["left"] + child_margin["right"]
            if line.started and line.width and line.width + outer > containing.width:
                # It does not fit after what is on the line, so the line ends first.
                flush()
                placed = _place(child, child_style, walk,
                                _Containing(containing.left, containing.top + cursor,
                                            containing.width, containing.height),
                                positioned)
                outer = placed.width + child_margin["left"] + child_margin["right"]
            leftmost = max(leftmost, (placed.left - containing.left)
                           + max(placed.width, placed.overflow_right) + child_margin["right"])
            height = placed.height + child_margin["top"] + child_margin["bottom"]
            recorded = walk.baselines.get(child)
            item_ascent = height if recorded is None else recorded + child_margin["top"]
            line.add(outer, item_ascent, max(0.0, height - item_ascent))
            line.items.append((child, placed, item_ascent))
            continue
        flush()
        placed = _place(child, child_style, walk,
                        _Containing(containing.left, containing.top + cursor, containing.width,
                                    containing.height),
                        positioned)
        recorded = walk.baselines.get(child)
        if first_baseline is None and recorded is not None:
            # A block's baseline is the one its first line box has, wherever that line is.
            first_baseline = cursor + child_margin["top"] + recorded
        leftmost = max(leftmost, (placed.left - containing.left)
                       + max(placed.width, placed.overflow_right) + child_margin["right"])
        deepest = max(deepest, (placed.top - containing.top)
                      + max(placed.height, placed.overflow_bottom))
        cursor += child_margin["top"] + placed.height + child_margin["bottom"]
    flush()
    if first_baseline is not None:
        walk.baselines[element] = first_baseline
    walk.overflows[element] = (max(widest, leftmost), max(cursor, deepest))
    return cursor


def _flex_children(element, style, walk, containing, positioned):
    row = style.get("flex-direction", "row") not in ("column", "column-reverse")
    children = [child for child in _child_elements(element) if isinstance(child, Element)]
    if row:
        cursor = 0.0
        tallest = 0.0
        widest = 0.0
        for child in children:
            child_style = _style_of(child, walk.rules)
            placed = _place(child, child_style, walk,
                            _Containing(containing.left + cursor, containing.top,
                                        max(0.0, containing.width - cursor), containing.height),
                            positioned)
            cursor += placed.width
            tallest = max(tallest, placed.height)
            widest = max(widest, (placed.left - containing.left)
                         + max(placed.width, placed.overflow_right))
        walk.overflows[element] = (widest, tallest)
        return tallest
    cursor = 0.0
    widest = 0.0
    deepest = 0.0
    for child in children:
        child_style = _style_of(child, walk.rules)
        placed = _place(child, child_style, walk,
                        _Containing(containing.left, containing.top + cursor, containing.width,
                                    max(0.0, containing.height - cursor)),
                        positioned)
        cursor += placed.height
        widest = max(widest, (placed.left - containing.left)
                     + max(placed.width, placed.overflow_right))
        deepest = max(deepest, (placed.top - containing.top)
                     + max(placed.height, placed.overflow_bottom))
    walk.overflows[element] = (widest, max(cursor, deepest))
    return cursor


def _grid_children(element, style, walk, containing, positioned):
    columns = max(1, len(str(style.get("grid-template-columns", "1fr")).split()))
    cell = containing.width / columns
    children = [child for child in _child_elements(element) if isinstance(child, Element)]
    cursor = 0.0
    row_height = 0.0
    widest = 0.0
    for index, child in enumerate(children):
        column = index % columns
        if column == 0 and index:
            cursor += row_height
            row_height = 0.0
        child_style = _style_of(child, walk.rules)
        placed = _place(child, child_style, walk,
                        _Containing(containing.left + column * cell, containing.top + cursor,
                                    cell, containing.height),
                        positioned)
        row_height = max(row_height, placed.height)
        widest = max(widest, (placed.left - containing.left)
                     + max(placed.width, placed.overflow_right))
    walk.overflows[element] = (widest, cursor + row_height)
    return cursor + row_height


def layout(element, containing_width=None, top=0.0, rules=None, style=None, collected=None):
    """*element*'s border box in viewport coordinates, laying the document out.

    The document is walked once per call: a box only knows where it sits after its
    ancestors have placed it, so ``containing_width`` and ``top`` are ignored and
    every recorded box carries its own viewport coordinates.
    """
    document = getattr(element, "ownerDocument", None)
    if document is None:
        return Box()
    boxes = {} if collected is None else collected
    root = document.get_documentElement()
    width, height = _viewport_width(element)
    if isinstance(root, Element):
        walk = _Walk(rules or document_rules(document), boxes, (width, height))
        _place(root, _style_of(root, walk.rules), walk,
               _Containing(0.0, 0.0, width, height), None)
    return boxes.get(element, Box())


def positioned_box(element):
    """*element*'s box with the offset its ancestors put it at."""
    return layout(element)


def offset_parent(element):
    """The box an element's ``offsetLeft`` is measured against, or ``None``."""
    if not getattr(element, "isConnected", False):
        return None
    own = computed_style(element)
    if own.get("position", "static") == "fixed":
        # Chrome answers no offset parent for a fixed box, nor for one with no box at all.
        return None
    if own.get("display", "block") == "none":
        return None
    document = getattr(element, "ownerDocument", None)
    if getattr(element, "localName", "") in ("html", "body"):
        return None
    current = getattr(element, "parentElement", None)
    while current is not None:
        style = computed_style(current)
        if style.get("position", "static") != "static":
            return current
        if getattr(current, "localName", "") in ("body", "td", "th", "table"):
            return current
        current = getattr(current, "parentElement", None)
    if document is not None:
        return document.get_body()
    return None


def computed_style_object(element, *, rendered=True):
    """The cascade as a declaration block: the same interface, and nothing may write it back."""
    return CSSStyleDeclaration(element=element,
                               computed=computed_style(element) if rendered else {})

__all__ = [
    "Box", "computed_style", "computed_style_object", "document_rules", "layout",
    "offset_parent", "positioned_box", "text_width",
]
