"""``MediaSource.isTypeSupported``: the codecs a media source can carry.

A media source is only supported for a container when the codecs it names are ones
Chrome was built with, so the table is the reference build's codec list.
"""
import re

from pyv8 import IdlSpec, MethodSpec
from pyv8.flag import FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from .events import EventTarget

#: ``audio/*`` or ``video/*`` with the type's own characters: Chrome refuses anything else.
_TYPE = re.compile(r"^(?:audio|video)/[a-z0-9][a-z0-9!#$&^_.+-]*$")

#: The codecs Chrome answers true for, by container; a name matches by its prefix.
CONTAINER_CODECS = {
    "audio/mp4": ("mp4a",),
    "audio/webm": ("opus", "vorbis"),
    "video/mp4": ("avc1", "av01", "mp4a"),
    "video/webm": ("vp8", "vp9", "opus", "vorbis"),
}

#: Types that are complete on their own, so a media source carries them with no codecs.
SELF_CONTAINED = ("audio/mpeg",)


def _codecs(parameters):
    for parameter in parameters.split(";"):
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "codecs":
            return [item.strip().strip('"').lower()
                    for item in value.split(",") if item.strip()]
    return []


@interface('MediaSource', 'EventTarget')
class MediaSource(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="isTypeSupported", callback="fn_isTypeSupported", length=1,
                       location=FlagLocation.kInterface,
                       receiver_check=FlagReceiverCheck.kDoNotCheck,
                       cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
        ),
        constructor_behavior=1,
        length=0,
        to_string_tag="MediaSource",
        has_constructor=1,
    )

    @staticmethod
    def fn_isTypeSupported(_realm, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('MediaSource', 'isTypeSupported', 1, 0, constructor=False)
        name, _, parameters = str(dom_string(type)).partition(";")
        name = name.strip().lower()
        if not _TYPE.match(name):
            return False
        codecs = _codecs(parameters)
        if not codecs:
            return name in SELF_CONTAINED
        prefixes = CONTAINER_CODECS.get(name)
        if not prefixes:
            return False
        return all(codec.startswith(prefixes) for codec in codecs)


__all__ = ["MediaSource"]
