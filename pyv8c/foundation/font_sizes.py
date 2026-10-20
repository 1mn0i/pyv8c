"""Pure CSS font-size resolution; no DOM, layout or native runtime imports."""
import re


#: What CSS's absolute font-size keywords mean, in the sizes a 16px ``medium`` maps to.
_FONT_SIZE_KEYWORDS = {
    "xx-small": 9.0, "x-small": 10.0, "small": 13.0, "medium": 16.0,
    "large": 18.0, "x-large": 24.0, "xx-large": 32.0, "xxx-large": 48.0,
}

#: ``smaller`` and ``larger`` are a ratio of the parent, not a step in the table above.
_FONT_SIZE_STEP = 1.2

#: One length in a font-size, with the unit CSS allows it to carry.
_FONT_SIZE_TERM = re.compile(
    r"(-?[0-9]*\.?[0-9]+)(px|pt|pc|in|cm|mm|q|em|rem|ex|ch|%|vw|vh|vmin|vmax)?", re.I)


def font_size_term(term, parent, root):
    """One length as pixels, or ``None`` when it is not a length this engine reads."""
    match = _FONT_SIZE_TERM.fullmatch(str(term).strip())
    if match is None:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in ("", "px"):
        return amount
    if unit == "em":
        return amount * parent
    if unit == "rem":
        return amount * root
    if unit == "%":
        return amount * parent / 100.0
    if unit == "pt":
        return amount * 96.0 / 72.0
    if unit == "pc":
        return amount * 16.0
    if unit == "in":
        return amount * 96.0
    if unit == "cm":
        return amount * 96.0 / 2.54
    if unit == "mm":
        return amount * 96.0 / 25.4
    if unit == "q":
        return amount * 96.0 / 101.6
    if unit in ("ex", "ch"):
        # A face's own ex and ch need its metrics; half an em is what they are taken for.
        return amount * parent * 0.5
    return None


def _calc_size(text, parent, root):
    """``calc(...)`` as pixels: a sum of lengths, which is all a font-size asks of it."""
    inner = text[text.index("(") + 1:text.rindex(")")].replace(" ", "")
    total = 0.0
    sign = 1.0
    terms = re.findall(r"[+-]?[^+-]+", inner)
    if not terms:
        return None
    for term in terms:
        if term[0] in "+-":
            sign = 1.0 if term[0] == "+" else -1.0
            term = term[1:]
        value = font_size_term(term, parent, root)
        if value is None:
            return None
        total += sign * value
    return total


def font_size_px(declared, parent=16.0, root=16.0):
    """``font-size`` as the absolute size every browser's computed style reports.

    A relative length resolves against the parent's computed size, ``rem`` against the
    root's, and a keyword against the table CSS defines for it.  A value this cannot read
    leaves the inherited size in place, which is what a declaration CSS refused to parse
    amounts to.
    """
    text = str(declared).strip().lower()
    if not text:
        return parent
    if text in _FONT_SIZE_KEYWORDS:
        return _FONT_SIZE_KEYWORDS[text]
    if text == "smaller":
        return parent / _FONT_SIZE_STEP
    if text == "larger":
        return parent * _FONT_SIZE_STEP
    if text.startswith("calc(") and text.endswith(")"):
        value = _calc_size(text, parent, root)
        return parent if value is None else value
    value = font_size_term(text, parent, root)
    return parent if value is None else value


def px_text(value):
    """A computed length as CSS writes it: four decimals at most, then the unit."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{text or '0'}px"
