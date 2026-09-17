"""``HTMLCanvasElement`` and its 2D rendering context.

The optional Pillow dependency backs the drawing surface; without it the
context still exposes the full API and simply records nothing.
"""

import base64
import io
import math
import re

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import dom_string
from .html_elements import HTMLElement

#: ``textBaseline`` accepts these spellings; any other value leaves the state
#: untouched, which is how the canvas state attributes treat unknown values.
_TEXT_BASELINES = frozenset(("alphabetic", "bottom", "hanging", "ideographic", "middle", "top"))


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


@interface('CanvasRenderingContext2D', None)
class CanvasRenderingContext2D:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="canvas", getter="get_canvas", side_effect=1),
                    AttributeSpec(name="fillStyle", getter="get_fillStyle", setter="set_fillStyle"),
                    AttributeSpec(name="strokeStyle", getter="get_strokeStyle", setter="set_strokeStyle"),
                    AttributeSpec(name="font", getter="get_font", setter="set_font"),
                    AttributeSpec(name="textBaseline", getter="get_textBaseline", setter="set_textBaseline")),
        methods=(
            MethodSpec(name="fillRect", callback="fn_fillRect"),
            MethodSpec(name="clearRect", callback="fn_clearRect"),
            MethodSpec(name="fillText", callback="fn_fillText"),
            MethodSpec(name="strokeText", callback="fn_strokeText"),
            MethodSpec(name="beginPath", callback="fn_beginPath"),
            MethodSpec(name="moveTo", callback="fn_moveTo"),
            MethodSpec(name="lineTo", callback="fn_lineTo"),
            MethodSpec(name="arc", callback="fn_arc"),
            MethodSpec(name="stroke", callback="fn_stroke"),
            MethodSpec(name="strokeRect", callback="fn_strokeRect"),
            MethodSpec(name="measureText", callback="fn_measureText"),
            MethodSpec(name="save", callback="fn_save"),
            MethodSpec(name="restore", callback="fn_restore"),
        ),
        constructor_behavior=0, has_constructor=0,
    )
    def __init__(self, canvas):
        self._canvas = canvas; self._fill = "#000000"; self._stroke = "#000000"; self._font = "10px sans-serif"
        self._baseline = "alphabetic"
        self._path = []; self._stack = []
        try:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (canvas._width, canvas._height), (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)
        except ImportError: self._image = self._draw = None
    def _reset(self):
        if self._image is not None:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGBA", (self._canvas._width, self._canvas._height), (0, 0, 0, 0))
            self._draw = ImageDraw.Draw(self._image)
    def get_canvas(self): return self._canvas
    def get_fillStyle(self): return self._fill
    def set_fillStyle(self, v): self._fill = str(v)
    def get_strokeStyle(self): return self._stroke
    def set_strokeStyle(self, v): self._stroke = str(v)
    def get_font(self): return self._font
    def set_font(self, v): self._font = str(v)
    def get_textBaseline(self): return self._baseline
    def set_textBaseline(self, value):
        value = str(dom_string(value))
        if value in _TEXT_BASELINES:
            self._baseline = value
    def _font_obj(self):
        if self._image is None: return None
        from PIL import ImageFont
        m = re.search(r"([0-9.]+)\s*(px|pt)", self._font); size = float(m.group(1)) if m else 10
        if m and m.group(2) == 'pt': size *= 96 / 72
        family = self._font[m.end():].strip().split(',')[0].strip('"\' ') if m else ''
        candidates = [family, "arial", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
        for f in candidates:
            try:
                if f: return ImageFont.truetype(f, max(1, int(size)))
            except OSError: pass
        return ImageFont.load_default()
    def fn_fillRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=_canvas_color(self._fill))
    def fn_clearRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw: self._draw.rectangle((float(x), float(y), float(x)+float(w), float(y)+float(h)), fill=(0,0,0,0))
    def fn_fillText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw.text(
                (float(x), float(y)), str(text), font=self._font_obj(),
                fill=_canvas_color(self._fill), anchor='ls',
            )
    def fn_strokeText(self, text='', x=0, y=0, *_):
        if self._draw:
            self._draw.text(
                (float(x), float(y)), str(text), font=self._font_obj(),
                fill=None, stroke_width=1, stroke_fill=_canvas_color(self._stroke), anchor='ls',
            )
    def fn_beginPath(self, *_): self._path = []
    def fn_moveTo(self, x=0, y=0, *_): self._path.append([(float(x), float(y))])
    def fn_lineTo(self, x=0, y=0, *_):
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
    def fn_stroke(self, *_):
        if self._draw:
            for p in self._path:
                if len(p) > 1: self._draw.line(p, fill=_canvas_color(self._stroke), width=1)
    def fn_strokeRect(self, x=0, y=0, w=0, h=0, *_):
        if self._draw:
            self._draw.rectangle(
                (float(x), float(y), float(x) + float(w), float(y) + float(h)),
                outline=_canvas_color(self._stroke), width=1,
            )
    def fn_measureText(self, text='', *_):
        class Metrics: pass
        m = Metrics()
        m.width = self._draw.textlength(str(text), font=self._font_obj()) if self._draw else len(str(text))*10
        return m
    def fn_save(self, *_): self._stack.append((self._fill, self._stroke, self._font, self._baseline))
    def fn_restore(self, *_):
        if self._stack: self._fill, self._stroke, self._font, self._baseline = self._stack.pop()


@interface('HTMLCanvasElement', 'HTMLElement')
class HTMLCanvasElement(HTMLElement):
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="width", getter="get_width", setter="set_width"),
                    AttributeSpec(name="height", getter="get_height", setter="set_height")),
        methods=(MethodSpec(name="getContext", callback="fn_getContext"),
                 MethodSpec(name="toDataURL", callback="fn_toDataURL")),
        constructor_behavior=0, has_constructor=1,
    )
    def __init__(self, tag_name="canvas", services=None, **kw):
        super().__init__(tag_name, services=services, **kw); self._width=300; self._height=150; self._context2d=None
    def _resize(self):
        if self._context2d: self._context2d._reset()
    def get_width(self): return self._width
    def set_width(self, v): self._width=max(0, int(float(v))); self.attributes['width']=str(self._width); self._resize()
    def get_height(self): return self._height
    def set_height(self, v):
        self._height=max(0, int(float(v))); self.attributes['height']=str(self._height); self._resize()
    def fn_getContext(self, kind='', *_):
        if str(kind).lower() != '2d': return pyv8.Null
        if self._context2d is None: self._context2d=CanvasRenderingContext2D(self)
        return self._context2d
    def fn_toDataURL(self, kind='image/png', *_):
        if self._context2d is None: self.fn_getContext('2d')
        out=io.BytesIO(); self._context2d._image.save(out, format='PNG')
        return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii')
