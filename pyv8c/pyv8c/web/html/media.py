"""``<audio>`` and ``<video>``, with the lists their playback state exposes.
Nothing decodes here, so the classes report what a fresh element reports."""
from pyv8c import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, boolean, dom_string, integer, missing_error, number
from ..page_errors import dom_exception
from ..dom.events import EventTarget
from ..media.source import can_play_type, media_facts
from .elements import HTMLElement
from .utils import set_url_value, url_value

#: The loose table and the codec parser live with the strict one, so the two questions cannot
#: drift apart; see :mod:`media_source`.


@interface('TimeRanges', None)
class TimeRanges:
    """A media element's ranges: nothing has loaded, so there are none."""

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

    def get_length(self): return 0


@interface('HTMLMediaElement', 'HTMLElement')
class HTMLMediaElement(HTMLElement):
    """``<audio>`` and ``<video>``: the playback state a page reads.  Nothing decodes,
    so it is what a fresh element reports; ``canPlayType`` matches Chrome's answers."""

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

    def get_src(self): return url_value(self, "src")
    def set_src(self, value):
        set_url_value(self, "src", value)
        self._restart_resource()
    def _attribute_changed_here(self, name, old, new):
        super()._attribute_changed_here(name, old, new)
        if name == "src":
            self._restart_resource()
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
        return can_play_type(dom_string(type), media_facts(self._services))

    def fn_play(self, *_extra):
        realm = getattr(self._services, "realm_services", None)
        return realm.promise_resolve(None) if realm is not None else None
    def fn_pause(self, *_extra): return None
    def fn_load(self, *_extra): return None


@interface('HTMLAudioElement', 'HTMLMediaElement')
class HTMLAudioElement(HTMLMediaElement):
    pass


@interface('HTMLVideoElement', 'HTMLMediaElement')
class HTMLVideoElement(HTMLMediaElement):
    """The video-only faces the IDL declares.

    Nothing decodes here, so the intrinsic size is ``0`` -- Chromium answers
    ``0`` until a frame is available.  ``width``/``height`` are the reflected
    unsigned-long content attributes (an absent one reads back ``0``, and a
    negative assignment stores ``0`` rather than wrapping).  The remaining
    members are ordinary reflected string/boolean content attributes.
    """

    def get_videoWidth(self): return 0
    def get_videoHeight(self): return 0

    def _dimension(self, name):
        digits = ""
        for character in str(self.attributes.get(name, "")).strip():
            if character not in "0123456789":
                break
            digits += character
        return int(digits) if digits else 0

    def _set_dimension(self, name, value):
        self.attributes[name] = str(max(0, integer(value, signed=True)))

    def get_width(self): return self._dimension("width")
    def set_width(self, value): self._set_dimension("width", value)
    def get_height(self): return self._dimension("height")
    def set_height(self, value): self._set_dimension("height", value)

    def get_poster(self): return url_value(self, "poster")
    def set_poster(self, value): set_url_value(self, "poster", value)
    def get_playsInline(self): return self._flag("playsinline")
    def set_playsInline(self, value): self._set_flag("playsinline", value)
    def get_disablePictureInPicture(self):
        return self._flag("disablepictureinpicture")
    def set_disablePictureInPicture(self, value):
        self._set_flag("disablepictureinpicture", value)

__all__ = ["TimeRanges", "TextTrackList", "HTMLMediaElement", "HTMLAudioElement", "HTMLVideoElement"]
