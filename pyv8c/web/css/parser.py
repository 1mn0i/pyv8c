"""CSS declaration parsing and serialization."""
import re
from ...foundation.arguments import dom_string
from .colors import NAMED_COLORS, SYSTEM_COLORS
from .values import _ABSOLUTE_LENGTH, _ABSOLUTE_LENGTH_PROPERTIES, length_math

_NAME_RE = re.compile(r"^(?:--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*)$")

def _property_name(value):
    value = str(dom_string(value)).strip()
    if value.startswith("--"):
        return value if _NAME_RE.match(value) else ""
    value = value.lower()
    return value if _NAME_RE.match(value) else ""


def _normalize_value(value):
    value = str(dom_string(value)).strip()
    if not value or '\x00' in value or any(True for _ in _top_level_delimiters(value, '{};')):
        return ""
    return re.sub(r"[ \t\r\n]+", " ", value)


def _canonical_absolute_length(name, value):
    if name not in _ABSOLUTE_LENGTH_PROPERTIES:
        return value
    if value.lower().startswith('calc(') and '%' not in value:
        pixels = length_math(value)
        if pixels is not None:
            return f'calc({pixels:.6g}px)'
    match = _ABSOLUTE_LENGTH.fullmatch(value)
    if match is None:
        return value
    amount = f'{float(match[1]):g}'
    return f'{amount}{match[2].lower()}'


_BOX_SIDES = ('top', 'right', 'bottom', 'left')


def _box_values(value):
    """The four values represented by a simple margin/padding shorthand."""
    parts, current, depth, quote = [], [], 0, None
    for char in value:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            current.append(char)
        elif char == '(':
            depth += 1
            current.append(char)
        elif char == ')':
            depth -= 1
            current.append(char)
        elif char.isspace() and depth == 0:
            if current:
                parts.append(''.join(current))
                current = []
        else:
            current.append(char)
    if current:
        parts.append(''.join(current))
    if depth != 0 or quote:
        return None
    if not 1 <= len(parts) <= 4:
        return None
    return (parts[0], parts[1] if len(parts) > 1 else parts[0],
            parts[2] if len(parts) > 2 else parts[0],
            parts[3] if len(parts) > 3 else parts[1] if len(parts) > 1 else parts[0])


def _expand_box_conflicts(declarations, force=None):
    """Expand box shorthands when they overlap longhands, preserving cascade priority."""
    result = []
    names = {name for name, _, _ in declarations}
    for name, value, priority in declarations:
        parts = _box_values(value) if name in ('margin', 'padding') else None
        overlap = parts is not None and (force == name or any(
            name + '-' + side in names for side in _BOX_SIDES))
        items = [(name + '-' + side, part, priority)
                 for side, part in zip(_BOX_SIDES, parts)] if overlap else [(name, value, priority)]
        for item in items:
            previous = next((i for i, old in enumerate(result) if old[0] == item[0]), None)
            if previous is None:
                result.append(item)
            elif result[previous][2] != 'important' or item[2] == 'important':
                if overlap:
                    result.pop(previous)
                    result.append(item)
                else:
                    result[previous] = item
    return result


def _serialize_declarations(declarations):
    result = []
    index = 0
    while index < len(declarations):
        name, value, priority = declarations[index]
        for shorthand in ('margin', 'padding'):
            group = declarations[index:index + 4]
            if (len(group) == 4 and [item[0] for item in group] ==
                    [shorthand + '-' + side for side in _BOX_SIDES]
                    and len({item[2] for item in group}) == 1):
                top, right, bottom, left = (item[1] for item in group)
                if left == right:
                    value = (top if right == top else f'{top} {right}') if bottom == top else f'{top} {right} {bottom}'
                else:
                    value = f'{top} {right} {bottom} {left}'
                name, priority = shorthand, group[0][2]
                index += 3
                break
        result.append(f"{name}: {value}{' !important' if priority else ''};")
        index += 1
    return ' '.join(result)


def _valid_width(value):
    """Reject unsupported bare width identifiers at CSSOM parse time."""
    lower = value.lower()
    if lower in ('auto', 'min-content', 'max-content', 'fit-content', 'stretch',
                 'inherit', 'initial', 'unset', 'revert', 'revert-layer'):
        return True
    if lower.startswith(('calc(', 'min(', 'max(', 'clamp(', 'var(', 'fit-content(')):
        return True
    match = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+))'
                         r'(px|%|em|rem|vw|vh|vmin|vmax|in|cm|mm|q|pt|pc|ch|ex)?', lower)
    return match is not None and float(match[1]) >= 0 and (match[2] is not None or float(match[1]) == 0)


_FONT_SINGLE_KEYWORDS = frozenset({
    'caption', 'icon', 'menu', 'message-box', 'small-caption', 'status-bar',
    'inherit', 'initial', 'unset', 'revert', 'revert-layer',
})


def _valid_font(value):
    # A lone identifier cannot be a font shorthand unless it names a system font
    # or a CSS-wide keyword. In particular, Safari's -apple-system-body is not
    # accepted by Chrome's CSS parser.
    return (not re.fullmatch(r'[-a-zA-Z]+', value)
            or value.lower() in _FONT_SINGLE_KEYWORDS)



def _split_priority(value):
    match = re.search(r"\s*!\s*important\s*$", value, re.I)
    return (value[:match.start()].rstrip(), "important") if match else (value, "")


def valid_color(value):
    """Recognize color keywords, hexadecimal colors and CSS color functions."""
    value = str(value).strip().lower()
    if value in NAMED_COLORS or value in SYSTEM_COLORS or value in (
            'currentcolor', 'inherit', 'initial', 'unset', 'revert', 'revert-layer'):
        return True
    if re.fullmatch(r'#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})', value):
        return True
    return bool(re.fullmatch(r'(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color|'
                             r'color-mix|light-dark|device-cmyk|var)\(.+\)', value, re.S))


def _color_declaration(name, value):
    if name != 'color' or not value:
        return value
    if not valid_color(value):
        return None
    lowered = value.lower()
    return lowered if lowered in NAMED_COLORS or lowered in SYSTEM_COLORS else value


def parse_declarations(raw):
    """Parse ``a: b; c: d !important`` into a declaration list.

    A repeated property keeps the position of its first occurrence and the value
    of its last; this is the single parser for ``cssText`` and the cascade reader.
    """
    declarations, positions = [], {}
    raw, start = str(raw), 0
    for end in (*_top_level_delimiters(raw, ';'), len(raw)):
        chunk, start = raw[start:end], end + 1
        if ":" not in chunk:
            continue
        name, value = chunk.split(":", 1)
        name = _property_name(name)
        if not name:
            continue
        value, priority = _split_priority(_normalize_value(value))
        if not value:
            continue
        if (name == 'width' and not _valid_width(value)
                or name == 'font' and not _valid_font(value)):
            continue
        value = _color_declaration(name, value)
        if value is None:
            continue
        value = _canonical_absolute_length(name, value)
        item = (name, value, priority)
        if name in positions:
            old = declarations[positions[name]]
            if old[2] == 'important' and priority != 'important':
                continue
            declarations[positions[name]] = item
        else:
            positions[name] = len(declarations)
            declarations.append(item)
        declarations = _expand_box_conflicts(declarations)
        positions = {name: index for index, (name, _, _) in enumerate(declarations)}
    return _expand_box_conflicts(declarations)



def _top_level_delimiters(text, delimiters):
    """CSS separators outside strings, escapes, comments and functions."""
    quote, depth, escaped, comment = None, 0, False, False
    index = 0
    while index < len(text):
        char = text[index]
        if comment:
            if text[index:index+2] == '*/':
                comment = False
                index += 1
        elif escaped:
            escaped = False
        elif char == '\\':
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif text[index:index+2] == '/*':
            comment = True
            index += 1
        elif char in '([':
            depth += 1
        elif char in ')]':
            depth = max(0, depth - 1)
        elif not depth and char in delimiters:
            yield index
        index += 1



