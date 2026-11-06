"""CORS-safelisted author request headers."""
import re

_LANGUAGE = re.compile(r'^[0-9A-Za-z *,\-.;=]*$')
_RANGE = re.compile(r'^bytes=([0-9]+)-([0-9]*)$')
_UNSAFE = frozenset('"():<>?@[\\]{}' + ''.join(chr(i) for i in range(32) if i != 9) + '\x7f')
NO_CORS_HEADER_NAMES = frozenset(('accept', 'accept-language', 'content-language', 'content-type'))


def header_size(value):
    try:
        return len(str(value).encode('latin-1'))
    except UnicodeEncodeError:
        return len(str(value).encode('utf-8'))


def safelisted_header(name, value):
    name, value = str(name).lower(), str(value)
    if header_size(value) > 128:
        return False
    if name in ('accept-language', 'content-language'):
        return bool(_LANGUAGE.fullmatch(value))
    if name in ('accept', 'content-type') and any(char in _UNSAFE for char in value):
        return False
    if name == 'accept':
        return True
    if name == 'content-type':
        return value.split(';', 1)[0].strip().lower() in (
            'application/x-www-form-urlencoded', 'multipart/form-data', 'text/plain')
    if name == 'range':
        match = _RANGE.fullmatch(value)
        return bool(match and (not match.group(2) or int(match.group(1)) <= int(match.group(2))))
    return False
