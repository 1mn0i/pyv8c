"""Streaming mono dynamics compression, following Blink's compression kernel.

Adapted from third_party/blink/renderer/platform/audio/dynamics_compressor.cc
in Chromium. Parameter changes are sampled at 32-frame division boundaries.

Copyright (C) 2011 Google Inc. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of Apple Computer, Inc. ("Apple") nor the names of its
   contributors may be used to endorse or promote products derived from this
   software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY APPLE AND ITS CONTRIBUTORS "AS IS" AND ANY
EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL APPLE OR ITS CONTRIBUTORS BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
import math
from collections import deque


_RELEASE_POLYNOMIAL = (
    0.09,
    -1.5788320352845888 * .09 + 2.3305837032074286 * .16 - .9141194204840429 * .42 + .1623677525612032 * .98,
    .5334142869106424 * .09 - 1.272736789213631 * .16 + .9258856042207512 * .42 - .18656310191776226 * .98,
    .08783463138207234 * .09 - .1694162967925622 * .16 + .08588057951595272 * .42 - .00429891410546283 * .98,
    -.042416883008123074 * .09 + .1115693827987602 * .16 - .09764676325265872 * .42 + .028494263462021576 * .98,
)


def _db(value):
    return 20 * math.log10(value) if value > 0 else -math.inf


def _linear(value):
    return 10 ** (value / 20)


class CompressorDSP:
    """State carried between successive samples of a compressor node."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.delay = deque([0.0] * min(int(.006 * sample_rate), 1023))
        self.detector = 0.0
        self.gain = 1.0
        self.reduction = 0.0
        self._meter = 1.0
        self._meter_rate = 1 - math.exp(-1 / (.325 * sample_rate))
        self._max_attack = -1.0
        self._frame = 0
        self._parameters = None
        self._scaled_gain = 0.0
        self._envelope_rate = 0.0

    def _knee_curve(self, value, k):
        if value < self._threshold:
            return value
        return self._threshold - math.expm1(-k * (value - self._threshold)) / k

    def _saturate(self, value):
        if value < self._knee_threshold:
            return self._knee_curve(value, self._k)
        return _linear(self._knee_y_db + (_db(value) - self._knee_db) / self._ratio)

    def _curve(self, threshold, knee, ratio):
        self._threshold = _linear(threshold)
        self._ratio = max(ratio, 1.0)
        self._knee_db = threshold + max(knee, 0.0)
        self._knee_threshold = _linear(self._knee_db)
        low, high, k = .1, 10000., 5.
        x, x2 = self._knee_threshold, self._knee_threshold * 1.001
        denominator = _db(x2) - self._knee_db
        for _ in range(15):
            slope = (_db(self._knee_curve(x2, k)) - _db(self._knee_curve(x, k))) / denominator
            if slope < 1 / self._ratio:
                high = k
            else:
                low = k
            k = math.sqrt(low * high)
        self._k = k
        self._knee_y_db = _db(self._knee_curve(self._knee_threshold, k))
        self._makeup = (1 / self._saturate(1.0)) ** .6

    def _division(self, parameters):
        threshold, knee, ratio, attack, release = parameters
        if parameters[:3] != (self._parameters or ())[:3]:
            self._curve(threshold, knee, ratio)
        self._parameters = parameters
        desired = min(1.0, max(0.0, self.detector))
        self._scaled_gain = math.asin(desired) / (math.pi / 2)
        releasing = self._scaled_gain > self.gain
        difference = _db(self.gain / self._scaled_gain) if self._scaled_gain else (-1.0 if releasing else 1.0)
        if not math.isfinite(difference):
            difference = -1.0 if releasing else 1.0
        if releasing:
            self._max_attack = -1.0
            x = .25 * (min(0.0, max(-12.0, difference)) + 12)
            polynomial = sum(coefficient * x ** power for power, coefficient in enumerate(_RELEASE_POLYNOMIAL))
            frames = max(1.0, self.sample_rate * max(0.0, release) * polynomial)
            self._envelope_rate = _linear(5 / frames)
        else:
            self._max_attack = max(self._max_attack, difference)
            frames = self.sample_rate * max(.001, attack)
            self._envelope_rate = 1 - (.25 / max(.5, self._max_attack)) ** (1 / frames)

    def process(self, value, parameters):
        if self._frame % 32 == 0:
            self._division(parameters)
        magnitude = abs(value)
        attenuation = self._saturate(magnitude) / magnitude if magnitude > .0001 else 1.0
        release_rate = _linear(max(2.0, -_db(attenuation)) / (.0025 * self.sample_rate)) - 1
        rate = release_rate if attenuation > self.detector else 1.0
        self.detector = min(1.0, self.detector + (attenuation - self.detector) * rate)
        if self._envelope_rate < 1:
            self.gain += (self._scaled_gain - self.gain) * self._envelope_rate
        else:
            self.gain = min(1.0, self.gain * self._envelope_rate)
        warped = math.sin(math.pi / 2 * self.gain)
        meter = _db(warped)
        self._meter = meter if meter < self._meter else self._meter + (meter - self._meter) * self._meter_rate
        self.reduction = self._meter
        delayed = value
        if self.delay:
            self.delay.append(value)
            delayed = self.delay.popleft()
        self._frame += 1
        return delayed * self._makeup * warped
