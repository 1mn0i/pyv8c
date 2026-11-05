"""MediaRecorder's MIME capability query for the bundled Chromium profile.

Recording support differs from playback and MediaSource support. This table was
measured against a local Chrome 155 build; it is not generated from the profile.
"""

import re

from ...features.runtime import FeatureImplementation
from ...foundation.arguments import MISSING, dom_string, missing_error
from ...idl import interface
from .events import EventTarget


_CONTAINERS = {
    'audio/webm': frozenset(('opus', 'pcm')),
    'video/webm': frozenset(('vp8', 'vp9', 'avc1', 'avc3', 'h264', 'av01', 'opus', 'pcm')),
    'audio/mp4': frozenset(('mp4a', 'opus')),
    'video/mp4': frozenset(('avc1', 'avc3', 'hvc1', 'hev1', 'av01', 'mp4a', 'vp9', 'opus')),
}
_TYPE = re.compile(r'^(?:audio|video)/[a-z0-9][a-z0-9!#$&^_.+-]*$')
_AV1 = re.compile(r'^av01\.[0-3]\.[0-9]{2}[mh]\.[0-9]{2}(?:\..*)?$')
_HEVC = re.compile(r'^(?:hvc1|hev1)\.[0-9]+\.[0-9]+\.l[0-9]+\.b[0-9a-f]+$')


def _family(codec):
    name = codec.split('.', 1)[0]
    return name if name else None


def _codec_supported(container, codec):
    codec = codec.lower()
    family = _family(codec)
    if family not in _CONTAINERS[container]:
        return False
    if container.endswith('/webm'):
        return codec == family or codec.startswith(family + '.')
    if codec in ('vp9', 'opus', 'avc1', 'avc3', 'av01'):
        return True
    if family in ('avc1', 'avc3'):
        match = re.fullmatch(r'avc[13]\.([0-9a-f]{6})', codec)
        return match is not None and int(match.group(1), 16) != 0
    if family in ('hvc1', 'hev1'):
        return _HEVC.fullmatch(codec) is not None
    if family == 'av01':
        return _AV1.fullmatch(codec) is not None
    if family == 'mp4a':
        return codec == 'mp4a.40.2'
    return False


def recording_type_supported(value):
    """Whether the reference Chrome build accepts one recording MIME type."""
    text = dom_string(value).strip()
    if text == '':
        return True  # An empty type lets Chromium choose its default.
    container, _, parameters = text.partition(';')
    container = container.strip().lower()
    if not _TYPE.fullmatch(container) or container not in _CONTAINERS:
        return False
    codecs = None
    for parameter in parameters.split(';') if parameters else ():
        name, equal, content = parameter.partition('=')
        if equal and name.strip().lower() == 'codecs':
            codecs = content.strip().strip('"').strip()
            break
    if not codecs:
        return True
    parts = [part.strip() for part in codecs.split(',')]
    return all(part and _codec_supported(container, part) for part in parts)


@interface('MediaRecorder', 'EventTarget')
class MediaRecorder(EventTarget):
    @staticmethod
    def fn_isTypeSupported(_realm, mime_type=MISSING, *_extra):
        if mime_type is MISSING:
            raise missing_error('MediaRecorder', 'isTypeSupported', 1, 0,
                                constructor=False)
        return recording_type_supported(mime_type)


FEATURE = FeatureImplementation({'MediaRecorder': MediaRecorder})
