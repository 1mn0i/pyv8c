"""Current-path geometry, separate from the rasterizer's curve sampling.

Hit tests intersect lines and monotone curve intervals with a horizontal ray.
Curves retain their control points instead of testing a coarse polygon.
"""
import math
import struct


def _scalar(value):
    # SkPath and its query point use single-precision coordinates.
    try:
        return struct.unpack('f', struct.pack('f', value))[0]
    except OverflowError:
        return math.copysign(math.inf, value)


def _point(point):
    return tuple(_scalar(value) for value in point)


def _near(a, b, scale):
    return abs(a - b) <= 8 * math.ulp(scale)


def _opposite(a, b):
    first, second = a[0]*b[1], a[1]*b[0]
    tolerance = max(4 * (math.ulp(first) + math.ulp(second)),
                    32 * math.ulp(math.hypot(*a) * math.hypot(*b)))
    return abs(first - second) <= tolerance and all(x*y <= 0 for x, y in zip(a, b))


def _quadratic_roots(a, b, c):
    if a == 0:
        return [] if b == 0 else [-c / b]
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return []
    root = math.sqrt(discriminant)
    q = -.5 * (b + math.copysign(root, b))
    return [-b / (2 * a)] if q == 0 else [q / a, c / q]


class _Bezier:
    def __init__(self, points):
        self.points = tuple(points)

    def point(self, t):
        points = self.points
        while len(points) > 1:
            points = tuple(((1 - t) * a[0] + t * b[0],
                            (1 - t) * a[1] + t * b[1])
                           for a, b in zip(points, points[1:]))
        return points[0]

    def extrema(self, axis):
        p = [point[axis] for point in self.points]
        if len(p) == 2:
            return []
        if len(p) == 3:
            denominator = p[0] - 2 * p[1] + p[2]
            roots = [(p[0] - p[1]) / denominator] if denominator else []
        else:
            roots = _quadratic_roots(-p[0] + 3*p[1] - 3*p[2] + p[3],
                                     2*(p[0] - 2*p[1] + p[2]), p[1] - p[0])
        return sorted(set(t for t in roots if 0 < t < 1))

    def tangent(self, t):
        degree = len(self.points) - 1
        points = tuple((degree*(b[0]-a[0]), degree*(b[1]-a[1]))
                       for a, b in zip(self.points, self.points[1:]))
        return _Bezier(points).point(t)

    def samples(self):
        count = 1 if len(self.points) == 2 else 24
        return [self.point(i / count) for i in range(1, count + 1)]


class _Ellipse:
    def __init__(self, center, u, v, start, sweep):
        self.center, self.u, self.v = center, u, v
        self.start, self.sweep = start, sweep
        self.points = tuple(self.point(t) for t in (0, .25, .5, .75, 1))

    def point(self, t):
        angle = self.start + self.sweep * t
        cosine, sine = math.cos(angle), math.sin(angle)
        if abs(cosine) < 1e-15:
            cosine = 0.0
        if abs(sine) < 1e-15:
            sine = 0.0
        return (self.center[0] + self.u[0]*cosine + self.v[0]*sine,
                self.center[1] + self.u[1]*cosine + self.v[1]*sine)

    def extrema(self, axis):
        if not self.sweep:
            return []
        angle = math.atan2(self.v[axis], self.u[axis])
        low, high = sorted((self.start, self.start + self.sweep))
        return sorted((angle + k*math.pi - self.start) / self.sweep
                      for k in range(math.floor((low-angle)/math.pi),
                                     math.ceil((high-angle)/math.pi) + 1)
                      if 0 < (angle + k*math.pi - self.start) / self.sweep < 1)

    def tangent(self, t):
        angle = self.start + self.sweep*t
        sine, cosine = math.sin(angle), math.cos(angle)
        sine = 0.0 if abs(sine) < 1e-15 else sine
        cosine = 0.0 if abs(cosine) < 1e-15 else cosine
        return tuple(self.sweep*(-u*sine + v*cosine)
                     for u, v in zip(self.u, self.v))

    def samples(self):
        count = max(8, int(abs(self.sweep) * 24))
        return [self.point(i / count) for i in range(1, count + 1)]


def _intersections(segment, x, y):
    """Return winding contribution and tangents at boundary intersections."""
    breaks = [0, *segment.extrema(1), 1]
    scale = max((abs(value) for point in segment.points for value in point), default=0.0)
    winding, tangents = 0, []
    for low, high in zip(breaks, breaks[1:]):
        a, b = segment.point(low), segment.point(high)
        if a[1] == b[1]:
            if _near(y, a[1], scale):
                xs = [a[0], b[0], *(segment.point(t)[0] for t in segment.extrema(0)
                                    if low < t < high)]
                if min(xs) <= x <= max(xs) and x != b[0]:
                    tangents.append(segment.tangent(low))
            continue
        if not min(a[1], b[1]) <= y <= max(a[1], b[1]):
            continue
        left, right = low, high
        increasing = b[1] > a[1]
        if y == a[1]:
            root = low
        elif y == b[1]:
            root = high
        elif isinstance(segment, _Bezier) and len(segment.points) == 2:
            root = (y - a[1]) / (b[1] - a[1])
        else:
            for _ in range(60):
                mid = (left + right) / 2
                if (segment.point(mid)[1] < y) == increasing:
                    left = mid
                else:
                    right = mid
            root = (left + right) / 2
        at_x = segment.point(root)[0]
        if _near(at_x, x, scale):
            # Endpoints belong to the next segment. Counting both would make
            # an ordinary corner disappear under even-odd filling.
            if root != high:
                tangents.append(segment.tangent(root))
            continue
        if at_x < x and min(a[1], b[1]) <= y < max(a[1], b[1]):
            winding += 1 if increasing else -1
    return winding, tangents


class Path:
    def __init__(self):
        self.contours = []
        self.current = None
        self.position = None

    def move_to(self, point):
        point = _point(point)
        self.current = (point, [])
        self.contours.append(self.current)
        self.position = point

    def _append(self, segment):
        self.current[1].append(segment)
        self.position = segment.point(1)

    def line_to(self, point):
        point = _point(point)
        if self.position is None:
            self.move_to(point)
            return
        if self.current is None:
            self.move_to(self.position)
        self._append(_Bezier((self.position, point)))

    def curve_to(self, *points):
        points = tuple(_point(p) for p in points)
        if self.position is None:
            self.move_to(points[0])
        if self.current is None:
            self.move_to(self.position)
        self._append(_Bezier((self.position, *points)))

    def ellipse(self, center, u, v, start, sweep):
        if u == (0, 0) or v == (0, 0):
            # Blink turns a zero-axis ellipse into lines through each quadrant.
            # Preserve float angle endpoints (including their rounding), rather
            # than replacing this with one line between the two extreme points.
            quarter = _scalar(math.pi/2)
            first, last = _scalar(start), _scalar(start+sweep)
            angles = [first]
            step = quarter if sweep >= 0 else -quarter
            angle = first - math.fmod(first, quarter) + (quarter if sweep >= 0 else 0)
            while first != last and (angle < last if sweep >= 0 else angle > last):
                angles.append(angle)
                angle = _scalar(angle + step)
            angles.append(last)
            for angle in angles:
                cosine, sine = _scalar(math.cos(angle)), _scalar(math.sin(angle))
                self.line_to(tuple(c + a*cosine + b*sine for c, a, b in zip(center, u, v)))
            return
        segment = _Ellipse(_point(center), _point(u), _point(v), start, sweep)
        self.line_to(segment.point(0))
        if sweep:
            self._append(segment)

    def close(self):
        if self.current is not None:
            self.line_to(self.current[0])
            self.current = None

    def __iter__(self):
        for start, segments in self.contours:
            yield [start, *(p for segment in segments for p in segment.samples())]

    def contains(self, x, y, rule):
        x, y = _scalar(x), _scalar(y)
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        winding, tangents = 0, []
        for start, segments in self.contours:
            support = [start, *(p for segment in segments for p in segment.points)]
            if not all(math.isfinite(v) for p in support for v in p):
                continue
            if not segments:
                continue
            closed = [*segments, _Bezier((segments[-1].point(1), start))]
            for segment in closed:
                contribution, boundary = _intersections(segment, x, y)
                tangents.extend(boundary)
                winding += contribution
        filled = bool(winding % 2) if rule == 'evenodd' else winding != 0
        if filled:
            return True
        if len(tangents) <= 1 or len(tangents) % 2 or rule == 'evenodd':
            return bool(len(tangents) % 2)
        remaining = []
        for tangent in tangents:
            if tangent == (0, 0):
                continue
            opposite = next((i for i, other in enumerate(remaining)
                             if _opposite(tangent, other)), None)
            if opposite is None:
                remaining.append(tangent)
            else:
                remaining.pop(opposite)
        return bool(remaining)
