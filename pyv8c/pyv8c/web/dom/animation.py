"""Web Animations: timelines, effects and the animations an element starts.

The IDL baseline declares ``Animation``/``AnimationEffect``/``KeyframeEffect``
but this module used to ship only the two timelines.  Chrome 157 answers a
freshly created ``Element.animate(...)`` with a running, pending Animation whose
effect is a KeyframeEffect (``phase9_chrome_shape.json``).  Without these
classes the binder filled every member with an ``Undefined`` stub, which is
what the Kasada host ledger saw as an extra nested null.
"""
from __future__ import annotations

import time
import math
import re

from pyv8c import pyv8

from ...foundation.arguments import MISSING, dictionary, dom_string, is_nullish, number
from ...idl import interface
from .events import EventTarget

__all__ = ["AnimationTimeline", "DocumentTimeline", "AnimationEffect",
           "KeyframeEffect", "Animation"]


@interface('AnimationTimeline', None)
class AnimationTimeline:
    def __init__(self, **_):
        self._start = time.monotonic()

    def get_currentTime(self):
        """Milliseconds since this timeline started."""
        return round((time.monotonic() - self._start) * 1000.0, 3)


@interface('DocumentTimeline', 'AnimationTimeline')
class DocumentTimeline(AnimationTimeline):
    """The timeline of one document; ``currentTime`` counts from its start."""


def _options(value):
    if is_nullish(value) or value is MISSING:
        return {}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {"duration": value}
    if isinstance(value, (dict, pyv8.JSObject)):
        return dictionary(value)
    return {}


def _valid_easing(value):
    text = str(dom_string(value)).strip().lower()
    if text in ('linear', 'ease', 'ease-in', 'ease-out', 'ease-in-out', 'step-start', 'step-end'):
        return True
    match = re.fullmatch(r'cubic-bezier\(\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*([^,]+)\s*\)', text)
    if match:
        try:
            values = [float(value) for value in match.groups()]
            return all(math.isfinite(value) for value in values) and 0 <= values[0] <= 1 and 0 <= values[2] <= 1
        except ValueError:
            return False
    match = re.fullmatch(r'steps\(\s*(\d+)\s*(?:,\s*(start|end|jump-start|jump-end|jump-none|jump-both)\s*)?\)', text)
    if match:
        count = int(match.group(1))
        return count > 0 and not (count == 1 and match.group(2) == 'jump-none')
    return bool(re.fullmatch(r'linear\(\s*[-+\d.%\s,]+\)', text))


def _keyframe_list(value):
    from ...foundation.arguments import sequence
    from ..page_errors import PageTypeError
    if is_nullish(value) or value is MISSING:
        return []
    if isinstance(value, pyv8.JSObject):
        if not callable(value._iterator_method()):
            properties = dictionary(value)
            columns = {name: sequence(item) if isinstance(item, (list, tuple, pyv8.JSObject)) else [item]
                       for name, item in properties.items()}
            length = max((len(column) for column in columns.values()), default=0)
            value = [{name: values[index] for name, values in columns.items() if index < len(values)}
                     for index in range(length)]
        else:
            value = sequence(value)
    frames = [dict(dictionary(frame).items()) for frame in value]
    previous = 0.0
    for frame in frames:
        if 'easing' in frame and not _valid_easing(frame['easing']):
            raise PageTypeError('Invalid keyframe easing.')
        offset = frame.get('offset')
        if is_nullish(offset):
            continue
        offset = float(number(offset))
        if not math.isfinite(offset) or offset < 0 or offset > 1 or offset < previous:
            raise PageTypeError('Keyframe offsets must be ordered numbers between 0 and 1.')
        frame['offset'] = previous = offset
    return frames


def _timing_values(options, previous=None):
    from ..page_errors import PageTypeError
    result = dict(previous or {'delay': 0.0, 'endDelay': 0.0, 'fill': 'auto',
                              'iterationStart': 0.0, 'iterations': 1.0, 'duration': 'auto',
                              'direction': 'normal', 'easing': 'linear'})
    for name in result:
        if name not in options:
            continue
        value = options[name]
        if name == 'duration' and isinstance(value, str):
            if value != 'auto': raise PageTypeError('Invalid animation duration.')
        elif name in ('duration', 'delay', 'endDelay', 'iterationStart', 'iterations'):
            value = float(number(value))
            if math.isnan(value) or (name in ('duration', 'iterationStart', 'iterations') and value < 0):
                raise PageTypeError('Invalid animation timing value.')
            if name in ('delay', 'endDelay', 'iterationStart') and not math.isfinite(value):
                raise PageTypeError('Invalid animation timing value.')
        else:
            value = str(dom_string(value))
            if name == 'easing' and not _valid_easing(value):
                raise PageTypeError('Invalid animation easing.')
            allowed = {'fill': ('auto', 'none', 'forwards', 'backwards', 'both'),
                       'direction': ('normal', 'reverse', 'alternate', 'alternate-reverse')}
            if name in allowed and value not in allowed[name]:
                raise PageTypeError('Invalid animation timing option.')
        result[name] = value
    return result


def _effect_progress(timing, local_time):
    duration = timing['duration'] if isinstance(timing['duration'], (int, float)) else 0
    iterations = timing['iterations']
    active_duration = 0 if duration == 0 or iterations == 0 else duration * iterations
    active = local_time - timing['delay']
    fill = timing['fill']
    if active < 0:
        if fill not in ('backwards', 'both'): return None
        active = 0
    elif active >= active_duration:
        if fill not in ('forwards', 'both'): return None
        active = active_duration
    overall = timing['iterationStart'] + (active / duration if duration else 0)
    if not math.isfinite(overall): return None
    whole = math.floor(overall)
    progress = overall - whole
    if active > 0 and active == active_duration and progress == 0:
        whole -= 1
        progress = 1.0
    direction = timing['direction']
    reverse = direction == 'reverse' or (direction == 'alternate' and whole % 2 == 1) or (direction == 'alternate-reverse' and whole % 2 == 0)
    return (1.0 - progress if reverse else progress), max(0, whole)


@interface('AnimationEffect', None)
class AnimationEffect:
    """The timing model shared by animation effects."""
    def __init__(self, options=None, **_):
        values = _options(options)
        self._timing = _timing_values(values)
        self._animation = None

    def fn_getTiming(self, *_): return dict(self._timing)
    def fn_getComputedTiming(self, *_):
        result = dict(self._timing)
        duration = result['duration']
        active = 0 if duration in ('auto', 0) or result['iterations'] == 0 else duration * result['iterations']
        local = self._animation.get_currentTime() if self._animation is not None else pyv8.Null
        progress = _effect_progress(result, local) if local is not pyv8.Null else None
        result.update(activeDuration=active, endTime=max(0, result['delay'] + active + result['endDelay']),
                      localTime=local, progress=progress[0] if progress else pyv8.Null,
                      currentIteration=progress[1] if progress else pyv8.Null)
        return result

    def fn_updateTiming(self, timing=MISSING, *_):
        values = _options(timing)
        self._timing = _timing_values(values, self._timing)
        if self._animation is not None:
            self._animation._reschedule()
            self._animation._invalidate_target()


@interface('KeyframeEffect', 'AnimationEffect')
class KeyframeEffect(AnimationEffect):
    def __init__(self, target=None, keyframes=None, options=None, services=None, **_):
        super().__init__(options=options)
        self._services = services
        self._target = target
        self._pseudo_element = str(dom_string(_options(options).get('pseudoElement', ''))) or pyv8.Null
        self._composite = str(dom_string(_options(options).get('composite', 'replace')))
        self._iteration_composite = str(dom_string(_options(options).get('iterationComposite', 'replace')))
        self._keyframes = _keyframe_list(keyframes)

    def get_target(self): return self._target if self._target is not None else pyv8.Null
    def get_pseudoElement(self): return self._pseudo_element
    def get_composite(self): return self._composite
    def get_iterationComposite(self): return self._iteration_composite
    def fn_getKeyframes(self, *_): return [dict(frame) for frame in self._keyframes]
    def fn_setKeyframes(self, keyframes=MISSING, *_):
        self._keyframes = _keyframe_list(keyframes)
        if self._animation is not None: self._animation._invalidate_target()


@interface('Animation', 'EventTarget')
class Animation(EventTarget):
    """Animation timing, playback state and promise lifecycle."""
    def __init__(self, target=None, keyframes=None, options=None, services=None, **_):
        super().__init__(services=services)
        self._options = _options(options)
        self._current_time = 0.0
        self._anchor = None
        self._start_time = pyv8.Null
        self._playback_rate = float(number(self._options.get('playbackRate', 1)))
        self._pending = True
        self._id = str(dom_string(self._options.get('id', '')))
        self._replace_state = 'active'
        self._range_start = self._range_end = pyv8.Null
        self._effect = KeyframeEffect(target=target, keyframes=keyframes, options=options, services=services)
        self._effect._animation = self
        self._timeline = DocumentTimeline()
        self._timer = None
        self._settled_finished = self._settled_ready = False
        self._play_state = 'running'
        self._ready_promise, self._settle_ready, _ = self._new_promise()
        self._finished_promise, self._settle_finished, self._reject_finished = self._new_promise()
        if target is not None:
            self._attach_target(target)
        tasks = self._tasks()
        if tasks is not None:
            tasks.schedule_html_timer(self._on_ready, 0.016, repeat=False)
        else:
            self._on_ready()

    def _tasks(self):
        return getattr(getattr(self, '_services', None), 'task_services', None)

    def _attach_target(self, target):
        import weakref
        if not hasattr(target, '_animations'): target._animations = []
        if self not in target._animations: target._animations.append(self)
        document = target.ownerDocument
        if document is not None:
            if not hasattr(document, '_animated_elements'): document._animated_elements = weakref.WeakSet()
            document._animated_elements.add(target)
        target._invalidate_layout()

    def _detach_target(self, target):
        animations = getattr(target, '_animations', [])
        if self in animations: animations.remove(self)
        document = target.ownerDocument
        if not animations and document is not None:
            getattr(document, '_animated_elements', set()).discard(target)
        target._invalidate_layout()

    def _invalidate_target(self):
        target = getattr(self._effect, '_target', None)
        if target is not None: target._invalidate_layout()

    def _new_promise(self):
        realm = getattr(getattr(self, '_services', None), 'realm_services', None)
        if realm is None: return pyv8.JSPromise(resolve=None), None, None
        return realm.make_promise()

    def _end_time(self):
        return self._effect.fn_getComputedTiming()['endTime'] if self._effect is not None else 0

    def _snapshot(self):
        if self._anchor is not None and self._play_state == 'running':
            now = time.monotonic()
            self._current_time += (now - self._anchor) * 1000 * self._playback_rate
            self._anchor = now
        return self._current_time

    def _reschedule(self):
        import math
        tasks = self._tasks()
        if tasks is None: return
        if self._timer is not None:
            tasks.cancel(self._timer)
            self._timer = None
        if self._play_state != 'running' or self._pending or self._playback_rate == 0: return
        current = self._snapshot()
        remaining = ((self._end_time() - current) if self._playback_rate > 0 else current)
        seconds = max(0, remaining / abs(self._playback_rate) / 1000)
        if math.isfinite(seconds):
            self._timer = tasks.schedule_html_timer(self._on_finish, seconds, repeat=False)

    def _on_ready(self):
        if self._settled_ready: return
        self._settled_ready = True
        self._pending = False
        if self._play_state == 'running':
            self._anchor = time.monotonic()
            self._start_time = self._timeline.get_currentTime()
        if self._settle_ready is not None: self._settle_ready(self)
        if not self._settled_finished:
            self._reschedule()

    def _on_finish(self):
        if self._settled_finished or self._play_state != 'running' or self._playback_rate == 0: return
        self._timer = None
        self._settled_finished = True
        self._on_ready()
        self._play_state = 'finished'
        self._current_time = self._end_time() if self._playback_rate >= 0 else 0
        self._anchor = None
        target = getattr(self._effect, '_target', None)
        if target is not None and self._effect._timing['fill'] not in ('forwards', 'both'):
            self._detach_target(target)
        else:
            self._invalidate_target()
        if self._settle_finished is not None: self._settle_finished(self)
        tasks = self._tasks()
        if tasks is not None:
            from .events import Event
            def dispatch_finish():
                event = Event('finish')
                event._trusted = True
                self._dispatch_event(event)
            tasks.post_task(dispatch_finish)

    def get_effect(self): return self._effect if self._effect is not None else pyv8.Null
    def set_effect(self, value):
        from ...foundation.arguments import interface_value
        if is_nullish(value):
            value = None
        else:
            value = interface_value(value, 'AnimationEffect', 'Animation', 'effect', 0, nullable=True)
        previous = self._effect
        if previous is value: return
        target = getattr(previous, '_target', None)
        if target is not None: self._detach_target(target)
        if previous is not None: previous._animation = None
        owner = getattr(value, '_animation', None)
        if owner is not None and owner is not self:
            owner.set_effect(None)
        self._effect = value
        if value is not None:
            value._animation = self
            target = getattr(value, '_target', None)
            if target is not None and self._play_state != 'idle': self._attach_target(target)
        self._reschedule()
    def get_timeline(self): return self._timeline
    def set_timeline(self, value): self._timeline = value
    def get_playState(self): return self._play_state
    def get_currentTime(self): return self._snapshot() if self._current_time is not pyv8.Null else pyv8.Null
    def set_currentTime(self, value):
        if is_nullish(value):
            if self._current_time is not pyv8.Null:
                from ..page_errors import PageTypeError
                raise PageTypeError('Cannot set a resolved current time to null.')
            return
        self._current_time = number(value)
        inside = (self._current_time < self._end_time() if self._playback_rate >= 0
                  else self._current_time > 0)
        if self._play_state == 'finished' and inside:
            self._play_state = 'running' if self._playback_rate else 'paused'
            self._finished_promise, self._settle_finished, self._reject_finished = self._new_promise()
            self._settled_finished = False
            target = getattr(self._effect, '_target', None)
            if target is not None: self._attach_target(target)
        if self._play_state == 'running': self._anchor = time.monotonic()
        self._reschedule()
        self._invalidate_target()
    def get_startTime(self): return self._start_time
    def set_startTime(self, value): self._start_time = value
    def get_playbackRate(self): return self._playback_rate
    def set_playbackRate(self, value):
        self._snapshot()
        self._playback_rate = float(number(value))
        self._reschedule()
        self._invalidate_target()
    def get_rangeStart(self): return self._range_start
    def set_rangeStart(self, value): self._range_start = value
    def get_rangeEnd(self): return self._range_end
    def set_rangeEnd(self, value): self._range_end = value
    def get_pending(self): return self._pending
    def get_id(self): return self._id
    def set_id(self, value): self._id = str(dom_string(value))
    def get_replaceState(self): return self._replace_state
    def get_overallProgress(self): return pyv8.Null
    def get_finished(self): return self._finished_promise
    def get_ready(self): return self._ready_promise
    def get_onfinish(self): return self._get_handler('onfinish')
    def set_onfinish(self, value): self._set_handler('onfinish', value)

    def fn_play(self, *_):
        if self._play_state in ('idle', 'finished'):
            if self._play_state == 'idle' or self._playback_rate >= 0:
                self._current_time = self._end_time() if self._playback_rate < 0 else 0.0
            self._finished_promise, self._settle_finished, self._reject_finished = self._new_promise()
            self._settled_finished = False
        target = getattr(self._effect, '_target', None)
        if target is not None: self._attach_target(target)
        self._play_state = 'running'
        self._anchor = time.monotonic()
        self._pending = False
        self._reschedule()

    def fn_pause(self, *_):
        self._snapshot()
        self._play_state = 'paused'
        self._anchor = None
        self._pending = False
        self._reschedule()
        self._invalidate_target()

    def fn_cancel(self, *_):
        if self._play_state != 'idle' and not self._settled_finished and self._reject_finished:
            realm = self._services.realm_services
            realm.materialize_interface('DOMException')
            # Preserve the realm's exception across the Python/V8 value boundary;
            # directly returning a native Error converts it into a Python exception.
            reason = realm.evaluate_script("new Proxy(new DOMException('The animation was cancelled.', 'AbortError'), "
                                           "{get: (target, key) => Reflect.get(target, key, target)})")
            self._reject_finished(reason)
        self._play_state = 'idle'
        self._current_time = self._start_time = pyv8.Null
        self._anchor = None
        self._pending = False
        self._reschedule()
        target = getattr(self._effect, '_target', None)
        if target is not None: self._detach_target(target)
        self._finished_promise, self._settle_finished, self._reject_finished = self._new_promise()
        self._settled_finished = False

    def fn_finish(self, *_):
        from ..page_errors import InvalidStateError
        import math
        if self._playback_rate == 0 or not math.isfinite(self._end_time()):
            raise InvalidStateError('Cannot finish this animation.')
        if self._timer is not None and self._tasks() is not None:
            self._tasks().cancel(self._timer)
            self._timer = None
        self._play_state = 'running'
        self._on_finish()
        self._pending = False

    def fn_reverse(self, *_):
        from ..page_errors import InvalidStateError
        if self._playback_rate == 0: raise InvalidStateError('Cannot reverse an animation with zero playback rate.')
        self.set_playbackRate(-self._playback_rate)
        self.fn_play()
    def fn_updatePlaybackRate(self, rate=MISSING, *_): self.set_playbackRate(rate)
    def fn_persist(self, *_): return None
    def fn_commitStyles(self, *_): return None


def animated_style(element, values):
    """Overlay supported numeric and discrete keyframe properties on a style snapshot."""
    import re
    for animation in getattr(element, '_animations', ()):
        if animation._play_state == 'idle' or animation._effect is None: continue
        timing = animation._effect._timing
        current = animation.get_currentTime()
        if current is pyv8.Null: continue
        state = _effect_progress(timing, current)
        if state is None: continue
        progress = state[0]
        frames = animation._effect._keyframes
        if not frames: continue
        offsets = [float(frame['offset']) if frame.get('offset') is not None else None for frame in frames]
        if len(offsets) == 1:
            offsets[0] = 1.0 if offsets[0] is None else offsets[0]
        else:
            if offsets[0] is None: offsets[0] = 0.0
            if offsets[-1] is None: offsets[-1] = 1.0
            previous = 0
            for index in range(1, len(offsets)):
                if offsets[index] is not None:
                    for pos in range(previous + 1, index):
                        offsets[pos] = offsets[previous] + (offsets[index] - offsets[previous]) * (pos - previous) / (index - previous)
                    previous = index
        upper = next((i for i, offset in enumerate(offsets) if offset >= progress), len(offsets) - 1)
        lower = max(0, upper - 1)
        if progress >= offsets[-1]: lower = upper = len(offsets) - 1
        span = offsets[upper] - offsets[lower]
        fraction = min(1.0, max(0.0, (progress - offsets[lower]) / span)) if span else 0.0
        for name in set(frames[lower]) & set(frames[upper]):
            if name in ('offset', 'easing', 'composite'): continue
            prop = re.sub(r'([A-Z])', lambda m: '-' + m.group(1).lower(), name)
            left, right = str(frames[lower][name]), str(frames[upper][name])
            a = re.fullmatch(r'(-?(?:\d+(?:\.\d*)?|\.\d+))([a-z%]*)', left)
            b = re.fullmatch(r'(-?(?:\d+(?:\.\d*)?|\.\d+))([a-z%]*)', right)
            if a and b and a[2] == b[2]:
                values[prop] = f'{float(a[1]) + (float(b[1]) - float(a[1])) * fraction:.12g}{a[2]}'
            else:
                values[prop] = left if fraction < .5 else right
    return values
