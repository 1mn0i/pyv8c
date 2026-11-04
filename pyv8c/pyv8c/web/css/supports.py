"""The CSS.supports() API and condition parser."""
import re
from ...idl import interface
from ...foundation.arguments import MISSING, dom_string
from .properties import supported


_IDENT = r"-?[A-Za-z_][A-Za-z0-9_-]*"
_DECLARATION_NAME = re.compile(r"--[A-Za-z0-9_-]+|-?[A-Za-z_][A-Za-z0-9_-]*")
_ATTRIBUTE_RE = re.compile(
    rf"^\s*{_IDENT}\s*(?:[~|^$*]?=\s*(?:{_IDENT}|\"[^\"]*\"|'[^']*')\s*)?$")
#: Pseudo names Chrome accepts; an unknown one makes the selector a parse error.
_PSEUDO_CLASSES = frozenset((
    "active", "any-link", "checked", "default", "defined", "dir", "disabled",
    "empty", "enabled", "first-child", "first-of-type", "focus", "focus-visible",
    "focus-within", "fullscreen", "has", "host", "hover", "in-range", "indeterminate",
    "invalid", "is", "lang", "last-child", "last-of-type", "link", "modal",
    "not", "nth-child", "nth-last-child", "nth-last-of-type", "nth-of-type",
    "only-child", "only-of-type", "optional", "out-of-range", "placeholder-shown",
    "popover-open", "read-only", "read-write", "required", "root", "scope",
    "target", "user-invalid", "valid", "visited", "where",
))
_PSEUDO_ELEMENTS = frozenset((
    "after", "backdrop", "before", "cue", "file-selector-button", "first-letter",
    "first-line", "marker", "part", "placeholder", "selection", "slotted",
))
_SELECTOR_ARGUMENT_PSEUDOS = frozenset(("has", "is", "not", "where"))


def _balanced_end(text, start, opening, closing):
    depth, quote = 0, None
    for index in range(start, len(text)):
        char = text[index]
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _compound_supported(text):
    if not text:
        return False
    index = 0
    match = re.match(r"\*|" + _IDENT, text)
    if match is not None:
        index = match.end()
    while index < len(text):
        char = text[index]
        if char in ".#":
            match = re.match(_IDENT, text[index + 1:])
            if match is None:
                return False
            index += 1 + match.end()
        elif char == "[":
            end = _balanced_end(text, index, "[", "]")
            if end is None or not _ATTRIBUTE_RE.match(text[index + 1:end]):
                return False
            index = end + 1
        elif char == ":":
            element = text.startswith("::", index)
            start = index + (2 if element else 1)
            match = re.match(_IDENT, text[start:])
            if match is None:
                return False
            name = match.group(0).lower()
            index = start + match.end()
            if index < len(text) and text[index] == "(":
                end = _balanced_end(text, index, "(", ")")
                if end is None:
                    return False
                inner = text[index + 1:end]
                if name in _SELECTOR_ARGUMENT_PSEUDOS:
                    if not _selector_supported(inner):
                        return False
                elif not inner.strip():
                    return False
                index = end + 1
            elif element:
                if name not in _PSEUDO_ELEMENTS:
                    return False
            elif name not in _PSEUDO_CLASSES:
                return False
        else:
            return False
    return index > 0


def _split_compounds(text):
    parts, current, depth, quote = [], [], 0, None
    for char in text.strip():
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char in "([":
            depth += 1
            current.append(char)
        elif char in ")]":
            depth -= 1
            current.append(char)
        elif depth == 0 and (char.isspace() or char in ">+~"):
            if current:
                parts.append("".join(current))
                current = []
            if not char.isspace():
                parts.append(char)
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _selector_group_supported(text):
    parts = _split_compounds(text)
    if not parts:
        return False
    previous = None
    for part in parts:
        if part in (">", "+", "~"):
            if previous is None or previous in (">", "+", "~"):
                return False
        elif not _compound_supported(part):
            return False
        previous = part
    return previous not in (">", "+", "~")


def _selector_supported(selector):
    text = str(selector).strip()
    if not text:
        return False
    depth, quote, current, groups = 0, None, [], []
    for char in text:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in "\"'":
            quote = char
            current.append(char)
        elif char in "([":
            depth += 1
            current.append(char)
        elif char in ")]":
            depth -= 1
            if depth < 0:
                return False
            current.append(char)
        elif char == "," and depth == 0:
            groups.append("".join(current))
            current = []
        else:
            current.append(char)
    if depth or quote is not None:
        return False
    groups.append("".join(current))
    return all(_selector_group_supported(group.strip()) for group in groups)


_UNSUPPORTED, _SUPPORTED, _FAILURE = 0, 1, 2


def _negate(result):
    if result == _SUPPORTED:
        return _UNSUPPORTED
    if result == _UNSUPPORTED:
        return _SUPPORTED
    return result


def _conjunction(left, right):
    if left == _FAILURE or right == _FAILURE:
        return _FAILURE
    return _SUPPORTED if left == _SUPPORTED and right == _SUPPORTED else _UNSUPPORTED


def _disjunction(left, right):
    if left == _FAILURE or right == _FAILURE:
        return _FAILURE
    return _SUPPORTED if left == _SUPPORTED or right == _SUPPORTED else _UNSUPPORTED


class _Stream:
    """The token cursor Blink's CSSSupportsParser walks."""

    __slots__ = ("text", "index")

    def __init__(self, text):
        self.text, self.index = text, 0

    def skip(self):
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def peek(self):
        return self.text[self.index] if self.index < len(self.text) else ""

    def ident(self):
        match = re.match(_IDENT, self.text[self.index:])
        # An identifier immediately followed by '(' is a function token, not a keyword.
        if match is None or self.text[self.index + match.end():].startswith("("):
            return ""
        return match.group(0).lower()

    def at_ident(self, name):
        return self.ident() == name

    def take_ident(self):
        name = self.ident()
        if name:
            self.index += len(name)
        return name

    def take_if_ident(self, name):
        if not self.at_ident(name):
            return False
        self.index += len(name)
        return True

    def take_function(self):
        match = re.match(_IDENT + r"\(", self.text[self.index:])
        if match is None:
            return None
        self.index += match.end()
        return match.group(0)[:-1].lower()

    def take_block(self):
        """Consume a balanced close for an opening parenthesis already consumed."""
        start, depth, quote = self.index, 1, None
        while self.index < len(self.text):
            char = self.text[self.index]
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    self.index += 1
                    return self.text[start:self.index - 1]
            self.index += 1
        return None


def _consume_condition(stream):
    stream.skip()
    if stream.take_if_ident("not"):
        return _negate(_consume_in_parens(stream))
    result = _consume_in_parens(stream)
    if stream.at_ident("and"):
        while stream.take_if_ident("and"):
            result = _conjunction(result, _consume_in_parens(stream))
    elif stream.at_ident("or"):
        while stream.take_if_ident("or"):
            result = _disjunction(result, _consume_in_parens(stream))
    return result


def _consume_in_parens(stream):
    stream.skip()
    if stream.peek() == "(":
        saved = stream.index
        stream.index += 1
        inner = _consume_condition(stream)
        stream.skip()
        if inner != _FAILURE and stream.peek() == ")":
            stream.index += 1
            stream.skip()
            return inner
        stream.index = saved
    if _consume_feature(stream):
        return _SUPPORTED
    if _consume_general_enclosed(stream):
        return _UNSUPPORTED
    return _FAILURE


#: `IsSupportedKeywordFormat` / `IsSupportedKeywordTech` as Chrome 155 answers them.
_FONT_FORMATS = frozenset(("collection", "opentype", "truetype", "woff", "woff2"))
_FONT_TECHS = frozenset((
    "color-cbdt", "color-colrv0", "color-colrv1", "color-sbix",
    "features-aat", "features-opentype", "palettes", "variations",
))
_AT_RULES = frozenset((
    "container", "font-face", "import", "keyframes", "layer", "media", "namespace",
    "page", "property", "scope", "supports",
))


def _consume_feature(stream):
    saved = stream.index
    name = stream.take_function()
    if name == "selector":
        inner = stream.take_block()
        if inner is not None and _selector_supported(inner):
            stream.skip()
            return True
        stream.index = saved
        return False
    if name in ("font-format", "font-tech"):
        inner = stream.take_block()
        known = _FONT_FORMATS if name == "font-format" else _FONT_TECHS
        if inner is not None and inner.strip().lower() in known:
            stream.skip()
            return True
        stream.index = saved
        return False
    if name == "at-rule":
        inner = stream.take_block()
        match = None if inner is None else re.match(r"@([A-Za-z-]+)\s*$", inner.strip())
        if match is not None and match.group(1).lower() in _AT_RULES:
            stream.skip()
            return True
        stream.index = saved
        return False
    if name is not None:
        stream.index = saved
    if stream.peek() == "(":
        saved = stream.index
        stream.index += 1
        if _consume_declaration(stream):
            stream.skip()
            if stream.peek() == ")":
                stream.index += 1
                stream.skip()
                return True
        stream.index = saved
    return False


def _consume_declaration(stream):
    stream.skip()
    match = _DECLARATION_NAME.match(stream.text[stream.index:])
    if match is None:
        return False
    name = match.group(0).lower()
    stream.index += match.end()
    stream.skip()
    if stream.peek() != ":":
        return False
    stream.index += 1
    start, depth, quote = stream.index, 0, None
    while stream.index < len(stream.text):
        char = stream.text[stream.index]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        stream.index += 1
    value = stream.text[start:stream.index].strip()
    return bool(value) and (name.startswith("--") or supported(name))


def _consume_general_enclosed(stream):
    if stream.peek() == "(":
        stream.index += 1
        return stream.take_block() is not None
    saved = stream.index
    if stream.take_function() is None:
        return False
    if stream.take_block() is None:
        stream.index = saved
        return False
    return True


def _supports_condition(text):
    stream = _Stream("(" + str(text) + ")")
    result = _consume_condition(stream)
    if result == _FAILURE:
        return False
    stream.skip()
    if stream.index != len(stream.text):
        return False
    return result == _SUPPORTED


@interface('CSS', None)
class CSS:
    """The CSSOM entry point: one question a page asks before it writes a rule."""

    @staticmethod
    def fn_supports(_realm, property=MISSING, value=MISSING, *_extra):
        """Whether a property is one this tree resolves names for.

        One argument is a whole declaration, as in ``CSS.supports("color: red")``; two
        are a property and a value. Arguments are coerced to strings and anything
        unparseable or unknown answers false rather than throwing, which is what
        Chrome does with a number or a missing argument. A value is only checked for
        being there, because a grammar per property is not modelled.
        """
        if value is MISSING:
            return _supports_condition(_text(property))
        return supported(_text(property)) and bool(_text(value).strip())


def _text(value):
    if value is MISSING:
        return ""
    return value if isinstance(value, str) else dom_string(value)


