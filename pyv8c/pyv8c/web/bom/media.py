"""``window.matchMedia`` and the list it hands back.

Viewport, screen and resolution features read the same environment as Window and
Screen.  A viewport that never resizes never fires a change.
"""

import math
import re


from ...idl import interface
from ...foundation.arguments import MISSING, missing_error
from ..dom.events import EventTarget


#: ``(min-width: 40em)`` and the Media Queries 4 equality form ``(width = 0px)``.
_FEATURE_RE = re.compile(r"\(\s*([a-z-]+)\s*(:|=)\s*([^)]*)\)", re.I)
_NUMBER_RE = r"(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?"
_RESOLUTION_RE = re.compile(rf"^\s*({_NUMBER_RE})\s*(dppx|dpi|dpcm)\s*$", re.I)
_RATIO_RE = re.compile(rf"^\s*({_NUMBER_RE})(?:\s*/\s*({_NUMBER_RE}))?\s*$", re.I)


#: What a desktop browser reports for the features this one does not model.
_DEFAULT_FEATURES = {
    "prefers-color-scheme": "light",
    "prefers-reduced-motion": "no-preference",
    "prefers-contrast": "no-preference",
    "hover": "hover",
    "any-hover": "hover",
    "pointer": "fine",
    "any-pointer": "fine",
    "display-mode": "browser",
    "scripting": "enabled",
    "update": "fast",
    "overflow-block": "scroll",
    "overflow-inline": "scroll",
    "color-gamut": "srgb",
    "forced-colors": "none",
    "dynamic-range": "standard",
    "prefers-reduced-transparency": "no-preference",
    "device-posture": "continuous",
}


def _pixels(text):
    """A media-query length in pixels, or None when it is not a length."""
    match = re.match(r"^\s*(-?(?:\d+\.?\d*|\.\d+))\s*(px|em|rem)?\s*$", str(text))
    if not match:
        return None
    value = float(match.group(1))
    return value * 16.0 if (match.group(2) or "px").lower() in ("em", "rem") else value


def _resolution(value):
    match = _RESOLUTION_RE.fullmatch(value)
    if match is None:
        return None
    amount = float(match.group(1))
    return amount * {"dppx": 1, "dpi": 1 / 96, "dpcm": 2.54 / 96}[match.group(2).lower()]


def _ratio(value):
    match = _RATIO_RE.fullmatch(value)
    if match is None:
        return None
    denominator = float(match.group(2)) if match.group(2) else 1.0
    return float(match.group(1)) / denominator if denominator else None


def _compare(name, actual, wanted):
    if wanted is None:
        return False
    if name.startswith("min-"):
        return actual >= wanted
    if name.startswith("max-"):
        return actual <= wanted
    return abs(actual - wanted) < 1e-9


def _matches_feature(name, value, width, height, screen_width, screen_height,
                     dpr, color_depth):
    name, value = name.lower(), value.strip().lower()
    base = name.removeprefix("min-").removeprefix("max-")
    dimensions = {"width": width, "height": height,
                  "device-width": screen_width, "device-height": screen_height}
    if base in dimensions:
        return _compare(name, dimensions[base], _pixels(value))
    if base == "resolution":
        wanted = _resolution(value)
        if wanted is not None and value.endswith("dpcm"):
            # Blink compares dpcm values at two decimal places in dppx.
            return _compare(name, math.floor(0.5 + 100 * dpr) / 100,
                            math.floor(0.5 + 100 * wanted) / 100)
        return _compare(name, dpr, wanted)
    if base in ("aspect-ratio", "device-aspect-ratio"):
        numerator, denominator = ((width, height) if base == "aspect-ratio"
                                  else (screen_width, screen_height))
        return denominator > 0 and _compare(name, numerator / denominator, _ratio(value))
    if base == "color":
        try:
            return _compare(name, color_depth // 3, float(value))
        except ValueError:
            return False
    if base == "color-index":
        try:
            return _compare(name, 0, float(value))
        except ValueError:
            return False
    if name == "orientation":
        return value == ("landscape" if width >= height else "portrait")
    return _DEFAULT_FEATURES.get(name) == value


def evaluate(query, width, height, *, screen_width=None, screen_height=None,
             dpr=1.0, color_depth=24):
    """Whether *query* holds for a viewport of this size; an unknown feature
    answers false, as a browser answers an unsupported feature.
    """
    screen_width = width if screen_width is None else screen_width
    screen_height = height if screen_height is None else screen_height
    for alternative in str(query).split(","):
        alternative = alternative.strip().lower()
        if not alternative:
            continue
        negated = alternative.startswith("not ")
        if negated:
            alternative = alternative[4:].strip()
        head = alternative.split(" and ", 1)[0].strip()
        matches = head in ("", "all", "screen") if not head.startswith("(") else True
        features = _FEATURE_RE.findall(alternative)
        matches = matches and bool(features or not head.startswith("(")) and all(
            _matches_feature(name, value, width, height, screen_width,
                             screen_height, dpr, color_depth)
            for name, _operator, value in features
        )
        if matches != negated:
            return True
    return False


@interface('MediaQueryList', 'EventTarget')
class MediaQueryList(EventTarget):
    """``matchMedia``'s answer: the query and whether it holds right now."""

    def __init__(self, query="", width=0, height=0, *, screen_width=None,
                 screen_height=None, dpr=1.0, color_depth=24, **_):
        super().__init__()
        self._query = str(query)
        self._matches = evaluate(self._query, width, height,
                                 screen_width=screen_width, screen_height=screen_height,
                                 dpr=dpr, color_depth=color_depth)

    def get_media(self): return self._query
    def get_matches(self): return bool(self._matches)
    def get_onchange(self): return self._get_handler("onchange")
    def set_onchange(self, value): self._set_handler("onchange", value)

    def fn_addListener(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error('MediaQueryList', 'addListener', 1, 0, constructor=False)
        return self.fn_addEventListener("change", callback)

    def fn_removeListener(self, callback=MISSING, *_extra):
        if callback is MISSING:
            raise missing_error('MediaQueryList', 'removeListener', 1, 0, constructor=False)
        return self.fn_removeEventListener("change", callback)

__all__ = ["MediaQueryList", "evaluate"]
