"""Canvas gradient implementation."""

import math
from ....idl import interface
from ....foundation.arguments import MISSING, missing_error
from ...page_errors import IndexSizeError
from .colors import number_text, parses_as_color, canvas_color


@interface('CanvasGradient', None)
class CanvasGradient:
    """What ``createRadialGradient`` and ``createLinearGradient`` hand back."""

    def __init__(self, kind, coordinates, **_):
        self._kind, self._coordinates = kind, coordinates
        self._stops = []

    def fn_addColorStop(self, offset=MISSING, color=MISSING, *_extra):
        """Record one stop, or refuse the value the way Chromium's parser does."""
        from ...page_errors import dom_exception
        for position, value in enumerate((offset, color)):
            if value is MISSING:
                raise missing_error('CanvasGradient', 'addColorStop', 2, position, constructor=False)
        value = float(offset)
        if not 0.0 <= value <= 1.0:
            raise IndexSizeError(
                "Failed to execute 'addColorStop' on 'CanvasGradient':"
                f" The provided value ({number_text(value)}) is outside the range (0.0, 1.0).")
        if not parses_as_color(color):
            raise dom_exception(
                "SyntaxError",
                "Failed to execute 'addColorStop' on 'CanvasGradient':"
                f" The value provided ('{color}') could not be parsed as a color.")
        self._stops.append((value, canvas_color(color)))
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
