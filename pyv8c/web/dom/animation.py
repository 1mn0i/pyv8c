"""Web Animations timelines: ``document.timeline`` and the interface it hangs from."""
import time

from pyv8 import AttributeSpec, IdlSpec

from ...engine.features import interface

__all__ = ["AnimationTimeline", "DocumentTimeline"]


@interface('AnimationTimeline', None)
class AnimationTimeline:
    __v8_idl_spec__ = IdlSpec(
        attributes=(AttributeSpec(name="currentTime", getter="get_currentTime", side_effect=1),),
        constructor_behavior=0,
        to_string_tag="AnimationTimeline",
        has_constructor=1,
    )

    def __init__(self, **_):
        self._start = time.monotonic()

    def get_currentTime(self):
        """Milliseconds since this timeline started."""
        return round((time.monotonic() - self._start) * 1000.0, 3)


@interface('DocumentTimeline', 'AnimationTimeline')
class DocumentTimeline(AnimationTimeline):
    """The timeline of one document; ``currentTime`` counts from its start."""
