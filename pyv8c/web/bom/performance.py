"""``window.performance``: the clock, and an empty entry list.

The pinned baseline has no ``now``, so the clock is only reachable through
``timeOrigin``.  Marks and measures are accepted and forgotten.
"""

import time

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, dom_string, missing_error
from ..dom.events import EventTarget

#: When this process started: what ``timeOrigin`` and ``now`` count from.
_STARTED = time.time() * 1000.0


@interface('Performance', 'EventTarget')
class Performance(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="timeOrigin", getter="get_timeOrigin", side_effect=1),
            AttributeSpec(name="onresourcetimingbufferfull",
                          getter="get_onresourcetimingbufferfull",
                          setter="set_onresourcetimingbufferfull"),
        ),
        methods=(
            MethodSpec(name="getEntries", callback="fn_getEntries", length=0, side_effect=1),
            MethodSpec(name="getEntriesByName", callback="fn_getEntriesByName", length=1, side_effect=1),
            MethodSpec(name="getEntriesByType", callback="fn_getEntriesByType", length=1, side_effect=1),
            MethodSpec(name="mark", callback="fn_mark", length=1),
            MethodSpec(name="measure", callback="fn_measure", length=1),
            MethodSpec(name="clearMarks", callback="fn_clearMarks", length=0),
            MethodSpec(name="clearMeasures", callback="fn_clearMeasures", length=0),
            MethodSpec(name="clearResourceTimings", callback="fn_clearResourceTimings", length=0),
            MethodSpec(name="setResourceTimingBufferSize", callback="fn_setResourceTimingBufferSize", length=1),
        ),
        constructor_behavior=0,
        to_string_tag="Performance",
        has_constructor=1,
    )

    def get_timeOrigin(self):
        return _STARTED

    def fn_getEntries(self, *_extra):
        return []

    def fn_getEntriesByName(self, name=MISSING, type=pyv8.Undefined, *_extra):
        if name is MISSING:
            raise missing_error('Performance', 'getEntriesByName', 1, 0, constructor=False)
        dom_string(name)
        return []

    def fn_getEntriesByType(self, type=MISSING, *_extra):
        if type is MISSING:
            raise missing_error('Performance', 'getEntriesByType', 1, 0, constructor=False)
        dom_string(type)
        return []

    def fn_mark(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Performance', 'mark', 1, 0, constructor=False)
        dom_string(name)

    def fn_measure(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('Performance', 'measure', 1, 0, constructor=False)
        dom_string(name)

    def fn_clearMarks(self, *_extra): return None
    def fn_clearMeasures(self, *_extra): return None
    def fn_clearResourceTimings(self, *_extra): return None

    def fn_setResourceTimingBufferSize(self, size=MISSING, *_extra):
        if size is MISSING:
            raise missing_error('Performance', 'setResourceTimingBufferSize', 1, 0, constructor=False)

    def get_onresourcetimingbufferfull(self):
        return self._get_handler("onresourcetimingbufferfull")

    def set_onresourcetimingbufferfull(self, value):
        self._set_handler("onresourcetimingbufferfull", value)


__all__ = ["Performance"]
