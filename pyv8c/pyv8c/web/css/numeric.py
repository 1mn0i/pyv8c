"""Bounded parsing of dimensionless CSS math for integer media features."""
import math
import re


_INTEGER = re.compile(r'[+-]?[0-9]+\Z')
_TOKEN = re.compile(r'([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:e[+-]?[0-9]+)?)'
                    r'|(-?[a-z_][a-z0-9_-]*)|([()+*/,-])', re.I)
_GAP = re.compile(r'(?:[ \t\r\n\f]+|/\*.*?\*/)+', re.S)
_UNITS = {'px': (1, 'length'), 'in': (96, 'length'),
          'cm': (96/2.54, 'length'), 'mm': (96/25.4, 'length'),
          'q': (96/101.6, 'length'), 'pt': (96/72, 'length'),
          'pc': (16, 'length'), 'em': (16, 'length'), 'rem': (16, 'length'),
          'deg': (1, 'angle'), 'rad': (180/math.pi, 'angle'),
          'grad': (.9, 'angle'), 'turn': (360, 'angle'),
          's': (1, 'time'), 'ms': (.001, 'time')}


def _product(a, b, divide=False):
    amount, units = a
    other, other_units = b
    result_units = dict(units)
    for unit, exponent in other_units.items():
        result_units[unit] = result_units.get(unit, 0) + (-exponent if divide else exponent)
        if not result_units[unit]:
            del result_units[unit]
    if divide and other == 0:
        result = (math.nan if amount == 0 or math.isnan(amount) else
                  math.copysign(math.inf, amount) * math.copysign(1, other))
    else:
        result = amount / other if divide else amount * other
    return result, result_units


class _Parser:
    def __init__(self, text):
        self.text, self.tokens, self.index, self.depth = text, [], 0, 0
        position = 0
        while position < len(text):
            gap = _GAP.match(text, position)
            if gap:
                position = gap.end()
                continue
            token = _TOKEN.match(text, position)
            if token is None or len(self.tokens) >= 1024:
                raise ValueError('invalid or oversized CSS math')
            self.tokens.append((token.group(), token.start(), token.end(), token.lastindex))
            position = token.end()

    def peek(self):
        return self.tokens[self.index][0] if self.index < len(self.tokens) else None

    def take(self, spelling=None):
        if self.index >= len(self.tokens):
            raise ValueError('missing CSS math operand')
        token = self.tokens[self.index]
        if spelling is not None and token[0] != spelling:
            raise ValueError('invalid CSS math separator')
        self.index += 1
        return token

    def expression(self):
        result = self.term()
        while self.peek() in ('+', '-'):
            op, start, end, _kind = self.take()
            # Comments separate tokens but do not supply calc's required whitespace.
            if (start == 0 or end == len(self.text) or
                    self.text[start-1] not in ' \t\r\n\f' or
                    self.text[end] not in ' \t\r\n\f'):
                raise ValueError('CSS sum operator requires whitespace')
            other = self.term()
            if result[1] != other[1]:
                raise ValueError('incompatible CSS dimensions')
            result = (result[0] + other[0] if op == '+' else result[0] - other[0], result[1])
        return result

    def term(self):
        result = self.factor()
        while self.peek() in ('*', '/'):
            op = self.take()[0]
            result = _product(result, self.factor(), divide=op == '/')
        return result

    def factor(self):
        self.depth += 1
        if self.depth > 64:
            raise ValueError('CSS math nesting is too deep')
        try:
            spelling, start, end, kind = self.take()
            if kind == 1:
                result = float(spelling), {}
                if self.index < len(self.tokens):
                    unit, unit_start, _end, unit_kind = self.tokens[self.index]
                    if unit_kind == 2 and unit_start == end:
                        factor, dimension = _UNITS[unit]
                        self.index += 1
                        result = result[0] * factor, {dimension: 1}
                return result
            constants = {'pi': math.pi, 'e': math.e, 'infinity': math.inf,
                         '-infinity': -math.inf, 'nan': math.nan}
            if spelling in constants:
                return constants[spelling], {}
            if spelling == '(':
                result = self.expression()
                self.take(')')
                return result
            if spelling not in ('calc', 'min', 'max', 'clamp'):
                raise ValueError('unsupported CSS math function')
            opening = self.take('(')
            if opening[1] != end:
                raise ValueError('not a CSS function token')
            arguments = [self.expression()]
            while self.peek() == ',':
                self.take(',')
                arguments.append(self.expression())
            self.take(')')
            if any(v[1] != arguments[0][1] for v in arguments):
                raise ValueError('incompatible CSS function dimensions')
            amounts = [v[0] for v in arguments]
            if spelling == 'calc' and len(amounts) == 1:
                return arguments[0]
            if spelling == 'clamp' and len(amounts) != 3:
                raise ValueError('clamp requires three operands')
            if any(math.isnan(v) for v in amounts):
                amount = math.nan
            elif spelling in ('min', 'max'):
                amount = (min if spelling == 'min' else max)(amounts)
            elif spelling == 'clamp':
                amount = max(amounts[0], min(amounts[1], amounts[2]))
            else:
                raise ValueError('invalid CSS function arity')
            return amount, arguments[0][1]
        finally:
            self.depth -= 1


def integer_math(text):
    """A CSS integer (including rounded calc math), or None for invalid syntax."""
    if _INTEGER.fullmatch(text):
        # float handles arbitrary digit strings without Python's int digit limit.
        return float(text)
    if not text.startswith(('calc(', 'min(', 'max(', 'clamp(')):
        return None
    try:
        parser = _Parser(text)
        amount, units = parser.expression()
        if parser.peek() is not None or units:
            return None
        if math.isnan(amount):
            return 0.0
        return float(math.floor(amount + .5)) if math.isfinite(amount) else amount
    except (ValueError, KeyError, OverflowError, RecursionError):
        return None
