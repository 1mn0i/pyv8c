"""Canvas context2d implementation."""

import math
import re

from pyv8c import pyv8

from ....idl import interface
from ....foundation.arguments import (MISSING, IdlArgumentError, boolean, dom_string,
                                      is_nullish, missing_error, number, sequence)
from ...page_errors import IndexSizeError, PageTypeError
from .bitmap import ImageData, bitmap_source_image, signature_error
from .colors import canvas_color, number_text, parse_color, serialize_color
from .contexts import enum_error
from .gradient import CanvasGradient
from .text import TextMetrics
from ..path import Path as _CanvasPath


_TEXT_BASELINES = frozenset(("alphabetic", "bottom", "hanging", "ideographic", "middle", "top"))


_TEXT_ALIGNS = frozenset(("center", "end", "left", "right", "start"))


_BASELINE_ANCHORS = {
    "alphabetic": "s",
    "bottom": "d",
    "hanging": "a",
    "ideographic": "d",
    "middle": "m",
    "top": "a",
}


_ALIGN_ANCHORS = {"center": "m", "end": "r", "left": "l", "right": "r", "start": "l"}


_GENERIC_FAMILIES = frozenset((
    "cursive", "emoji", "fangsong", "fantasy", "math", "monospace", "sans-serif",
    "serif", "system-ui", "ui-monospace", "ui-rounded", "ui-sans-serif", "ui-serif",
))


_SYSTEM_FONTS = {
    "caption": "16px Arial",
    "icon": "16px Arial",
    "message-box": "16px Arial",
    "menu": '12px "Microsoft YaHei UI"',
    "small-caption": '12px "Microsoft YaHei UI"',
    "status-bar": '12px "Microsoft YaHei UI"',
}


_COMPOSITE_OPERATIONS = frozenset((
    "clear", "copy", "source-over", "source-in", "source-out", "source-atop",
    "destination-over", "destination-in", "destination-out", "destination-atop", "xor",
    "lighter"))


_BLEND_OPERATIONS = frozenset((
    "normal", "multiply", "screen", "overlay", "darken", "lighten", "color-dodge",
    "color-burn", "hard-light", "soft-light", "difference", "exclusion", "hue", "saturation",
    "color", "luminosity"))


_FILTER_FUNCTIONS = frozenset((
    "blur", "brightness", "contrast", "drop-shadow", "grayscale", "hue-rotate", "invert",
    "opacity", "saturate", "sepia"))


_CANVAS_IMAGE_SOURCE = ("CSSImageValue or HTMLImageElement or SVGImageElement or HTMLVideoElement"
                        " or HTMLCanvasElement or ImageBitmap or OffscreenCanvas or VideoFrame")


_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _multiply_2d(left, right):
    """Postmultiply Canvas's affine transform, in (a, b, c, d, e, f) order."""
    a, b, c, d, e, f = left
    x, y, z, w, tx, ty = right
    return (a*x + c*y, b*x + d*y, a*z + c*w, b*z + d*w,
            a*tx + c*ty + e, b*tx + d*ty + f)


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


def _is_canvas_image_source(value):
    """Whether ``drawImage`` may take this value; only the canvas ones can also be read."""
    return type(value).__name__ in ("HTMLCanvasElement", "OffscreenCanvas", "HTMLImageElement",
                                    "SVGImageElement", "HTMLVideoElement", "ImageBitmap",
                                    "VideoFrame")



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
        #: The two line-joining state attributes the IDL declares; Chrome's
        #: defaults are "butt" and "miter" and invalid assignments are ignored.
        self._line_cap = "butt"
        self._line_join = "miter"
        #: The rest of the IDL-declared 2D state Chrome answers on a fresh context.
        self._direction = "inherit"
        self._smoothing_quality = "low"
        self._line_dash = []
        #: The faces this context has already opened for drawing, by file, face and size.
        self._font_handles = {}
        self._path = _CanvasPath(); self._stack = []
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
    def set_font(self, v):
        """CSS's font shorthand: system keywords resolve, unparseable values are ignored."""
        text = str(v).strip()
        keyword = text.lower()
        if keyword in _SYSTEM_FONTS:
            self._font = _SYSTEM_FONTS[keyword]
            return
        if not re.search(r"[0-9]*\.?[0-9]+\s*(px|pt|em|rem|%)", text, re.I):
            # Chrome keeps the previous font when the value is not a valid shorthand.
            return
        self._font = text
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
    def get_lineCap(self): return self._line_cap
    def set_lineCap(self, value):
        value = str(dom_string(value))
        if value in ("butt", "round", "square"):
            self._line_cap = value
    def get_lineJoin(self): return self._line_join
    def set_lineJoin(self, value):
        value = str(dom_string(value))
        if value in ("miter", "round", "bevel"):
            self._line_join = value
    def get_direction(self): return self._direction
    def set_direction(self, value):
        value = str(dom_string(value))
        if value in ("ltr", "rtl", "inherit"):
            self._direction = value
    def get_imageSmoothingQuality(self): return self._smoothing_quality
    def set_imageSmoothingQuality(self, value):
        value = str(dom_string(value))
        if value in ("low", "medium", "high"):
            self._smoothing_quality = value
    def fn_setLineDash(self, segments=MISSING, *_extra):
        """Store the dash pattern.  V8 hands the sequence over as a JSObject, so
        ``sequence()`` is the framework's iterator-aware conversion (a plain
        isinstance check silently saw one opaque object).  Non-finite and
        negative entries are dropped rather than inventing an exception path.
        """
        if segments is MISSING:
            self._line_dash = []
            return None
        values = sequence(segments, convert=lambda item: float(number(item)))
        self._line_dash = [v for v in values if math.isfinite(v) and v >= 0]
        return None
    def fn_getLineDash(self, *_extra):
        return list(self._line_dash)
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
        # Chrome serializes the weight it was given: ``900`` stays ``900``, ``bold`` is 700.
        numeric = next((int(word) for word in words
                        if word.isdigit() and 1 <= int(word) <= 1000), None)
        # CSS prints an integral size without a fractional part: 14px, not 14.0px.
        number = f"{size:.6f}".rstrip("0").rstrip(".") or "0"
        return {
            "font-size": f"{number}px",
            "font-family": ", ".join([name for name in families if name] or ["sans-serif"]),
            "font-weight": (str(numeric) if numeric is not None
                            else ("700" if "bold" in words or "bolder" in words else "400")),
            "font-style": "italic" if ("italic" in words or "oblique" in words) else "normal",
        }

    def _font_obj(self):
        """The face this context's ``font`` asks for, as Pillow needs it to draw with.

        The family list goes through the table layout measures with, so the glyphs on the
        surface and the width reported for them come from one face.
        """
        if self._image is None: return None
        from PIL import ImageFont
        from ...dom import layout
        from ....foundation.environment import environment_of
        from ....foundation.system_fonts import file_for
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
            from ...page_errors import IndexSizeError
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
                points = subpath
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
            self._draw_text(str(text), x, y, fill=canvas_color(self._fill))
    def fn_strokeText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw_text(str(text), x, y, fill=None, stroke=canvas_color(self._stroke))
    def fn_rect(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *_extra):
        x, y, width, height = self._path_numbers('rect', (x, y, width, height))
        if not all(math.isfinite(value) for value in (x, y, width, height)):
            return
        self._path.move_to(self._device(x, y))
        for px, py in ((x+width,y), (x+width,y+height), (x,y+height)):
            self._path.line_to(self._device(px, py))
        self._path.close()

    def fn_beginPath(self, *_): self._path = _CanvasPath()
    def _path_numbers(self, method, values):
        for index, value in enumerate(values):
            if value is MISSING:
                raise missing_error(type(self).__name__, method, len(values), index,
                                    constructor=False)
        return tuple(float(number(value)) for value in values)

    def fn_moveTo(self, x=MISSING, y=MISSING, *_):
        x, y = self._path_numbers('moveTo', (x, y))
        if math.isfinite(x) and math.isfinite(y):
            self._path.move_to(self._device(x, y))
    def fn_lineTo(self, x=MISSING, y=MISSING, *_):
        x, y = self._path_numbers('lineTo', (x, y))
        if not math.isfinite(x) or not math.isfinite(y): return
        self._path.line_to(self._device(x, y))
    def fn_arc(self, x=MISSING, y=MISSING, r=MISSING, start=MISSING, end=MISSING,
               anticlockwise=False, *_):
        x, y, r, start, end = self._path_numbers('arc', (x, y, r, start, end))
        if not all(math.isfinite(value) for value in (x, y, r, start, end)):
            return
        if r < 0:
            raise IndexSizeError(
                f"Failed to execute 'arc' on '{type(self).__name__}':"
                f" The radius provided ({number_text(r)}) is negative.")
        self.fn_ellipse(x, y, r, r, 0, start, end, anticlockwise)
    def fn_closePath(self, *_):
        """Close the last subpath; nothing at all when the path is empty."""
        self._path.close()
    def fn_bezierCurveTo(self, cp1x=MISSING, cp1y=MISSING, cp2x=MISSING,
                         cp2y=MISSING, x=MISSING, y=MISSING, *_):
        self._curve_to('bezierCurveTo', (cp1x, cp1y, cp2x, cp2y, x, y))
    def fn_quadraticCurveTo(self, cpx=MISSING, cpy=MISSING, x=MISSING, y=MISSING, *_):
        self._curve_to('quadraticCurveTo', (cpx, cpy, x, y))
    def _curve_to(self, method, values):
        values = self._path_numbers(method, values)
        if all(math.isfinite(value) for value in values):
            self._path.curve_to(*(self._device(*values[i:i+2]) for i in range(0, len(values), 2)))
    def fn_ellipse(self, x=MISSING, y=MISSING, rx=MISSING, ry=MISSING,
                   rotation=MISSING, start=MISSING, end=MISSING,
                   anticlockwise=False, *_):
        """An ellipse arc; non-finite values are ignored, a negative radius is refused."""
        values = self._path_numbers('ellipse', (x, y, rx, ry, rotation, start, end))
        if not all(math.isfinite(value) for value in values): return
        x, y, rx, ry, rotation, start, end = values
        anticlockwise = boolean(anticlockwise)
        for radius, axis in ((rx, "major"), (ry, "minor")):
            if radius < 0:
                raise IndexSizeError(
                    f"Failed to execute 'ellipse' on '{type(self).__name__}':"
                    f" The {axis}-axis radius provided ({number_text(radius)}) is negative.")
        sweep = end - start
        if abs(sweep) >= math.tau:
            sweep = -math.tau if anticlockwise else math.tau
        elif anticlockwise and sweep > 0:
            sweep -= math.tau
        elif not anticlockwise and sweep < 0:
            sweep += math.tau
        center = self._device(x, y)
        a, b, c, d, _e, _f = self._transform
        if a*d - b*c == 0:
            return
        cosine, sine = math.cos(rotation), math.sin(rotation)
        u = (rx*(a*cosine + c*sine), rx*(b*cosine + d*sine))
        v = (ry*(-a*sine + c*cosine), ry*(-b*sine + d*cosine))
        self._path.ellipse(center, u, v, start % math.tau, sweep)
    def fn_isPointInPath(self, x=MISSING, y=MISSING, fillRule="nonzero", *_extra):
        """Test the current path in canvas coordinates, including implicit closes."""
        interface = type(self).__name__
        x, y = self._path_numbers('isPointInPath', (x, y))
        rule = "nonzero" if fillRule is pyv8.Undefined else str(dom_string(fillRule))
        if rule not in ("nonzero", "evenodd"):
            raise enum_error(interface, 'isPointInPath', rule, "CanvasFillRule")
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        return self._path.contains(x, y, rule)
    def get_shadowColor(self): return self._shadow
    def set_shadowColor(self, value):
        """A colour the parser refuses is ignored, leaving the state as it was."""
        color = parse_color(value)
        if color is not None: self._shadow = serialize_color(color)
    def fn_fill(self, *args):
        """Fill the current path; a path argument draws nothing here, as a stub one would."""
        interface = type(self).__name__
        rule = "nonzero"
        if args:
            first = args[0]
            if isinstance(first, str):
                rule = first
            elif isinstance(first, (int, float)) and not isinstance(first, bool):
                raise signature_error(interface, 'fill')
            elif len(args) > 1 and isinstance(args[1], str):
                rule = args[1]
        if rule not in ("nonzero", "evenodd"):
            raise enum_error(interface, 'fill', rule, "CanvasFillRule")
        self._paint_path()
    def fn_stroke(self, *_):
        if self._draw:
            for subpath in self._path:
                points = subpath
                if len(points) > 1: self._draw.line(points, fill=canvas_color(self._stroke), width=1)
    def fn_strokeRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle(self._rect(x, y, w, h),
                                 outline=canvas_color(self._stroke), width=1)
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
            from ...dom.matrix import init_matrix_values
            matrix, _ = init_matrix_values(args[0])
            values = (matrix[0], matrix[1], matrix[4], matrix[5], matrix[12], matrix[13])
        else:
            if len(args) < 6:
                raise signature_error('CanvasRenderingContext2D', 'setTransform')
            values = tuple(float(number(value)) for value in args[:6])
        if all(math.isfinite(value) for value in values):
            self._transform = values
    def fn_resetTransform(self, *_extra):
        self._transform = _IDENTITY
    def fn_getTransform(self, *_extra):
        from ...dom.matrix import DOMMatrix
        return DOMMatrix(list(self._transform))
    def fn_createRadialGradient(self, x0=MISSING, y0=MISSING, r0=MISSING,
                              x1=MISSING, y1=MISSING, r1=MISSING, *_extra):
        return self._gradient('radial', (x0, y0, r0, x1, y1, r1))

    def fn_drawImage(self, *args):
        """Draw a source onto this surface, with Blink's overloads and its silences."""
        from ...page_errors import InvalidStateError
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
            raise signature_error(interface, 'drawImage')
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
        from ...dom import layout
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
        from ...dom import layout
        from ....foundation.environment import environment_of
        environment = environment_of(getattr(self._canvas, "_services", None))
        run, _ = layout._shaped(text, style, environment)
        width = (sum(math.trunc(advance * 65536) / 65536 for advance in run.advances)
                 if run is not None else layout.text_width(text, style, environment))
        return TextMetrics(width, **self._text_boxes(text, style, environment))
    def fn_save(self, *_):
        self._stack.append((self._fill, self._stroke, self._font, self._baseline,
                            self._composite, self._filter, self._transform, self._shadow,
                            self._line_cap, self._line_join, self._direction,
                            self._smoothing_quality, list(self._line_dash)))
    def fn_restore(self, *_):
        if self._stack:
            (self._fill, self._stroke, self._font, self._baseline,
             self._composite, self._filter, self._transform, self._shadow,
             self._line_cap, self._line_join, self._direction,
             self._smoothing_quality, self._line_dash) = self._stack.pop()

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
                 else Image.new('RGBA', self._image.size, canvas_color(style)))
        layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
        self._image.alpha_composite(layer)


    def fn_createLinearGradient(self, x0=MISSING, y0=MISSING, x1=MISSING, y1=MISSING, *_extra):
        return self._gradient('linear', (x0, y0, x1, y1))
