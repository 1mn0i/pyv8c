"""``HTMLCanvasElement`` and its 2D rendering context.

The optional Pillow dependency backs the drawing surface; without it the
context still exposes the full API and simply records nothing.
"""

import base64
import io
import math
import re

import pyv8

from ...idl import interface
from ...foundation.arguments import (MISSING, IdlArgumentError, dictionary, dom_string, integer,
                                     is_nullish, missing_error, number)
from ..page_errors import IndexSizeError, PageTypeError
from .events import EventTarget
from .html_elements import HTMLElement
from .webgl import WebGL2RenderingContext, WebGLRenderingContext


#: ``textBaseline`` accepts these spellings; any other value leaves the state untouched.
_TEXT_BASELINES = frozenset(("alphabetic", "bottom", "hanging", "ideographic", "middle", "top"))

#: ``textAlign`` accepts these spellings, likewise.
_TEXT_ALIGNS = frozenset(("center", "end", "left", "right", "start"))

#: Canvas baselines as Pillow anchor letters; ``top``/``hanging`` map to the ascender.
_BASELINE_ANCHORS = {
    "alphabetic": "s",
    "bottom": "d",
    "hanging": "a",
    "ideographic": "d",
    "middle": "m",
    "top": "a",
}

#: ``textAlign`` as Pillow anchor letters (``start``/``end`` assume LTR).
_ALIGN_ANCHORS = {"center": "m", "end": "r", "left": "l", "right": "r", "start": "l"}

#: Family names CSS serializes without quotes; anything else is quoted.
_GENERIC_FAMILIES = frozenset((
    "cursive", "emoji", "fangsong", "fantasy", "math", "monospace", "sans-serif",
    "serif", "system-ui", "ui-monospace", "ui-rounded", "ui-sans-serif", "ui-serif",
))


#: The context kinds a canvas can answer for; ``experimental-webgl`` is the older spelling.
_CONTEXT_KINDS = ("2d", "webgl", "webgl2")


def _requested_context(kind):
    """The context kind a page asked for, or ``None`` when a canvas has none to give."""
    requested = str(kind).lower()
    if requested == "experimental-webgl": return "webgl"
    return requested if requested in _CONTEXT_KINDS else None


#: ``globalCompositeOperation``'s names, from Blink: twelve composite, then sixteen blend.
_COMPOSITE_OPERATIONS = frozenset((
    "clear", "copy", "source-over", "source-in", "source-out", "source-atop",
    "destination-over", "destination-in", "destination-out", "destination-atop", "xor",
    "lighter"))
_BLEND_OPERATIONS = frozenset((
    "normal", "multiply", "screen", "overlay", "darken", "lighten", "color-dodge",
    "color-burn", "hard-light", "soft-light", "difference", "exclusion", "hue", "saturation",
    "color", "luminosity"))
#: The CSS filter functions the ``filter`` setter accepts.
_FILTER_FUNCTIONS = frozenset((
    "blur", "brightness", "contrast", "drop-shadow", "grayscale", "hue-rotate", "invert",
    "opacity", "saturate", "sepia"))
_COLOR_SPACES = ("srgb", "display-p3")
#: What ``drawImage`` takes first, in the IDL's own order.
_CANVAS_IMAGE_SOURCE = ("CSSImageValue or HTMLImageElement or SVGImageElement or HTMLVideoElement"
                        " or HTMLCanvasElement or ImageBitmap or OffscreenCanvas or VideoFrame")
#: The canvas transform: (a, b, c, d, e, f) reads x' = ax + cy + e.
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply_2d(left, right):
    """Postmultiply Canvas's affine transform, in (a, b, c, d, e, f) order."""
    a, b, c, d, e, f = left
    x, y, z, w, tx, ty = right
    return (a*x + c*y, b*x + d*y, a*z + c*w, b*z + d*w,
            a*tx + c*ty + e, b*tx + d*ty + f)


def _enum_error(interface, member, value, enum_name):
    """What the bindings raise for a value outside a WebIDL enum."""
    return IdlArgumentError(
        f"Failed to execute '{member}' on '{interface}':"
        f" The provided value '{value}' is not a valid enum value of type {enum_name}.")


def _signature_error(interface, member):
    """What the bindings raise when no overload accepts the arguments they were given."""
    return IdlArgumentError(
        f"Failed to execute '{member}' on '{interface}':"
        " No function was found that matched the signature provided.")


def _parses_as_filter(text):
    """Whether CSS would take this ``filter`` value as a filter list."""
    value = text.strip()
    if value == "none":
        return True
    if value.lower() in ("inherit", "initial", "unset", "revert", "revert-layer"):
        return False
    if not value or not value.endswith(")") or value.count("(") != value.count(")"):
        return False
    for entry in value.split(")")[:-1]:
        name, _, arguments = entry.partition("(")
        if name.strip().lower() not in _FILTER_FUNCTIONS or not arguments.strip():
            return False
    return True


def _parses_as_color(value):
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


def _creation_settings(interface, options):
    """What ``getContext('2d', options)`` asked for, as ``getContextAttributes`` reports it."""
    settings = dictionary(options)
    record = {key: settings[key] for key in
              ("alpha", "desynchronized", "willReadFrequently", "colorSpace") if key in settings}
    if "colorSpace" in record and str(record["colorSpace"]) not in _COLOR_SPACES:
        raise _enum_error(interface, "getContext", record["colorSpace"], "PredefinedColorSpace")
    return record


def _is_canvas_image_source(value):
    """Whether ``drawImage`` may take this value; only the canvas ones can also be read."""
    return type(value).__name__ in ("HTMLCanvasElement", "OffscreenCanvas", "HTMLImageElement",
                                    "SVGImageElement", "HTMLVideoElement", "ImageBitmap",
                                    "VideoFrame")


@interface('TextMetrics', None)
class TextMetrics:
    """What ``measureText`` returns: the width, and the boxes that same run sits in.

    ``actualBoundingBox`` is the run's ink and ``fontBoundingBox`` is the face's own ascent
    and descent, both taken from the face the width was measured with, so a page that reads
    them reads what this engine measured rather than a constant.

    ``emHeight*`` and the three baselines stay zero, which is what a page reads from Chrome
    155 as well: a plausible-looking number nothing can be compared against would be worse
    than none.
    """

    def __init__(self, width=0.0, *, actual_left=0.0, actual_right=None,
                 actual_ascent=0.0, actual_descent=0.0,
                 font_ascent=0.0, font_descent=0.0, **_):
        self._width = float(width)
        self._actual_left = float(actual_left)
        self._actual_right = float(width if actual_right is None else actual_right)
        self._actual_ascent = float(actual_ascent)
        self._actual_descent = float(actual_descent)
        self._font_ascent = float(font_ascent)
        self._font_descent = float(font_descent)

    def get_width(self): return self._width
    def get_actualBoundingBoxLeft(self): return self._actual_left
    def get_actualBoundingBoxRight(self): return self._actual_right
    def get_actualBoundingBoxAscent(self): return self._actual_ascent
    def get_actualBoundingBoxDescent(self): return self._actual_descent
    def get_fontBoundingBoxAscent(self): return self._font_ascent
    def get_fontBoundingBoxDescent(self): return self._font_descent
    def get_emHeightAscent(self): return 0.0
    def get_emHeightDescent(self): return 0.0
    def get_hangingBaseline(self): return 0.0
    def get_alphabeticBaseline(self): return 0.0
    def get_ideographicBaseline(self): return 0.0


def _canvas_color(value):
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


@interface('ImageData', None)
class ImageData:
    """A rectangle of RGBA pixels, as ``getImageData`` hands it back."""

    def __init__(self, pixels=b"", width=0, height=0, **_):
        self._width, self._height = int(width), int(height)
        self._bytes = bytes(pixels)
        self._data = pyv8.JSTypeArray(list(pixels), pyv8.ArrayType.Uint8ClampedArray)

    def get_width(self): return self._width
    def get_height(self): return self._height
    def get_data(self): return self._data
    def get_colorSpace(self): return "srgb"
    def get_pixelFormat(self): return "rgba-unorm8"


@interface('ImageBitmap', None)
class ImageBitmap:
    """A decoded image: ``drawImage`` draws it, and ``close`` empties it."""

    def __init__(self, image=None, width=0, height=0, **_):
        self._image = image
        self._width, self._height = int(width), int(height)

    def get_width(self): return self._width
    def get_height(self): return self._height
    def fn_close(self, *_):
        """Emptied, as Chromium empties it: every later read reports 0."""
        self._image = None
        self._width = self._height = 0


#: What ``createImageBitmap`` takes, in the IDL's own order.
_IMAGE_BITMAP_SOURCE = ("HTMLImageElement or SVGImageElement or HTMLVideoElement"
                        " or HTMLCanvasElement or Blob or ImageData or ImageBitmap"
                        " or OffscreenCanvas or VideoFrame")


def _is_bitmap_source(value):
    """Whether ``createImageBitmap`` may take this value."""
    return type(value).__name__ in ("HTMLImageElement", "SVGImageElement", "HTMLVideoElement",
                                    "HTMLCanvasElement", "Blob", "ImageData", "ImageBitmap",
                                    "OffscreenCanvas", "VideoFrame")


def bitmap_source_image(source):
    """The pixels behind an image source, or ``None`` when the engine has none for it."""
    if isinstance(source, ImageBitmap):
        return source._image
    image = getattr(source, "_image", None)
    if image is not None: return image
    context = getattr(source, "_context2d", None)
    if context is not None: return context._image
    if isinstance(source, ImageData):
        if not (source._width and source._height): return None
        try:
            from PIL import Image
            return Image.frombytes("RGBA", (source._width, source._height), source._bytes)
        except (ImportError, ValueError):
            return None
    for name in ("_bytes", "_body", "_buffer"):
        data = getattr(source, name, None)
        if isinstance(data, (bytes, bytearray)) and data:
            try:
                from io import BytesIO
                from PIL import Image
                return Image.open(BytesIO(bytes(data))).convert("RGBA")
            except (ImportError, OSError, ValueError):
                return None
    return None


def _resize_size(options, size):
    """The size ``resizeWidth``/``resizeHeight`` ask for, keeping the aspect ratio."""
    width, height = size
    asked = [options.get("resizeWidth"), options.get("resizeHeight")]
    wanted = [int(value) if value is not None else 0 for value in asked]
    if wanted[0] <= 0 and wanted[1] <= 0: return size
    if wanted[0] <= 0: wanted[0] = max(1, round(width * wanted[1] / height))
    if wanted[1] <= 0: wanted[1] = max(1, round(height * wanted[0] / width))
    return (wanted[0], wanted[1])


def create_image_bitmap(interface, image, arguments, services):
    """Chromium's ``createImageBitmap``: a promise, or a throw before one is made."""
    from ..page_errors import InvalidStateError, PageRangeError
    if image is MISSING:
        raise missing_error(interface, 'createImageBitmap', 1, 0, constructor=False)
    if not _is_bitmap_source(image):
        raise IdlArgumentError(
            f"Failed to execute 'createImageBitmap' on '{interface}':"
            f" The provided value is not of type '({_IMAGE_BITMAP_SOURCE})'.")
    rest, options = list(arguments), {}
    if len(rest) in (1, 5):
        options = dictionary(rest.pop())
    if len(rest) == 4:
        sx, sy, sw, sh = (integer(value, signed=True) for value in rest)
        if sw < 0: sx, sw = sx + sw, -sw
        if sh < 0: sy, sh = sy + sh, -sh
        if sw == 0 or sh == 0:
            raise PageRangeError(
                f"Failed to execute 'createImageBitmap' on '{interface}':"
                f" The crop rect {'height' if sw else 'width'} is 0.")
    elif rest:
        raise _signature_error(interface, 'createImageBitmap')
    else:
        sx = sy = 0
        sw = sh = None
    bitmap = bitmap_source_image(image)
    if bitmap is None:
        raise InvalidStateError(
            f"Failed to execute 'createImageBitmap' on '{interface}':"
            " The image source is not usable.")
    if bitmap.size[0] <= 0 or bitmap.size[1] <= 0:
        raise InvalidStateError(
            f"Failed to execute 'createImageBitmap' on '{interface}':"
            f" The image source's {'width' if bitmap.size[0] <= 0 else 'height'} is 0.")
    if sw is not None: bitmap = bitmap.crop((sx, sy, sx + sw, sy + sh))
    size = _resize_size(options, bitmap.size)
    if size != bitmap.size: bitmap = bitmap.resize(size)
    result = ImageBitmap(bitmap, bitmap.size[0], bitmap.size[1])
    realm = getattr(services, "realm_services", None)
    if realm is None: return result
    promise, resolve, _reject = realm.make_promise()
    resolve(result)
    return promise


def _number_text(value):
    """How Chromium's ``String::Number`` spells a double in its messages."""
    if math.isnan(value): return "NaN"
    if math.isinf(value): return "Infinity" if value > 0 else "-Infinity"
    return f"{value:g}"


def _parse_color(value):
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
        red, green, blue, alpha = _canvas_color(text)
        return (red, green, blue, alpha / 255)
    try:
        from PIL import ImageColor
        parts = ImageColor.getrgb(text)
    except (ImportError, ValueError):
        return None
    channels = tuple(parts) + (255,) * (4 - len(parts))
    return (channels[0], channels[1], channels[2], channels[3] / 255)


def _serialize_color(rgba):
    """Chrome's canvas serialization: opaque as hex, translucent as ``rgba()``."""
    red, green, blue, alpha = rgba
    if alpha >= 1: return f"#{red:02x}{green:02x}{blue:02x}"
    return f"rgba({red}, {green}, {blue}, {_number_text(round(alpha, 3))})"


def _quadratic(start, control, end, steps=24):
    """A quadratic Bézier sampled into points, the start point excluded."""
    return [((1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t * t * end[0],
             (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t * t * end[1])
            for t in (index / steps for index in range(1, steps + 1))]


def _cubic(start, first, second, end, steps=24):
    """A cubic Bézier sampled into points, the start point excluded."""
    return [((1 - t) ** 3 * start[0] + 3 * (1 - t) ** 2 * t * first[0]
             + 3 * (1 - t) * t * t * second[0] + t ** 3 * end[0],
             (1 - t) ** 3 * start[1] + 3 * (1 - t) ** 2 * t * first[1]
             + 3 * (1 - t) * t * t * second[1] + t ** 3 * end[1])
            for t in (index / steps for index in range(1, steps + 1))]


@interface('CanvasGradient', None)
class CanvasGradient:
    """What ``createRadialGradient`` and ``createLinearGradient`` hand back."""

    def __init__(self, kind, coordinates, **_):
        self._kind, self._coordinates = kind, coordinates
        self._stops = []

    def fn_addColorStop(self, offset=MISSING, color=MISSING, *_extra):
        """Record one stop, or refuse the value the way Chromium's parser does."""
        from ..page_errors import IndexSizeError, dom_exception
        for position, value in enumerate((offset, color)):
            if value is MISSING:
                raise missing_error('CanvasGradient', 'addColorStop', 2, position, constructor=False)
        value = float(offset)
        if not 0.0 <= value <= 1.0:
            raise IndexSizeError(
                "Failed to execute 'addColorStop' on 'CanvasGradient':"
                f" The provided value ({_number_text(value)}) is outside the range (0.0, 1.0).")
        if not _parses_as_color(color):
            raise dom_exception(
                "SyntaxError",
                "Failed to execute 'addColorStop' on 'CanvasGradient':"
                f" The value provided ('{color}') could not be parsed as a color.")
        self._stops.append((value, _canvas_color(color)))
        self._stops.sort(key=lambda stop: stop[0])

    def _surface(self, size):
        from PIL import Image
        image = Image.new('RGBA', size)
        if not self._stops:
            return image
        stops = self._stops
        x0, y0, *rest = self._coordinates
        if self._kind == 'linear':
            x1, y1 = rest
            dx, dy = x1 - x0, y1 - y0
            denominator = dx * dx + dy * dy
            if not denominator:
                return image
            def parameter(x, y):
                return ((x - x0) * dx + (y - y0) * dy) / denominator
        else:
            r0, x1, y1, r1 = rest
            dx, dy, dr = x1 - x0, y1 - y0, r1 - r0
            a = dx * dx + dy * dy - dr * dr
            if not a and not dx and not dy and not dr:
                return image
            def parameter(x, y):
                px, py = x - x0, y - y0
                b = -2 * (px * dx + py * dy + r0 * dr)
                c = px * px + py * py - r0 * r0
                if abs(a) < 1e-12:
                    roots = [-c / b] if b else []
                elif b * b - 4 * a * c >= 0:
                    root = math.sqrt(b * b - 4 * a * c)
                    roots = [(-b + root) / (2 * a), (-b - root) / (2 * a)]
                else:
                    roots = []
                return max((t for t in roots if r0 + t * dr >= 0), default=None)
        def color_at(t):
            if t is None:
                return (0, 0, 0, 0)
            if t <= stops[0][0]:
                return stops[0][1]
            for (left, first), (right, second) in zip(stops, stops[1:]):
                if t < right:
                    ratio = (t - left) / (right - left)
                    alpha = first[3] * (1 - ratio) + second[3] * ratio
                    rgb = [round((first[i]*first[3]*(1-ratio) + second[i]*second[3]*ratio)/alpha)
                           if alpha else 0 for i in range(3)]
                    return (*rgb, round(alpha))
            return stops[-1][1]
        image.putdata([color_at(parameter(x + .5, y + .5))
                       for y in range(size[1]) for x in range(size[0])])
        return image



@interface('CanvasRenderingContext2D', None)
class CanvasRenderingContext2D:
    #: Chrome's ``2d`` context also carries ``lineWidth``, ``globalAlpha``, ``miterLimit``,
    #: ``lineDashOffset``, ``imageSmoothingEnabled`` and the three ``shadow*``; the baseline is
    #: missing that mixin, and the engine rejects a declaration that contradicts it, so the fix
    #: is a regenerated document (``tools/extract_idl.py``, needs ``CHROMIUM_ROOT``).

    def __init__(self, canvas):
        self._canvas = canvas; self._fill = "#000000"; self._stroke = "#000000"; self._font = "10px sans-serif"
        self._baseline = "alphabetic"
        self._align = "start"
        #: The faces this context has already opened for drawing, by file, face and size.
        self._font_handles = {}
        self._path = []; self._stack = []
        self._composite = "source-over"; self._filter = "none"
        self._transform = _IDENTITY
        self._shadow = "rgba(0, 0, 0, 0)"
        #: The options ``getContext('2d', ...)`` was given, for ``getContextAttributes``.
        self._creation = {}
        try:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (canvas._width, canvas._height), (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)
        except ImportError: self._image = self._draw = None
    def _reset(self):
        creation = self._creation
        self.__init__(self._canvas)
        self._creation = creation
    def get_canvas(self): return self._canvas
    def get_fillStyle(self): return self._fill
    def set_fillStyle(self, v): self._fill = v if isinstance(v, CanvasGradient) else str(v)
    def get_strokeStyle(self): return self._stroke
    def set_strokeStyle(self, v): self._stroke = v if isinstance(v, CanvasGradient) else str(v)
    def get_font(self):
        """Chrome's serialization: ``14px "Arial"`` reads back as ``14px Arial``."""
        style = self._font_style()
        parts = []
        if style["font-style"] != "normal":
            parts.append(style["font-style"])
        if style["font-weight"] != "400":
            parts.append("bold" if style["font-weight"] == "700" else style["font-weight"])
        parts.append(style["font-size"])
        families = []
        for name in str(style["font-family"]).split(","):
            name = name.strip()
            if not name:
                continue
            named = name.lower() in _GENERIC_FAMILIES
            plain = re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name) is not None
            families.append(name if named or plain else '"%s"' % name)
        parts.append(", ".join(families) or "sans-serif")
        return " ".join(parts)
    def set_font(self, v): self._font = str(v)
    def get_textBaseline(self): return self._baseline
    def set_textBaseline(self, value):
        value = str(dom_string(value))
        if value in _TEXT_BASELINES:
            self._baseline = value
    def get_textAlign(self): return self._align
    def set_textAlign(self, value):
        value = str(dom_string(value))
        if value in _TEXT_ALIGNS:
            self._align = value
    def _text_anchor(self):
        """The Pillow anchor this context's baseline and alignment name."""
        return (_ALIGN_ANCHORS.get(self._align, "l")
                + _BASELINE_ANCHORS.get(self._baseline, "s"))
    def _font_style(self):
        """The style this context's ``font`` asks for, as the layout layer reads one.

        A canvas font is CSS's shorthand: optional slant and weight, a size, the family list.
        """
        text = str(self._font or "")
        match = re.search(r"([0-9]*\.?[0-9]+)\s*(px|pt)", text, re.I)
        size = float(match.group(1)) if match else 10.0
        if match and (match.group(2) or "").lower() == "pt":
            size *= 96 / 72
        words = [word.lower() for word in (text[:match.start()] if match else "").split()]
        names = text[match.end():] if match else ""
        families = [name.strip().strip('"\'') for name in names.split(",")]
        heavy = "bold" in words or any(
            word.replace(".", "", 1).isdigit() and float(word) >= 600 for word in words)
        # CSS prints an integral size without a fractional part: 14px, not 14.0px.
        number = f"{size:.6f}".rstrip("0").rstrip(".") or "0"
        return {
            "font-size": f"{number}px",
            "font-family": ", ".join([name for name in families if name] or ["sans-serif"]),
            "font-weight": "700" if heavy else "400",
            "font-style": "italic" if ("italic" in words or "oblique" in words) else "normal",
        }

    def _font_obj(self):
        """The face this context's ``font`` asks for, as Pillow needs it to draw with.

        The family list goes through the table layout measures with, so the glyphs on the
        surface and the width reported for them come from one face.
        """
        if self._image is None: return None
        from PIL import ImageFont
        from . import layout
        from ...foundation.environment import environment_of
        from ...foundation.system_fonts import file_for
        style = self._font_style()
        environment = environment_of(getattr(self._canvas, "_services", None))
        families = [name.strip() for name in str(style["font-family"]).split(",")]
        record = layout._face_for(families, environment, style["font-weight"], style["font-style"])
        path = None if record is None else file_for(record)
        if path is None:
            return ImageFont.load_default()
        size = max(1, int(round(float(str(style["font-size"])[:-2]))))
        key = (path, getattr(record, "index", 0), size)
        handle = self._font_handles.get(key)
        if handle is None:
            try:
                handle = ImageFont.truetype(path, size, index=getattr(record, "index", 0))
            except Exception:  # noqa: BLE001 - a face that cannot be opened draws default
                return ImageFont.load_default()
            self._font_handles[key] = handle
        return handle
    def fn_getImageData(self, x=MISSING, y=MISSING, sw=MISSING, sh=MISSING, *_extra):
        """The pixels of one rectangle, transparent black outside the canvas."""
        for position, value in enumerate((x, y, sw, sh)):
            if value is MISSING:
                raise missing_error('CanvasRenderingContext2D', 'getImageData', 4, position,
                                    constructor=False)
        left, top = int(math.floor(float(x))), int(math.floor(float(y)))
        width, height = int(float(sw)), int(float(sh))
        if width < 0 or height < 0:
            from ..page_errors import IndexSizeError
            raise IndexSizeError(
                "Failed to execute 'getImageData' on 'CanvasRenderingContext2D':"
                " The source width or height is negative.")
        pixels = (bytes(4 * width * height) if self._image is None else
                  self._image.crop((left, top, left + width, top + height)).tobytes())
        return ImageData(pixels, width, height)

    def fn_createImageData(self, width=MISSING, height=MISSING, *_extra):
        interface = 'CanvasRenderingContext2D'
        if width is MISSING:
            raise missing_error(interface, 'createImageData', 1, 0, constructor=False)
        if isinstance(width, ImageData):
            w, h = width._width, width._height
        else:
            if height is MISSING:
                raise missing_error(interface, 'createImageData', 2, 1, constructor=False)
            w, h = abs(int(float(number(width)))), abs(int(float(number(height))))
        if w == 0 or h == 0:
            raise IndexSizeError(
                "Failed to execute 'createImageData' on 'CanvasRenderingContext2D':"
                " The source width or height is 0.")
        return ImageData(bytes(4 * w * h), w, h)

    def fn_putImageData(self, data=MISSING, dx=MISSING, dy=MISSING, *dirty):
        interface = 'CanvasRenderingContext2D'
        for index, value in enumerate((data, dx, dy)):
            if value is MISSING:
                raise missing_error(interface, 'putImageData', 3, index, constructor=False)
        if not isinstance(data, ImageData):
            raise IdlArgumentError(
                "Failed to execute 'putImageData' on 'CanvasRenderingContext2D':"
                " parameter 1 is not of type 'ImageData'.")
        if self._image is None:
            return
        from PIL import Image
        width, height = data._width, data._height
        pixels = bytes(int(data._data[index]) for index in range(4 * width * height))
        source = Image.frombytes('RGBA', (width, height), pixels)
        x, y = int(float(number(dx))), int(float(number(dy)))
        if len(dirty) >= 4:
            sx, sy, sw, sh = (int(float(number(value))) for value in dirty[:4])
            if sw < 0: sx, sw = sx + sw, -sw
            if sh < 0: sy, sh = sy + sh, -sh
            left, top = max(0, sx), max(0, sy)
            right, bottom = min(width, sx + sw), min(height, sy + sh)
            if right <= left or bottom <= top:
                return
            source = source.crop((left, top, right, bottom))
            x += left; y += top
        # putImageData deliberately ignores the current transform and compositing state.
        self._image.paste(source, (x, y))

    def _device(self, x, y):
        """A user-space point in device space, through the current transform."""
        a, b, c, d, e, f = self._transform
        return (a * float(x) + c * float(y) + e, b * float(x) + d * float(y) + f)

    def _rect(self, x, y, w, h):
        x, y, w, h = float(x), float(y), float(w), float(h)
        points = (self._device(x, y), self._device(x+w, y),
                  self._device(x+w, y+h), self._device(x, y+h))
        return (min(point[0] for point in points), min(point[1] for point in points),
                max(point[0] for point in points), max(point[1] for point in points))

    def _draw_rect_mask(self, draw, x, y, w, h):
        if float(w) == 0 or float(h) == 0:
            return
        box = self._rect(x, y, w, h)
        a, b, c, d, _e, _f = self._transform
        if (abs(b) < 1e-12 and abs(c) < 1e-12) or (abs(a) < 1e-12 and abs(d) < 1e-12):
            # ImageDraw's right/bottom endpoints are inclusive. Canvas rectangles
            # are half-open, so an integral 1x1 rectangle covers exactly one pixel.
            left, top, right, bottom = box
            if all(abs(value - round(value)) < 1e-9 for value in box):
                draw.rectangle((round(left), round(top), round(right)-1, round(bottom)-1), fill=255)
                return
        x, y, w, h = float(x), float(y), float(w), float(h)
        draw.polygon((self._device(x, y), self._device(x+w, y),
                      self._device(x+w, y+h), self._device(x, y+h)), fill=255)

    def _paint_path(self):
        """Fill every subpath with the current fillStyle.  Windings are not distinguished."""
        def polygons(draw):
            for subpath in self._path:
                points = [self._device(x, y) for x, y in subpath]
                if len(points) >= 3:
                    draw.polygon(points, fill=255)
        self._paint(self._fill, polygons)

    def fn_fillRect(self, x=0, y=0, w=0, h=0, *_):
        self._paint(self._fill, lambda draw: self._draw_rect_mask(draw, x, y, w, h))
    def fn_clearRect(self, x=0, y=0, w=0, h=0, *_):
        if self._image is not None:
            from PIL import Image, ImageDraw
            mask = Image.new('L', self._image.size)
            self._draw_rect_mask(ImageDraw.Draw(mask), x, y, w, h)
            self._image.paste((0, 0, 0, 0), mask=mask)
    def _draw_text(self, text, x, y, *, fill, stroke=None):
        """Draw one run at *x*/*y* on the named baseline; the bitmap face has no anchors."""
        font = self._font_obj()
        if font is None:
            return
        point = self._device(x, y)
        anchor = self._text_anchor()
        try:
            if stroke is None:
                self._draw.text(point, text, font=font, fill=fill, anchor=anchor)
            else:
                self._draw.text(point, text, font=font, fill=None, stroke_width=1,
                                stroke_fill=stroke, anchor=anchor)
        except ValueError:
            left, top, right, bottom = font.getbbox(text)
            offset_x = {"l": 0, "m": -(right - left) / 2, "r": -(right - left)}[anchor[0]]
            offset_y = -bottom if anchor[1] == "s" else 0
            corner = (point[0] + offset_x, point[1] + offset_y)
            if stroke is None:
                self._draw.text(corner, text, font=font, fill=fill)
            else:
                self._draw.text(corner, text, font=font, fill=None,
                                stroke_width=1, stroke_fill=stroke)
    def fn_fillText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw_text(str(text), x, y, fill=_canvas_color(self._fill))
    def fn_strokeText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw_text(str(text), x, y, fill=None, stroke=_canvas_color(self._stroke))
    def fn_rect(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *_extra):
        for index, value in enumerate((x, y, width, height)):
            if value is MISSING:
                raise missing_error(type(self).__name__, 'rect', 4, index, constructor=False)
        x, y, width, height = (float(number(value)) for value in (x, y, width, height))
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            return
        self._path.append([(x,y), (x+width,y), (x+width,y+height), (x,y+height), (x,y)])

    def fn_beginPath(self, *_): self._path = []
    def fn_moveTo(self, x=0, y=0, *_):
        if math.isfinite(float(x)) and math.isfinite(float(y)):
            self._path.append([(float(x), float(y))])
    def fn_lineTo(self, x=0, y=0, *_):
        if not math.isfinite(float(x)) or not math.isfinite(float(y)): return
        if not self._path: self.fn_moveTo(x, y)
        else: self._path[-1].append((float(x), float(y)))
    def fn_arc(self, x=0, y=0, r=0, start=0, end=0, anticlockwise=False, *_):
        n = max(8, int(abs(float(end) - float(start)) * 24))
        pts = [
            (float(x) + float(r) * math.cos(float(start) + (float(end) - float(start)) * i / n),
             float(y) + float(r) * math.sin(float(start) + (float(end) - float(start)) * i / n))
            for i in range(n + 1)
        ]
        self._path.append(pts)
    def fn_closePath(self, *_):
        """Close the last subpath; nothing at all when the path is empty."""
        if not self._path: return
        points = self._path[-1]
        if points and points[0] != points[-1]: points.append(points[0])
    def fn_bezierCurveTo(self, cp1x=0, cp1y=0, cp2x=0, cp2y=0, x=0, y=0, *_):
        values = (cp1x, cp1y, cp2x, cp2y, x, y)
        if not all(math.isfinite(float(value)) for value in values): return
        cp1x, cp1y, cp2x, cp2y, x, y = (float(value) for value in values)
        if not self._path: self.fn_moveTo(cp1x, cp1y)
        self._path[-1].extend(_cubic(self._path[-1][-1], (cp1x, cp1y), (cp2x, cp2y), (x, y)))
    def fn_quadraticCurveTo(self, cpx=0, cpy=0, x=0, y=0, *_):
        values = (cpx, cpy, x, y)
        if not all(math.isfinite(float(value)) for value in values): return
        cpx, cpy, x, y = (float(value) for value in values)
        if not self._path: self.fn_moveTo(cpx, cpy)
        self._path[-1].extend(_quadratic(self._path[-1][-1], (cpx, cpy), (x, y)))
    def fn_ellipse(self, x=0, y=0, rx=0, ry=0, rotation=0, start=0, end=0,
                   anticlockwise=False, *_):
        """An ellipse arc; non-finite values are ignored, a negative radius is refused."""
        from ..page_errors import IndexSizeError
        values = (x, y, rx, ry, rotation, start, end)
        if not all(math.isfinite(float(value)) for value in values): return
        x, y, rx, ry, rotation, start, end = (float(value) for value in values)
        for radius, axis in ((rx, "major"), (ry, "minor")):
            if radius < 0:
                raise IndexSizeError(
                    f"Failed to execute 'ellipse' on '{type(self).__name__}':"
                    f" The {axis}-axis radius provided ({_number_text(radius)}) is negative.")
        sweep = end - start
        if anticlockwise:
            while sweep > 0: sweep -= 2 * math.pi
        else:
            while sweep < 0: sweep += 2 * math.pi

        def point(angle):
            cosine, sine = math.cos(angle), math.sin(angle)
            return (x + rx * cosine * math.cos(rotation) - ry * sine * math.sin(rotation),
                    y + rx * cosine * math.sin(rotation) + ry * sine * math.cos(rotation))

        if not self._path: self.fn_moveTo(*point(start))
        steps = max(8, int(abs(sweep) * 24))
        self._path[-1].extend(point(start + sweep * index / steps) for index in range(1, steps + 1))
    def get_shadowColor(self): return self._shadow
    def set_shadowColor(self, value):
        """A colour the parser refuses is ignored, leaving the state as it was."""
        color = _parse_color(value)
        if color is not None: self._shadow = _serialize_color(color)
    def fn_fill(self, *args):
        """Fill the current path; a path argument draws nothing here, as a stub one would."""
        interface = type(self).__name__
        rule = "nonzero"
        if args:
            first = args[0]
            if isinstance(first, str):
                rule = first
            elif isinstance(first, (int, float)) and not isinstance(first, bool):
                raise _signature_error(interface, 'fill')
            elif len(args) > 1 and isinstance(args[1], str):
                rule = args[1]
        if rule not in ("nonzero", "evenodd"):
            raise _enum_error(interface, 'fill', rule, "CanvasFillRule")
        self._paint_path()
    def fn_stroke(self, *_):
        if self._draw:
            for subpath in self._path:
                points = [self._device(x, y) for x, y in subpath]
                if len(points) > 1: self._draw.line(points, fill=_canvas_color(self._stroke), width=1)
    def fn_strokeRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle(self._rect(x, y, w, h),
                                 outline=_canvas_color(self._stroke), width=1)
    def fn_scale(self, x=MISSING, y=MISSING, *_extra):
        """Multiply the transform; a non-finite factor is ignored, as Chromium ignores it."""
        interface = type(self).__name__
        for position, value in enumerate((x, y)):
            if value is MISSING:
                raise missing_error(interface, 'scale', 2, position, constructor=False)
        sx, sy = float(x), float(y)
        if not (math.isfinite(sx) and math.isfinite(sy)): return
        a, b, c, d, e, f = self._transform
        self._transform = (a * sx, b * sx, c * sy, d * sy, e, f)
    def fn_translate(self, x=MISSING, y=MISSING, *_extra):
        for index, value in enumerate((x, y)):
            if value is MISSING:
                raise missing_error('CanvasRenderingContext2D', 'translate', 2, index,
                                    constructor=False)
        self.fn_transform(1, 0, 0, 1, x, y)
    def fn_rotate(self, angle=MISSING, *_extra):
        if angle is MISSING:
            raise missing_error('CanvasRenderingContext2D', 'rotate', 1, 0,
                                constructor=False)
        angle = float(number(angle))
        if not math.isfinite(angle): return
        cosine, sine = math.cos(angle), math.sin(angle)
        self._transform = _multiply_2d(self._transform,
                                       (cosine, sine, -sine, cosine, 0, 0))
    def fn_transform(self, a=MISSING, b=MISSING, c=MISSING, d=MISSING,
                     e=MISSING, f=MISSING, *_extra):
        values = (a, b, c, d, e, f)
        for index, value in enumerate(values):
            if value is MISSING:
                raise missing_error('CanvasRenderingContext2D', 'transform', 6, index,
                                    constructor=False)
        values = tuple(float(number(value)) for value in values)
        if all(math.isfinite(value) for value in values):
            self._transform = _multiply_2d(self._transform, values)
    def fn_setTransform(self, *args):
        if not args:
            self._transform = _IDENTITY
            return
        if len(args) == 1:
            from .matrix import _init_values
            matrix, _ = _init_values(args[0])
            values = (matrix[0], matrix[1], matrix[4], matrix[5], matrix[12], matrix[13])
        else:
            if len(args) < 6:
                raise _signature_error('CanvasRenderingContext2D', 'setTransform')
            values = tuple(float(number(value)) for value in args[:6])
        if all(math.isfinite(value) for value in values):
            self._transform = values
    def fn_resetTransform(self, *_extra):
        self._transform = _IDENTITY
    def fn_getTransform(self, *_extra):
        from .matrix import DOMMatrix
        return DOMMatrix(list(self._transform))
    def fn_createRadialGradient(self, x0=MISSING, y0=MISSING, r0=MISSING,
                              x1=MISSING, y1=MISSING, r1=MISSING, *_extra):
        return self._gradient('radial', (x0, y0, r0, x1, y1, r1))

    def fn_drawImage(self, *args):
        """Draw a source onto this surface, with Blink's overloads and its silences."""
        from ..page_errors import InvalidStateError
        interface = type(self).__name__
        if not args:
            raise missing_error(interface, 'drawImage', 3, 0, constructor=False)
        source, rest = args[0], args[1:]
        if len(rest) == 2:
            sx, sy, sw, sh = 0.0, 0.0, MISSING, MISSING
            dx, dy, dw, dh = float(rest[0]), float(rest[1]), MISSING, MISSING
        elif len(rest) == 4:
            dx, dy, dw, dh = (float(value) for value in rest)
            sx, sy, sw, sh = 0.0, 0.0, MISSING, MISSING
        elif len(rest) == 8:
            sx, sy, sw, sh, dx, dy, dw, dh = (float(value) for value in rest)
        else:
            raise _signature_error(interface, 'drawImage')
        if not _is_canvas_image_source(source):
            raise IdlArgumentError(
                f"Failed to execute 'drawImage' on '{interface}':"
                f" The provided value is not of type '({_CANVAS_IMAGE_SOURCE})'.")
        numbers = [sx, sy, dx, dy] + [value for value in (sw, sh, dw, dh) if value is not MISSING]
        if not all(math.isfinite(value) for value in numbers): return
        if int(getattr(source, '_width', 1)) <= 0 or int(getattr(source, '_height', 1)) <= 0:
            raise InvalidStateError(
                f"Failed to execute 'drawImage' on '{interface}':"
                " The image argument is a canvas element with a width or height of 0.")
        image = bitmap_source_image(source)
        if image is None or self._draw is None or self._image is None: return
        # A negative source side is read as a rectangle drawn the other way round.
        if sw is not MISSING:
            if sw < 0: sx, sw = sx + sw, -sw
            if sh < 0: sy, sh = sy + sh, -sh
            if sw == 0 or sh == 0: return
        width = dw if dw is not MISSING else (sw if sw is not MISSING else int(source._width))
        height = dh if dh is not MISSING else (sh if sh is not MISSING else int(source._height))
        if width == 0 or height == 0: return
        (x0, y0), (x1, y1) = self._device(dx, dy), self._device(dx + width, dy + height)
        box = (round(min(x0, x1)), round(min(y0, y1)), round(max(x0, x1)), round(max(y0, y1)))
        piece = image if sw is MISSING else image.crop(
            (round(sx), round(sy), round(sx + sw), round(sy + sh)))
        size = (max(1, box[2] - box[0]), max(1, box[3] - box[1]))
        if piece.size != size: piece = piece.resize(size)
        self._image.paste(piece, (box[0], box[1]))
    def fn_getContextAttributes(self, *_):
        """The settings ``getContext`` was given, under the dictionary's own defaults."""
        return {
            "alpha": bool(self._creation.get("alpha", True)),
            "desynchronized": bool(self._creation.get("desynchronized", False)),
            "colorSpace": str(self._creation.get("colorSpace", "srgb")),
            "colorType": "unorm8",
            "toneMapping": {"mode": "standard"},
            "willReadFrequently": bool(self._creation.get("willReadFrequently", False)),
        }
    def get_globalCompositeOperation(self): return self._composite
    def set_globalCompositeOperation(self, value):
        """A name outside the two lists is ignored, leaving the state as it was."""
        text = str(dom_string(value))
        if text in _COMPOSITE_OPERATIONS or text in _BLEND_OPERATIONS:
            self._composite = text
    def get_filter(self): return self._filter
    def set_filter(self, value):
        """A value CSS would not parse is ignored; an object is kept as Chromium keeps it."""
        if is_nullish(value): return
        if not isinstance(value, str):
            self._filter = value
        elif _parses_as_filter(str(value)):
            self._filter = str(value)
    def _text_boxes(self, text, style, environment):
        """The boxes ``measureText`` reports, taken from the run that was measured.

        ``actualBoundingBox`` is the glyph ink counted from the origin, leftwards on the left
        as the spec counts it, and ``fontBoundingBox`` the face's own ascent and descent.
        """
        from . import layout
        boxes = {}
        found = layout._font_metrics(style, environment)
        if found is not None:
            boxes["font_ascent"] = found.ascent
            boxes["font_descent"] = found.descent
        run, _record = layout._shaped(text, style, environment)
        if run is None or not text:
            return boxes
        left = right = top = bottom = 0.0
        pen = 0.0
        for advance, (offset_x, offset_y), extent in zip(run.advances, run.offsets, run.extents):
            bearing_x, bearing_y, width, height = extent
            glyph_left = pen + offset_x + bearing_x
            glyph_top = bearing_y + offset_y
            left = min(left, glyph_left)
            right = max(right, glyph_left + width)
            top = max(top, glyph_top)
            bottom = min(bottom, glyph_top + height)
            pen += advance
        boxes.update({"actual_left": -left, "actual_right": right,
                      "actual_ascent": top, "actual_descent": -bottom})
        return boxes

    def fn_measureText(self, text='', *_):
        """A run's width and boxes, from the face and the shaping a box measures with."""
        text = str(text)
        style = self._font_style()
        from . import layout
        from ...foundation.environment import environment_of
        environment = environment_of(getattr(self._canvas, "_services", None))
        width = layout.text_width(text, style, environment)
        return TextMetrics(width, **self._text_boxes(text, style, environment))
    def fn_save(self, *_):
        self._stack.append((self._fill, self._stroke, self._font, self._baseline,
                            self._composite, self._filter, self._transform, self._shadow))
    def fn_restore(self, *_):
        if self._stack:
            (self._fill, self._stroke, self._font, self._baseline,
             self._composite, self._filter, self._transform, self._shadow) = self._stack.pop()

    def _gradient(self, kind, values):
        interface = type(self).__name__
        member = 'createLinearGradient' if kind == 'linear' else 'createRadialGradient'
        for index, value in enumerate(values):
            if value is MISSING:
                raise missing_error(interface, member, len(values), index, constructor=False)
        values = tuple(float(number(value)) for value in values)
        if not all(math.isfinite(value) for value in values):
            raise PageTypeError(f"Failed to execute '{member}' on '{interface}': "
                                "The provided double value is non-finite.")
        if kind == 'radial':
            for index in (2, 5):
                if values[index] < 0:
                    radius = 'r0' if index == 2 else 'r1'
                    raise IndexSizeError(f"Failed to execute '{member}' on '{interface}': "
                                         f"The {radius} provided is less than 0.")
        return CanvasGradient(kind, values)


    def _paint(self, style, draw_mask):
        if self._image is None:
            return
        from PIL import Image, ImageDraw, ImageChops
        mask = Image.new('L', self._image.size)
        draw_mask(ImageDraw.Draw(mask))
        layer = (style._surface(self._image.size) if isinstance(style, CanvasGradient)
                 else Image.new('RGBA', self._image.size, _canvas_color(style)))
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
        self._image.alpha_composite(layer)


    def fn_createLinearGradient(self, x0=MISSING, y0=MISSING, x1=MISSING, y1=MISSING, *_extra):
        return self._gradient('linear', (x0, y0, x1, y1))



@interface('HTMLCanvasElement', 'HTMLElement')
class HTMLCanvasElement(HTMLElement):
    def __init__(self, tag_name="canvas", services=None, **kw):
        super().__init__(tag_name, services=services, **kw)
        self._width = self._dimension(self.attributes.get('width'), 300)
        self._height = self._dimension(self.attributes.get('height'), 150)
        self._context2d=None
        self._context_kind=None; self._webgl=None; self._webgl2=None

    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, v):
        self.attributes['width'] = str(integer(v))
        self._width = self._dimension(self.attributes['width'], 300)
        self._resize()

    def get_height(self): return self._height
    def set_height(self, v):
        self.attributes['height'] = str(integer(v))
        self._height = self._dimension(self.attributes['height'], 150)
        self._resize()

    def fn_getContext(self, kind='', options=None, *_):
        requested = _requested_context(kind)
        if requested is None: return pyv8.Null
        # The first context a canvas answers for fixes its type, as it does in Chrome.
        if self._context_kind not in (None, requested): return pyv8.Null
        self._context_kind = requested
        if requested == '2d':
            if self._context2d is None:
                settings = _creation_settings(type(self).__name__, options)
                self._context2d=CanvasRenderingContext2D(self)
                self._context2d._creation = settings
            return self._context2d
        if requested == 'webgl2':
            if self._webgl2 is None: self._webgl2=WebGL2RenderingContext(self)
            return self._webgl2
        if self._webgl is None: self._webgl=WebGLRenderingContext(self)
        return self._webgl
    def fn_toDataURL(self, kind='image/png', *_):
        surface = self._context2d
        if surface is None:
            # A WebGL canvas draws nothing, so its pixels read back transparent.
            surface = (CanvasRenderingContext2D(self)
                       if self._context_kind in ('webgl', 'webgl2') else None)
            if surface is None:
                self.fn_getContext('2d'); surface = self._context2d
        out=io.BytesIO(); surface._image.save(out, format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii')

    @staticmethod
    def _dimension(value, default):
        match = re.match(r'^[\t\n\f\r ]*\+?(\d+)', str(value or ''))
        if match is None:
            return default
        value = int(match[1])
        return value if value < 2**31 else default


    def _attribute_changed(self, name, old, new):
        if name in ('width', 'height'):
            setattr(self, '_' + name, self._dimension(new, 300 if name == 'width' else 150))
            self._resize()


    def _attribute_changed_here(self, name, old, new):
        if name in ('width', 'height'):
            self._attribute_changed(name, old, new)
        else:
            super()._attribute_changed_here(name, old, new)



@interface('OffscreenCanvasRenderingContext2D', 'CanvasRenderingContext2D')
class OffscreenCanvasRenderingContext2D(CanvasRenderingContext2D):
    """The 2D context an ``OffscreenCanvas`` hands out: the same surface, no element."""


@interface('OffscreenCanvas', 'EventTarget')
class OffscreenCanvas(EventTarget):
    """A canvas with no element behind it: the page keeps the surface for itself."""

    __idl_overrides__ = {
        "interface": {
            "length": 2
        }
    }

    def __init__(self, width=MISSING, height=MISSING, *args, services=pyv8.Undefined,
                 _realm=None, **kwargs):
        if services is pyv8.Undefined:
            services = None
        super().__init__(*args, services=services or _realm, **kwargs)
        if width is MISSING:
            raise missing_error('OffscreenCanvas', 'constructor', 2, 0, constructor=True)
        if height is MISSING:
            raise missing_error('OffscreenCanvas', 'constructor', 2, 1, constructor=True)
        self._width = max(0, int(float(width)))
        self._height = max(0, int(float(height)))
        self._context2d = self._webgl = self._webgl2 = self._context_kind = None

    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, value): self._width = max(0, int(float(value))); self._resize()
    def get_height(self): return self._height
    def set_height(self, value): self._height = max(0, int(float(value))); self._resize()
    def get_oncontextlost(self): return self._get_handler("oncontextlost")
    def set_oncontextlost(self, value): self._set_handler("oncontextlost", value)
    def get_oncontextrestored(self): return self._get_handler("oncontextrestored")
    def set_oncontextrestored(self, value): self._set_handler("oncontextrestored", value)

    def fn_getContext(self, kind='', options=None, *_):
        requested = _requested_context(kind)
        if requested is None:
            return pyv8.Null
        if self._context_kind not in (None, requested): return pyv8.Null
        self._context_kind = requested
        if requested == '2d':
            if self._context2d is None:
                settings = _creation_settings(type(self).__name__, options)
                self._context2d = OffscreenCanvasRenderingContext2D(self)
                self._context2d._creation = settings
            return self._context2d
        if requested == 'webgl2':
            if self._webgl2 is None: self._webgl2 = WebGL2RenderingContext(self)
            return self._webgl2
        if self._webgl is None: self._webgl = WebGLRenderingContext(self)
        return self._webgl

    def fn_convertToBlob(self, options=None, *_):
        """The surface as a PNG, in the promise a caller attaches to."""
        surface = self._context2d
        if surface is None:
            self.fn_getContext('2d'); surface = self._context2d
        out = io.BytesIO()
        if surface._image is not None:
            surface._image.save(out, format='PNG')
        from ..fileapi.blob import Blob
        return self._promise(Blob([out.getvalue()], {"type": "image/png"}))

    def fn_transferToImageBitmap(self, *_):
        """No ``ImageBitmap`` here, so the call answers with nothing to hand over."""
        return None
    def fn_clearElementGeometry(self, *_): return None
    def fn_getElementTransform(self, *_): return None
    def fn_updateElementGeometry(self, *_): return None

    def _promise(self, value):
        realm = getattr(getattr(self, "_services", None), "realm_services", None)
        if realm is None:
            return None
        promise, resolve, _reject = realm.make_promise()
        resolve(value)
        return promise
