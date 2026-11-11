"""Canvas text implementation."""

from ....idl import interface


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
