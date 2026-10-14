"""The fonts this machine has, as one table.

Chromium answers "is this font installed" twice over and by two different routes: the
renderer's own font stack (DirectWrite, CoreText, fontconfig) settles a ``local()``
source without asking anyone, and the browser process enumerates the system collection
for ``queryLocalFonts()`` behind a permission.  A page reads both, so this table is the
single answer for those two and for measuring text -- they stay in step because they
read the same names.

The names are OpenType's: the PostScript name (name id 6), the full name (id 4), the
family (id 1) and the subfamily (id 2).  A ``local()`` source matches on the first two
only, which is what Chromium's font unique name lookup does, so a lookup here does the
same.  A face inside a collection (``.ttc``) is a record of its own, carrying the index
a page would have to pass to load it, because measuring text needs the same face.

Reading is stdlib and lazy: the table is built on first use, one file at a time, and
only the ``name`` table of each face is read.  A file that cannot be parsed is skipped
and noted once at debug level; nothing here ever raises into a page.
"""
from __future__ import annotations

import os
import struct
import threading
from collections.abc import Iterator
from dataclasses import dataclass

from .logging import logger

__all__ = ["FontRecord", "records", "table_of", "normalize", "file_for", "lookup_in",
           "lookup_local", "match_family", "family_records", "families"]

#: The four names a page can ask a face about, and the two ``local()`` matches on.
_FAMILY, _SUBFAMILY, _FULL, _POSTSCRIPT = 1, 2, 4, 6
_WANTED = frozenset((_FAMILY, _SUBFAMILY, _FULL, _POSTSCRIPT))

_TTC_TAG = b"ttcf"
_TABLE_RECORD = ">4sIII"
_NAME_RECORD = ">6H"

#: Where Windows keeps machine-wide fonts, and the registry key that lists them.
_WINDOWS_FONTS = "Fonts"
_WINDOWS_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"

#: Directories to walk elsewhere; a font the platform manager sees lives in one of these.
_POSIX_ROOTS = (
    "/usr/share/fonts/truetype",
    "/usr/share/fonts/opentype",
    "/usr/local/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
)

_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")


@dataclass(frozen=True)
class FontRecord:
    """One face: what it calls itself, where it lives, and which face of the file it is.

    ``path`` has two forms on purpose.  A record read from this machine carries the absolute
    path the file was found at, which only this machine can use; a record a profile carries
    names the file alone, because a profile travels and an absolute path would neither leave
    nor arrive intact.  :func:`file_for` turns either form into a file here, or into
    ``None``, which is the honest answer for a face whose file this machine does not have.
    """

    postscript_name: str
    full_name: str
    family: str
    style: str
    path: str
    index: int = 0

    def local_names(self) -> tuple[str, ...]:
        """The names ``local()`` matches, in the order the lookup tries them."""
        return (self.postscript_name, self.full_name)


def normalize(value):
    """The one form two spellings of a name agree on: case and runs of space folded away."""
    return " ".join(str(value).split()).casefold()


def _decode(raw, platform, encoding):
    """A name string: Windows and Unicode entries are UTF-16, the old Mac ones are bytes."""
    if platform == 3 or (platform == 0 and encoding in (0, 1, 2, 3, 4, 6)):
        try:
            return raw.decode("utf-16-be").strip()
        except UnicodeDecodeError:
            return ""
    return raw.decode("latin-1").strip()


def _rank(platform, encoding, language):
    """How good a match an entry is: a Windows English name beats a Mac one, which beats none."""
    if platform == 3 or platform == 0:
        return 3 if language in (0x409, 0) else 2
    if platform == 1:
        return 1 if language == 0 else 0
    return 0


def _faces(handle):
    """``(face offset, index)`` for every face in a file: a collection holds several."""
    handle.seek(0)
    header = handle.read(12)
    if len(header) < 12:
        return ()
    if header[:4] != _TTC_TAG:
        return ((0, 0),)
    count = struct.unpack(">I", header[8:12])[0]
    if count == 0 or count > 4096:
        return ()
    table = handle.read(4 * count)
    if len(table) < 4 * count:
        return ()
    return tuple((struct.unpack(">I", table[index * 4:index * 4 + 4])[0], index)
                 for index in range(count))


def _names(handle, face_offset):
    """``name id -> string`` for one face, keeping the best-ranked spelling of each."""
    handle.seek(face_offset)
    directory = handle.read(12)
    if len(directory) < 12:
        return {}
    count = struct.unpack(">H", directory[4:6])[0]
    records = handle.read(16 * count)
    name_offset = None
    for index in range(count):
        chunk = records[index * 16:index * 16 + 16]
        if len(chunk) < 16:
            break
        tag, _checksum, offset, _length = struct.unpack(_TABLE_RECORD, chunk)
        if tag == b"name":
            name_offset = offset
            break
    if name_offset is None:
        return {}
    handle.seek(name_offset)
    header = handle.read(6)
    if len(header) < 6:
        return {}
    _format, count, strings = struct.unpack(">HHH", header)
    table = handle.read(12 * count)
    best: dict[int, tuple[int, str]] = {}
    for index in range(count):
        chunk = table[index * 12:index * 12 + 12]
        if len(chunk) < 12:
            break
        platform, encoding, language, name_id, length, offset = struct.unpack(_NAME_RECORD, chunk)
        if name_id not in _WANTED or length == 0:
            continue
        handle.seek(name_offset + strings + offset)
        text = _decode(handle.read(length), platform, encoding)
        if not text:
            continue
        rank = _rank(platform, encoding, language)
        if name_id not in best or rank > best[name_id][0]:
            best[name_id] = (rank, text)
    return {name_id: text for name_id, (_rank, text) in best.items()}


def _record(path, index, names):
    """One face as a record, or ``None`` when it names itself in neither of the two ways."""
    postscript = names.get(_POSTSCRIPT, "")
    full = names.get(_FULL, "")
    if not postscript and not full:
        return None
    return FontRecord(
        postscript_name=postscript or full,
        full_name=full or postscript,
        family=names.get(_FAMILY) or postscript or full,
        style=names.get(_SUBFAMILY, ""),
        path=str(path),
        index=index,
    )


def roots() -> tuple[str, ...]:
    """The directories this platform keeps fonts in."""
    if os.name == "nt":
        windows = os.environ.get("WINDIR", r"C:\Windows")
        directories = [os.path.join(windows, _WINDOWS_FONTS)]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            directories.append(os.path.join(local, "Microsoft", "Windows", _WINDOWS_FONTS))
        return tuple(directories)
    return _POSIX_ROOTS


def _walk(root) -> Iterator[str]:
    """Every font file under a directory, at most one level below it."""
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        path = os.path.join(root, name)
        if name.lower().endswith(_SUFFIXES) and os.path.isfile(path):
            yield path
        elif os.path.isdir(path):
            try:
                nested = os.listdir(path)
            except OSError:
                continue
            for inner in nested:
                candidate = os.path.join(path, inner)
                if inner.lower().endswith(_SUFFIXES) and os.path.isfile(candidate):
                    yield candidate


def _registered() -> tuple[str, ...]:
    """The font files Windows registers, the ones this user installed first.

    The registry names what the platform's font manager publishes, which is the
    collection Chromium enumerates; a directory listing alone would miss a font
    registered from elsewhere and cannot tell a face index from a file name.
    """
    if os.name != "nt":
        return ()
    import winreg

    found: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, _WINDOWS_KEY) as key:
                count = winreg.QueryInfoKey(key)[1]
                for index in range(count):
                    _name, value, _kind = winreg.EnumValue(key, index)
                    if isinstance(value, str) and value:
                        found.append(value)
        except OSError:
            continue
    return tuple(found)


def _files() -> tuple[str, ...]:
    """Every candidate font file, in the order the index should prefer them."""
    seen: set[str] = set()
    ordered: list[str] = []

    def take(path):
        key = os.path.normcase(path)
        if key not in seen and os.path.isfile(path):
            seen.add(key)
            ordered.append(path)

    for entry in _registered():
        take(entry if os.path.isabs(entry) else os.path.join(roots()[0], entry))
    for root in roots():
        for path in _walk(root):
            take(path)
    return tuple(ordered)


def _build() -> tuple[FontRecord, ...]:
    """Read every candidate file once and keep one record per PostScript name."""
    found: dict[str, FontRecord] = {}
    unreadable = 0
    for path in _files():
        try:
            handle = open(path, "rb")
        except OSError:
            unreadable += 1
            continue
        with handle:
            for face_offset, index in _faces(handle):
                record = _record(path, index, _names(handle, face_offset))
                if record is None:
                    continue
                found.setdefault(normalize(record.postscript_name), record)
    if unreadable:
        logger.debug("system_fonts: {} font files could not be read", unreadable)
    logger.debug("system_fonts: {} faces indexed from {} roots", len(found), len(roots()))
    return tuple(found.values())


_records: tuple[FontRecord, ...] | None = None
_lock = threading.Lock()


_by_name: dict[str, str] | None = None


def _files_by_name() -> dict[str, str]:
    """``lowered file name -> path`` over the roots, read once like the table itself."""
    global _by_name
    if _by_name is None:
        found: dict[str, str] = {}
        for root in roots():
            for path in _walk(root):
                found.setdefault(os.path.basename(path).lower(), path)
        _by_name = found
    return _by_name


def file_for(record) -> str | None:
    """The file a record's face lives in here, or ``None`` when this machine does not have it.

    An absolute path is used as it stands; a file name is resolved through the roots and
    then, failing that, by PostScript name against this machine's own table -- a face whose
    file was renamed is still the same face.  ``None`` is not a failure: the names a page
    asks about are the profile's to answer, and only measurement needs a file.
    """
    if not record.path:
        return None
    if os.path.isabs(record.path):
        return record.path if os.path.isfile(record.path) else None
    found = _files_by_name().get(os.path.basename(record.path).lower())
    if found is not None:
        return found
    key = normalize(record.postscript_name)
    if not key:
        return None
    for machine in records():
        if normalize(machine.postscript_name) == key:
            return machine.path
    return None


def records() -> tuple[FontRecord, ...]:
    """Every face this machine has, read once and kept for the process.

    A profile that carries a font set of its own answers instead of this, and it does so
    from its own view: ``BrowserEnvironment.fonts``.  Keeping the override out of this
    module is what lets two pages of one process present different machines.
    """
    global _records
    if _records is None:
        with _lock:
            if _records is None:
                _records = _build()
    return _records


def table_of(environment) -> tuple[FontRecord, ...]:
    """The faces an environment answers with, or this machine's when it names none.

    The override lives in the environment rather than here so that two pages of one
    process can present two machines: each asks its own profile.
    """
    view = getattr(environment, "fonts", None)
    return records() if view is None else view.resolved()


def lookup_in(table, name) -> FontRecord | None:
    """The face ``local(<name>)`` names among *table*, PostScript name before full name.

    Chromium's unique name lookup tries the PostScript name first and the full name second,
    in a case- and space-folded form; so does this, which is why ``local("Cambria Math")``
    finds a face whose PostScript name has no space in it.
    """
    key = normalize(name)
    if not key:
        return None
    by_postscript: dict[str, FontRecord] = {}
    by_full: dict[str, FontRecord] = {}
    for record in table:
        by_postscript.setdefault(normalize(record.postscript_name), record)
        by_full.setdefault(normalize(record.full_name), record)
    return by_postscript.get(key) or by_full.get(key)


def lookup_local(name) -> FontRecord | None:
    """The face ``local(<name>)`` names on this machine, if any."""
    return lookup_in(records(), name)


def family_records(family) -> tuple[FontRecord, ...]:
    """Every face of one family, in the order the index read them."""
    key = normalize(family)
    return tuple(record for record in records() if normalize(record.family) == key)


def match_family(family) -> FontRecord | None:
    """The first face of a family: what a measurement falls back to."""
    for record in records():
        if normalize(record.family) == normalize(family):
            return record
    return None


def families() -> tuple[str, ...]:
    """Every family name, sorted, as a page would see them listed."""
    return tuple(sorted({record.family for record in records() if record.family}))
