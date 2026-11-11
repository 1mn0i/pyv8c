"""Canvas colors implementation."""

import math
import re


def parses_as_color(value):
    """Whether the canvas colour parser would take this string."""
    text = str(value).strip()
    if not text:
        return False
    if text.lower() == "transparent":
        return True
    if re.fullmatch(r"rgba?\([^)]*\)|hsla?\([^)]*\)|hwb\([^)]*\)|lab\([^)]*\)|lch\([^)]*\)"
                    r"|oklab\([^)]*\)|oklch\([^)]*\)|color\([^)]*\)", text, re.I):
        return True
    try:
        from PIL import ImageColor
        ImageColor.getrgb(text)
        return True
    except (ImportError, ValueError):
        return False


def canvas_color(value):
    value = str(value or "").strip().lower()
    named = {"red": (255, 0, 0, 255), "green": (0, 128, 0, 255), "blue": (0, 0, 255, 255),
             "black": (0, 0, 0, 255), "white": (255, 255, 255, 255), "transparent": (0, 0, 0, 0)}
    if value in named: return named[value]
    if value.startswith("#"):
        h = value[1:]
        if len(h) in (3, 4): h = ''.join(c * 2 for c in h)
        if len(h) == 6: h += "ff"
        try: return tuple(int(h[i:i+2], 16) for i in (0, 2, 4, 6))
        except ValueError: return (0, 0, 0, 255)
    m = re.match(r"rgba?\(([^)]+)\)", value)
    if m:
        p = [x.strip() for x in m.group(1).split(',')]
        try:
            rgb = [round(float(x[:-1]) * 2.55) if x.endswith('%') else int(float(x)) for x in p[:3]]
            if len(p) > 3:
                a = float(p[3]) * 255 if not p[3].endswith('%') else float(p[3][:-1]) * 2.55
            else:
                a = 255
            return tuple(max(0, min(255, int(x))) for x in (*rgb, a))
        except (ValueError, IndexError): pass
    return (0, 0, 0, 255)


def number_text(value):
    """How Chromium's ``String::Number`` spells a double in its messages."""
    if math.isnan(value): return "NaN"
    if math.isinf(value): return "Infinity" if value > 0 else "-Infinity"
    return f"{value:g}"


def parse_color(value):
    """The RGBA a canvas colour string parses to (alpha 0..1), or ``None`` when refused."""
    text = str(value).strip()
    if not text: return None
    if text.lower() == "transparent": return (0, 0, 0, 0.0)
    match = re.fullmatch(r"rgba?\(([^)]*)\)", text, re.I)
    if match:
        parts = [part.strip() for part in match.group(1).split(",")]
        if len(parts) not in (3, 4): return None
        try:
            channels = [round(float(part[:-1]) * 2.55) if part.endswith("%") else int(float(part))
                        for part in parts[:3]]
            alpha = 1.0 if len(parts) == 3 else (
                float(parts[3][:-1]) / 100 if parts[3].endswith("%") else float(parts[3]))
        except ValueError:
            return None
        return (*(max(0, min(255, channel)) for channel in channels), max(0.0, min(1.0, alpha)))
    if re.fullmatch(r"hsla?\([^)]*\)", text, re.I):
        red, green, blue, alpha = canvas_color(text)
        return (red, green, blue, alpha / 255)
    try:
        from PIL import ImageColor
        parts = ImageColor.getrgb(text)
    except (ImportError, ValueError):
        return None
    channels = tuple(parts) + (255,) * (4 - len(parts))
    return (channels[0], channels[1], channels[2], channels[3] / 255)


def serialize_color(rgba):
    """Chrome's canvas serialization: opaque as hex, translucent as ``rgba()``."""
    red, green, blue, alpha = rgba
    if alpha >= 1: return f"#{red:02x}{green:02x}{blue:02x}"
    return f"rgba({red}, {green}, {blue}, {number_text(round(alpha, 3))})"
