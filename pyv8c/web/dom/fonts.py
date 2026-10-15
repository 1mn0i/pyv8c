"""FontFace and FontFaceSet: the document's font faces.

A face loads when the name it asks for is one this machine has, and the answer is the one
Chromium gives.  ``local()`` names a face by its PostScript name and then by its full name
-- never by family -- and a miss rejects the load with a ``NetworkError`` reading "A network
error occurred."; a source that is not a value list is a different failure, a
``SyntaxError`` reading "The source provided ('...') could not be parsed as a value list.",
and it settles the face the moment it is constructed rather than where it is written.

``FontFaceSet.check`` answers true unless a face in the set is still loading, which is
Chromium's rule and not the one its name suggests: a family no face and no platform knows
is not a false.  ``load`` resolves with the faces it matched, which is an empty list when
the family is the platform's.  Which names exist comes from
:mod:`pyv8c.foundation.system_fonts`: this machine's, or the ones an environment profile
stands in for.
"""
from __future__ import annotations

import re

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dictionary, dom_string, is_nullish, missing_error
from ...foundation.environment import environment_of
from ...foundation.system_fonts import lookup_in, normalize, table_of
from ..page_errors import NetworkError, PageTypeError, dom_exception, js_error_value
from .css import parse_declarations, stylesheet_source
from .events import EventTarget

__all__ = ["FontFace", "FontFaceSet", "FontData"]

#: What a failed load says, worded as Chromium's own error table words it.
_NOT_FOUND = "A network error occurred."

#: A family no face and no platform knows, as opposed to a name the platform resolves.
_GENERIC_FAMILIES = frozenset((
    "serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-serif",
    "ui-sans-serif", "ui-monospace", "ui-rounded", "emoji", "math", "fangsong",
))

#: One word of an unquoted ``local()`` name: a CSS identifier, which may not carry ``:``.
_IDENTIFIER = re.compile(r"[A-Za-z0-9_\u0080-\U0010ffff-]+\Z")

#: A CSS string escape, as ``local("Noto\\20 Serif")`` writes one.
_ESCAPE = re.compile(r"\\(?:([0-9A-Fa-f]{1,6})[ \t\n]?|(.))", re.S)

#: Everything a font shorthand may put in front of the family, ending with its size.
_FONT_PREFIX = re.compile(
    r"\s*(?:(?:normal|italic|oblique|small-caps|bold|bolder|lighter"
    r"|[0-9]+(?:\.[0-9]+)?)\s+)*"
    r"(?:[0-9.]+(?:px|pt|pc|in|cm|mm|q|em|rem|ex|ch|vw|vh|vmin|vmax|%)"
    r"|xx-small|x-small|small|medium|large|x-large|xx-large|smaller|larger)"
    r"\s*(?:/\s*[^\s,]+)?\s*",
    re.I,
)


def _descriptor(options, name, default):
    value = options.get(name, default) if options else default
    return default if is_nullish(value) else str(value)


def _end_of_string(text):
    """The index just past the closing quote of a CSS string, or ``None``."""
    quote = text[:1]
    index = 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index + 1
        index += 1
    return None


def _unquote(text):
    """A CSS string without its quotes, with its escapes resolved."""
    return _ESCAPE.sub(
        lambda match: (chr(int(match.group(1), 16)) if match.group(1) else match.group(2)),
        text[1:-1],
    )


def _match_paren(text, start):
    """``(inner, rest)`` for the parenthesized group whose content starts at *start*."""
    depth = 1
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], text[index + 1:]
        index += 1
    return None


def _take_function(text, name):
    """``(inner, rest)`` for ``name(...)`` at the start of *text*, or ``None``."""
    if text[:len(name)].lower() != name or text[len(name):len(name) + 1] != "(":
        return None
    return _match_paren(text, len(name) + 1)


def _local_name(inner):
    """The name a ``local()`` holds: a string as written, or an identifier sequence."""
    text = inner.strip()
    if text[:1] in ("\"", "'"):
        end = _end_of_string(text)
        return None if end != len(text) else _unquote(text)
    words = text.split()
    if not words or not all(_IDENTIFIER.match(word) for word in words):
        return None
    return " ".join(words)


def _take_local(text):
    """``(("local", name), rest)`` for a ``local()`` source, or ``None``."""
    taken = _take_function(text, "local")
    if taken is None:
        return None
    name = _local_name(taken[0])
    return None if name is None else (("local", name), taken[1])


def _take_url(text):
    """``(("url", address), rest)`` for a ``url()`` source with its optional hints."""
    taken = _take_function(text, "url")
    if taken is None:
        return None
    inner = taken[0].strip()
    address = _unquote(inner) if inner[:1] in ("\"", "'") else inner
    if not address:
        return None
    rest = taken[1]
    while True:
        following = None
        for hint in ("format", "tech"):
            following = _take_function(rest.lstrip(), hint)
            if following is not None:
                break
        if following is None:
            return (("url", address), rest)
        rest = following[1]


def _take_string(text):
    """``(("url", value), rest)`` for a bare string source, which CSS reads as a url."""
    if text[:1] not in ("\"", "'"):
        return None
    end = _end_of_string(text)
    return None if end is None else (("url", _unquote(text[:end])), text[end:])


def _parse_sources(source):
    """A ``src`` descriptor as ``((kind, value), ...)``, or ``None`` if it is not a value list.

    Chromium parses this with the CSS parser and refuses anything it does not understand,
    including a ``local()`` name carrying a colon; a refusal is kept as the face's error
    rather than raised, so an unparseable face still exists and fails when it is awaited.
    """
    rest = str(source).strip()
    if not rest:
        return None
    sources = []
    while rest:
        taken = _take_local(rest) or _take_url(rest) or _take_string(rest)
        if taken is None:
            return None
        item, rest = taken
        sources.append(item)
        rest = rest.lstrip()
        if not rest:
            break
        if rest[:1] != ",":
            return None
        rest = rest[1:].strip()
    return tuple(sources) if sources else None


def _source_error(source):
    """The refusal Chromium gives an unparseable ``src``: a ``SyntaxError``, not a TypeError."""
    return dom_exception(
        "SyntaxError",
        f"The source provided ('{source}') could not be parsed as a value list.")


def _font_error(member, value):
    """The refusal Chromium gives a font shorthand it cannot read."""
    return dom_exception(
        "SyntaxError", f"Could not resolve '{value}' as a font.")


def _font_families(shorthand):
    """The family list of a CSS font shorthand, or ``None`` when it is not one.

    A shorthand needs a size before its first family, so ``check('Arial')`` is a refusal
    and ``check('16px Arial')`` is not; the families after a comma carry no size of their
    own, which is what a CSS value list means by writing it that way.
    """
    text = str(shorthand).strip()
    if not text:
        return None
    families = []
    for index, part in enumerate(text.split(",")):
        if index == 0:
            match = _FONT_PREFIX.match(part)
            if match is None:
                return None
            name = part[match.end():].strip()
        else:
            name = part.strip()
        if name[:1] in ("\"", "'"):
            if _end_of_string(name) != len(name):
                return None
            name = _unquote(name)
        if not name:
            return None
        families.append(name)
    return tuple(families) if families else None


#: One ``@font-face`` block, which is the only at-rule this set cares about.
_FONT_FACE_RULE = re.compile(r"@font-face\s*\{([^{}]*)\}", re.I)


def _font_face_rules(document):
    """The document's ``@font-face`` rules as ``(family, source, style, weight, range)``.

    Cached on ``style_version``, the marker the cascade already bumps whenever a style
    element is added, removed or edited, so a page that rewrites its sheet is read once.
    """
    if document is None:
        return ()
    version = getattr(document, "style_version", 0)
    cached = getattr(document, "_font_face_cache", None)
    if cached is not None and cached[0] == version:
        return cached[1]
    rules = []
    for node in document._descendants():
        source = stylesheet_source(node)
        if not source:
            continue
        for block in _FONT_FACE_RULE.findall(source):
            declared = {name: value for name, value, _priority in parse_declarations(block)}
            family = declared.get("font-family", "").strip().strip("\"'")
            face_source = declared.get("src", "").strip()
            if family and face_source:
                rules.append((family, face_source, declared.get("font-style", ""),
                              declared.get("font-weight", ""),
                              declared.get("unicode-range", "")))
    document._font_face_cache = (version, tuple(rules))
    return tuple(rules)


@interface('FontFace', None)
class FontFace:
    def __init__(self, family=MISSING, source=MISSING, descriptors=None, *_extra,
                 services=pyv8.Undefined, _realm=None, **_):
        if family is MISSING or source is MISSING:
            raise missing_error('FontFace', 'FontFace', 2,
                                0 if family is MISSING else 1, constructor=True)
        self._services = services if services is not pyv8.Undefined else _realm
        self._family = str(dom_string(family))
        self._source = str(source)
        options = dictionary(descriptors, name="FontFaceDescriptors") if descriptors else None
        self._style = _descriptor(options, "style", "normal")
        self._weight = _descriptor(options, "weight", "normal")
        self._stretch = _descriptor(options, "stretch", "normal")
        self._unicode_range = _descriptor(options, "unicodeRange", "U+0-10FFFF")
        self._ascent_override = _descriptor(options, "ascentOverride", "normal")
        self._feature_settings = _descriptor(options, "featureSettings", "normal")
        self._variation_settings = _descriptor(options, "variationSettings", "normal")
        self._display = _descriptor(options, "display", "auto")
        #: A source Chromium cannot parse is an error before the face is ever loaded.
        self._sources = _parse_sources(self._source)
        self._error = None if self._sources is not None else _source_error(self._source)
        self._status = "unloaded" if self._sources is not None else "error"
        self._loaded = None
        self._resolve = None
        self._reject = None

    def get_family(self): return self._family
    def get_style(self): return self._style
    def set_style(self, value): self._style = str(dom_string(value))
    def get_weight(self): return self._weight
    def set_weight(self, value): self._weight = str(dom_string(value))
    def get_stretch(self): return self._stretch
    def set_stretch(self, value): self._stretch = str(dom_string(value))
    def get_ascentOverride(self): return self._ascent_override
    def set_ascentOverride(self, value):
        self._ascent_override = str(dom_string(value))
    def get_unicodeRange(self): return self._unicode_range
    def set_unicodeRange(self, value): self._unicode_range = str(dom_string(value))
    def get_featureSettings(self): return self._feature_settings
    def set_featureSettings(self, value): self._feature_settings = str(dom_string(value))
    def get_variationSettings(self): return self._variation_settings
    def set_variationSettings(self, value): self._variation_settings = str(dom_string(value))
    def get_display(self): return self._display
    def set_display(self, value): self._display = str(dom_string(value))
    def get_status(self): return self._status

    def _table(self):
        """The faces to answer with: the environment's, or this machine's."""
        return table_of(environment_of(self._services))

    def _available(self):
        """Whether any source can be satisfied, in the order the sources were written.

        A ``local()`` source asks the machine; a ``url()`` one is a fetch this engine never
        performs, so it answers yes for the reason Chromium's fetch would: the face is there.
        """
        table = self._table()
        for kind, value in self._sources:
            if kind == "url":
                return True
            if lookup_in(table, value) is not None:
                return True
        return False

    def _begin(self):
        """Decide the load now; the promise settles in a task, as Chromium settles it."""
        if not self._available():
            self._error = NetworkError(_NOT_FOUND)
            self._status = "error"
        else:
            self._status = "loaded"
        if self._resolve is None:
            return
        post = getattr(self._services, "post_task", None)
        if post is None:
            self._finish()
        else:
            post(self._finish)

    def _finish(self):
        if self._status == "error":
            self._reject(js_error_value(self._error))
        else:
            self._resolve(self)

    def get_loaded(self):
        if self._loaded is None:
            if self._services is None:
                return pyv8.Undefined
            self._loaded, self._resolve, self._reject = self._services.realm_services.make_promise()
            # A face already settled settles the promise with it, wherever it is asked for.
            if self._status == "loaded":
                self._resolve(self)
            elif self._status == "error":
                self._reject(js_error_value(self._error))
        return self._loaded

    def fn_load(self, *_extra):
        if self._status == "unloaded":
            self._begin()
        return self.get_loaded()


@interface('FontFaceSet', 'EventTarget')
class FontFaceSet(EventTarget):
    #: ``keys``/``values``/``entries`` and ``Symbol.iterator`` hand out pairs from here.
    __idl_overrides__ = {
        "interface": {
            "length": 0,
        }
    }

    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, document=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)
        #: The document whose ``@font-face`` rules are part of this set, when there is one.
        self._document = document
        self._faces = []
        self._ready = None

    def _css_faces(self):
        """The faces the document's own ``@font-face`` rules declare, before any added one.

        They are the set's own faces as far as a page can tell: ``size`` counts them,
        iteration lists them first, and ``load`` settles them like any other, which is
        what Chromium's CSS-connected list means.
        """
        if self._document is None:
            return ()
        version = getattr(self._document, "style_version", 0)
        cached = getattr(self, "_css_faces_cache", None)
        if cached is not None and cached[0] == version:
            return cached[1]
        faces = []
        for family, source, style, weight, unicode_range in _font_face_rules(self._document):
            face = FontFace(family, source, services=self._services)
            if style:
                face.set_style(style)
            if weight:
                face.set_weight(weight)
            if unicode_range:
                face.set_unicodeRange(unicode_range)
            faces.append(face)
        self._css_faces_cache = (version, tuple(faces))
        return tuple(faces)

    def _all_faces(self):
        return self._css_faces() + tuple(self._faces)

    def _face_argument(self, value, member):
        if not isinstance(value, FontFace):
            raise PageTypeError(f"Failed to execute '{member}' on 'FontFaceSet': "
                              "parameter 1 is not of type 'FontFace'.")
        return value

    def _platform_family(self, family):
        """Whether the platform resolves a family, which ``check`` counts as answerable."""
        key = normalize(family)
        return any(normalize(record.family) == key for record in self._table())

    def _table(self):
        return table_of(environment_of(self._services))

    def _matching(self, family):
        key = normalize(family)
        return tuple(face for face in self._all_faces() if normalize(face.get_family()) == key)

    def get_status(self):
        return "loading" if any(face.get_status() == "loading" for face in self._all_faces()) \
            else "loaded"
    def get_size(self): return len(self._all_faces())
    def get_ready(self):
        if self._ready is None:
            self._ready = (self._services.realm_services.promise_resolve(self)
                           if self._services is not None else pyv8.Undefined)
        return self._ready
    def get_onloading(self): return self._get_handler("loading")
    def set_onloading(self, value): self._set_handler("loading", value)
    def get_onloadingdone(self): return self._get_handler("loadingdone")
    def set_onloadingdone(self, value): self._set_handler("loadingdone", value)
    def get_onloadingerror(self): return self._get_handler("loadingerror")
    def set_onloadingerror(self, value): self._set_handler("loadingerror", value)

    def fn_add(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'add', 1, 0, constructor=False)
        face = self._face_argument(face, 'add')
        if face not in self._faces:
            self._faces.append(face)
        return self

    def fn_delete(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'delete', 1, 0, constructor=False)
        face = self._face_argument(face, 'delete')
        if face in self._faces:
            self._faces.remove(face)
            return True
        return False

    def fn_clear(self, *_extra):
        self._faces.clear()

    def fn_has(self, face=MISSING, *_extra):
        if face is MISSING:
            raise missing_error('FontFaceSet', 'has', 1, 0, constructor=False)
        return self._face_argument(face, 'has') in self._all_faces()

    def fn_check(self, font=MISSING, text=MISSING, *_extra):
        if font is MISSING:
            raise missing_error('FontFaceSet', 'check', 1, 0, constructor=False)
        shorthand = str(dom_string(font))
        families = _font_families(shorthand)
        if families is None:
            raise _font_error('check', shorthand)
        for family in families:
            if normalize(family) in _GENERIC_FAMILIES or self._platform_family(family):
                continue
            faces = self._matching(family)
            # Only a face of this set that is still loading can make this a false.
            if faces and any(face.get_status() != "loaded" for face in faces):
                return False
        return True

    def fn_load(self, font=MISSING, text=MISSING, *_extra):
        if font is MISSING:
            raise missing_error('FontFaceSet', 'load', 1, 0, constructor=False)
        shorthand = str(dom_string(font))
        families = _font_families(shorthand)
        if self._services is None:
            return pyv8.Undefined
        if families is None:
            error = _font_error('load', shorthand)
            promise, _resolve, reject = self._services.realm_services.make_promise()
            reject(js_error_value(error))
            return promise
        faces = []
        for family in families:
            for face in self._matching(family):
                if face not in faces:
                    faces.append(face)
        for face in faces:
            face.fn_load()
        return self._services.realm_services.promise_resolve(faces)

    def __v8_iteration_source__(self):
        items = iter(tuple((face, face) for face in self._all_faces()))
        return lambda: next(items, None)


def font_entries(environment=None, selected=None):
    """The faces ``queryLocalFonts`` hands out: the ones that can name themselves.

    Chromium's enumeration skips a face with no PostScript name and one with no style, and
    a caller may ask for a subset by PostScript name.  The table keeps one face per
    PostScript name already, so this applies those two filters and nothing more.
    """
    wanted = None if selected is None else {str(name) for name in selected}
    entries = []
    for record in table_of(environment):
        if not record.postscript_name or not record.style:
            continue
        if wanted is not None and record.postscript_name not in wanted:
            continue
        entries.append(FontData(record.postscript_name, record.full_name,
                                record.family, record.style))
    return entries


@interface('FontData', None)
class FontData:
    """One face as ``queryLocalFonts`` reports it: four names, and never a file."""

    def __init__(self, postscript_name="", full_name="", family="", style="", **_):
        self._postscript_name = str(postscript_name)
        self._full_name = str(full_name)
        self._family = str(family)
        self._style = str(style)

    def get_postscriptName(self): return self._postscript_name
    def get_fullName(self): return self._full_name
    def get_family(self): return self._family
    def get_style(self): return self._style
