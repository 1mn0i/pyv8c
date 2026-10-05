"""``RTCRtpReceiver.getCapabilities``: the codecs this browser says it can receive.

The tables are what Chrome reports on a desktop build, which is the reference the
rest of the profile is read from; a different build answers with its own list.
"""
import re

import pyv8
from pyv8 import IdlSpec, MethodSpec
from pyv8.flag import FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, missing_error

#: ``(mimeType, clockRate, channels, sdpFmtpLine)`` per codec, in the order Chrome lists
#: them; ``channels`` is ``None`` for a direction that reports none.
AUDIO_CODECS = (
    ("audio/opus", 48000, 2, "minptime=10;useinbandfec=1"),
    ("audio/red", 48000, 2, ""),
    ("audio/G722", 8000, 1, ""),
    ("audio/PCMU", 8000, 1, ""),
    ("audio/PCMA", 8000, 1, ""),
    ("audio/CN", 8000, 1, ""),
    ("audio/telephone-event", 48000, 1, ""),
    ("audio/telephone-event", 8000, 1, ""),
)

def _h264(profile):
    """The two packetization modes Chrome lists for one H264 profile."""
    return tuple(("video/H264", 90000, None,
                  f"level-asymmetry-allowed=1;packetization-mode={mode}"
                  f";profile-level-id={profile}")
                 for mode in (1, 0))


#: The same for the video direction, which reports no channel count.  The H264 entries
#: are split around the AV1 ones because that is the order Chrome lists them in.
VIDEO_CODECS = (
    ("video/VP8", 90000, None, ""),
    ("video/rtx", 90000, None, ""),
    *(("video/VP9", 90000, None, f"profile-id={profile}") for profile in (0, 2, 1, 3)),
    *_h264("42001f"), *_h264("42e01f"), *_h264("4d001f"), *_h264("f4001f"),
    ("video/AV1", 90000, None, "level-idx=5;profile=0;tier=0"),
    ("video/AV1", 90000, None, "level-idx=5;profile=1;tier=0"),
    *_h264("64001f"),
    ("video/H265", 90000, None, "level-id=150;profile-id=1;tier-flag=0;tx-mode=SRST"),
    ("video/H265", 90000, None, "level-id=150;profile-id=2;tier-flag=0;tx-mode=SRST"),
    ("video/red", 90000, None, ""),
    ("video/ulpfec", 90000, None, ""),
    ("video/flexfec-03", 90000, None, "repair-window=10000000"),
)

#: The header extensions, which are the same for both directions but for the first.
AUDIO_EXTENSIONS = (
    "urn:ietf:params:rtp-hdrext:ssrc-audio-level",
    "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
    "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
    "urn:ietf:params:rtp-hdrext:sdes:mid",
)
VIDEO_EXTENSIONS = (
    "urn:ietf:params:rtp-hdrext:toffset",
    "http://www.webrtc.org/experiments/rtp-hdrext/abs-send-time",
    "urn:3gpp:video-orientation",
    "http://www.ietf.org/id/draft-holmer-rmcat-transport-wide-cc-extensions-01",
    "http://www.webrtc.org/experiments/rtp-hdrext/playout-delay",
    "http://www.webrtc.org/experiments/rtp-hdrext/video-content-type",
    "http://www.webrtc.org/experiments/rtp-hdrext/video-timing",
    "http://www.webrtc.org/experiments/rtp-hdrext/color-space",
    "urn:ietf:params:rtp-hdrext:sdes:mid",
    "urn:ietf:params:rtp-hdrext:sdes:rtp-stream-id",
    "urn:ietf:params:rtp-hdrext:sdes:repaired-rtp-stream-id",
)


def _codec(name, clock_rate, channels, sdp):
    """One codec capability, with the members Chrome writes and in its order."""
    entry = {}
    if channels is not None:
        entry["channels"] = channels
    entry["clockRate"] = clock_rate
    entry["mimeType"] = name
    if sdp:
        entry["sdpFmtpLine"] = sdp
    return entry


def _capabilities(codecs, extensions):
    return {"codecs": [_codec(*codec) for codec in codecs],
            "headerExtensions": [{"direction": "sendrecv", "uri": uri}
                                 for uri in extensions]}


@interface('RTCRtpReceiver', None)
class RTCRtpReceiver:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="getCapabilities", callback="fn_getCapabilities", length=1,
                       location=FlagLocation.kInterface,
                       receiver_check=FlagReceiverCheck.kDoNotCheck,
                       cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
        ),
        constructor_behavior=0,
        to_string_tag="RTCRtpReceiver",
        has_constructor=0,
    )

    @staticmethod
    def fn_getCapabilities(_realm, kind=MISSING, *_extra):
        if kind is MISSING:
            raise missing_error('RTCRtpReceiver', 'getCapabilities', 1, 0, constructor=False)
        direction = str(dom_string(kind))
        if direction == "audio":
            return _capabilities(AUDIO_CODECS, AUDIO_EXTENSIONS)
        if direction == "video":
            return _capabilities(VIDEO_CODECS, VIDEO_EXTENSIONS)
        return pyv8.Null


__all__ = ["RTCRtpReceiver"]
