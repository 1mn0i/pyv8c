"""``<audio>`` and ``<video>``, with the lists their playback state exposes.
Nothing decodes here, so the classes report what a fresh element reports."""
import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, boolean, dom_string, missing_error, number
from ...foundation.errors import dom_exception
from .events import EventTarget
from .html_elements import HTMLElement, _set_url_value, _url_value

#: Chrome's answers, measured: what a bare type answers, and the codec families it plays
#: in that container.  A container missing here is one it cannot play at all.
_CONTAINERS = {
    "audio/mpeg": ("probably", ("mp3",)),
    "audio/mp3": ("probably", ("mp3",)),
    "audio/aac": ("probably", ()),
    "audio/flac": ("probably", ()),
    "audio/mp4": ("maybe", ("mp4a", "opus", "flac")),
    "audio/ogg": ("maybe", ("vorbis", "opus", "flac")),
    "audio/wav": ("maybe", ("1",)),
    "audio/x-wav": ("maybe", ("1",)),
    "audio/webm": ("maybe", ("vorbis", "opus")),
    "video/mp4": ("maybe", ("avc1", "avc3", "hvc1", "hev1", "vp09", "av01", "mp4a", "opus",
                            "flac")),
    "video/webm": ("maybe", ("vp8", "vp9", "vp09", "av01", "opus", "vorbis")),
    "video/ogg": ("maybe", ("opus", "vorbis", "vp8")),
    "application/ogg": ("maybe", ("vorbis", "opus", "flac")),
}

#: Families Chrome is only sure of once a profile follows the name.
_PROFILE_NEEDED = frozenset({"avc1", "avc3"})


def _media_type(text):
    """Split ``type/subtype; codecs="a, b"`` the way Chrome reads it.

    The container is trimmed and lowered; a codec string is neither, which is why
    ``codecs="MP3"`` is not a codec it plays.
    """
    container, _, parameters = text.partition(";")
    for parameter in parameters.split(";"):
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "codecs":
            return container.strip().lower(), tuple(
                part.strip().strip('"') for part in value.split(",") if part.strip()
            )
    return container.strip().lower(), ()


def _plays(container, codecs):
    answer = _CONTAINERS.get(container)
    if answer is None:
        return ""
    bare, families = answer
    if not codecs:
        return bare
    result = "probably"
    for codec in codecs:
        if not any(codec == family or codec.startswith(family + ".")
                   for family in families):
            return ""
        if codec in _PROFILE_NEEDED:
            result = "maybe"
    return result


@interface('TimeRanges', None)
class TimeRanges:
    """A media element's ranges: nothing has loaded, so there are none."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        methods=(
            MethodSpec(name="start", callback="fn_start", length=1),
            MethodSpec(name="end", callback="fn_end", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="TimeRanges",
        has_constructor=1,
    )

    def get_length(self): return 0
    def fn_start(self, index=MISSING, *_extra): return self._refuse('start', index)
    def fn_end(self, index=MISSING, *_extra): return self._refuse('end', index)

    @staticmethod
    def _refuse(member, index):
        if index is MISSING:
            raise missing_error('TimeRanges', member, 1, 0, constructor=False)
        raise dom_exception("IndexSizeError",
                            f"Failed to execute '{member}' on 'TimeRanges': There is no range"
                            " at that index.")


@interface('TextTrackList', 'EventTarget')
class TextTrackList(EventTarget):
    """A media element's text tracks: with nothing loaded there are none."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="length", getter="get_length", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="TextTrackList",
        has_constructor=1,
    )

    def get_length(self): return 0


@interface('HTMLMediaElement', 'HTMLElement')
class HTMLMediaElement(HTMLElement):
    """``<audio>`` and ``<video>``: the playback state a page reads.  Nothing decodes,
    so it is what a fresh element reports; ``canPlayType`` matches Chrome's answers."""

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="src", getter="get_src", setter="set_src"),
            AttributeSpec(name="currentSrc", getter="get_currentSrc", side_effect=1),
            AttributeSpec(name="crossOrigin", getter="get_crossOrigin", side_effect=1),
            AttributeSpec(name="paused", getter="get_paused", side_effect=1),
            AttributeSpec(name="ended", getter="get_ended", side_effect=1),
            AttributeSpec(name="currentTime", getter="get_currentTime", setter="set_currentTime"),
            AttributeSpec(name="duration", getter="get_duration", side_effect=1),
            AttributeSpec(name="readyState", getter="get_readyState", side_effect=1),
            AttributeSpec(name="networkState", getter="get_networkState", side_effect=1),
            AttributeSpec(name="volume", getter="get_volume", setter="set_volume"),
            AttributeSpec(name="muted", getter="get_muted", setter="set_muted"),
            AttributeSpec(name="loop", getter="get_loop", setter="set_loop"),
            AttributeSpec(name="autoplay", getter="get_autoplay", setter="set_autoplay"),
            AttributeSpec(name="controls", getter="get_controls", setter="set_controls"),
            AttributeSpec(name="defaultPlaybackRate", getter="get_defaultPlaybackRate", side_effect=1),
            AttributeSpec(name="playbackRate", getter="get_playbackRate", setter="set_playbackRate"),
            AttributeSpec(name="seeking", getter="get_seeking", side_effect=1),
            AttributeSpec(name="error", getter="get_error", side_effect=1),
            AttributeSpec(name="preload", getter="get_preload", side_effect=1),
            AttributeSpec(name="textTracks", getter="get_textTracks", side_effect=1),
            AttributeSpec(name="buffered", getter="get_buffered", side_effect=1),
            AttributeSpec(name="played", getter="get_played", side_effect=1),
            AttributeSpec(name="seekable", getter="get_seekable", side_effect=1),
        ),
        methods=(
            MethodSpec(name="canPlayType", callback="fn_canPlayType", length=1, side_effect=1),
            MethodSpec(name="play", callback="fn_play", length=0, side_effect=1),
            MethodSpec(name="pause", callback="fn_pause", length=0),
            MethodSpec(name="load", callback="fn_load", length=0),
        ),
        constructor_behavior=0,
        to_string_tag="HTMLMediaElement",
        has_constructor=1,
    )

    def _media(self, name, default):
        values = getattr(self, "_media_values", None)
        if values is None:
            values = self._media_values = {}
        return values.get(name, default)

    def _set_media(self, name, value):
        self._media(name, None)
        self._media_values[name] = value

    def _flag(self, name): return name in self.attributes
    def _set_flag(self, name, value):
        if boolean(value): self.attributes[name] = ""
        else: self.attributes.pop(name, None)

    def get_src(self): return _url_value(self, "src")
    def set_src(self, value): _set_url_value(self, "src", value)
    def get_currentSrc(self): return ""          # nothing was ever loaded
    def get_crossOrigin(self): return self.attributes.get("crossorigin") or pyv8.Null
    def get_paused(self): return True
    def get_ended(self): return False
    def get_currentTime(self): return float(self._media("currentTime", 0.0))
    def set_currentTime(self, value): self._set_media("currentTime", float(number(value)))
    def get_duration(self): return float("nan")
    def get_readyState(self): return 0
    def get_networkState(self): return 0
    def get_volume(self): return float(self._media("volume", 1.0))
    def set_volume(self, value): self._set_media("volume", float(number(value)))
    def get_muted(self): return self._flag("muted")
    def set_muted(self, value): self._set_flag("muted", value)
    def get_loop(self): return self._flag("loop")
    def set_loop(self, value): self._set_flag("loop", value)
    def get_autoplay(self): return self._flag("autoplay")
    def set_autoplay(self, value): self._set_flag("autoplay", value)
    def get_controls(self): return self._flag("controls")
    def set_controls(self, value): self._set_flag("controls", value)
    def get_defaultPlaybackRate(self): return 1.0
    def get_playbackRate(self): return float(self._media("playbackRate", 1.0))
    def set_playbackRate(self, value): self._set_media("playbackRate", float(number(value)))
    def get_seeking(self): return False
    def get_error(self): return pyv8.Null
    def get_preload(self): return str(self.attributes.get("preload") or "metadata")

    def _empty(self, name, factory):
        value = getattr(self, name, None)
        if value is None:
            value = setattr(self, name, factory())
            value = getattr(self, name)
        return value

    def get_textTracks(self): return self._empty("_text_tracks", TextTrackList)
    def get_buffered(self): return self._empty("_buffered", TimeRanges)
    def get_played(self): return self._empty("_played", TimeRanges)
    def get_seekable(self): return self._empty("_seekable", TimeRanges)

    def fn_canPlayType(self, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('HTMLMediaElement', 'canPlayType', 1, 0, constructor=False)
        container, codecs = _media_type(str(dom_string(type)))
        return _plays(container, codecs)

    def fn_play(self, *_extra):
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(None) if realm is not None else None
    def fn_pause(self, *_extra): return None
    def fn_load(self, *_extra): return None


@interface('HTMLAudioElement', 'HTMLMediaElement')
class HTMLAudioElement(HTMLMediaElement):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, to_string_tag="HTMLAudioElement", has_constructor=1,
    )


@interface('HTMLVideoElement', 'HTMLMediaElement')
class HTMLVideoElement(HTMLMediaElement):
    __v8_idl_spec__ = IdlSpec(
        constructor_behavior=0, to_string_tag="HTMLVideoElement", has_constructor=1,
    )


__all__ = ["TimeRanges", "TextTrackList", "HTMLMediaElement", "HTMLAudioElement", "HTMLVideoElement"]
