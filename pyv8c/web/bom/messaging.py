"""MessageChannel and its entangled pair of MessagePorts.

Both ports belong to the realm that created the channel, so a delivery is a task on
that realm's queue: the sending side never runs the receiving handler itself.
"""
from __future__ import annotations

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import MISSING, is_nullish, missing_error
from ..dom.events import EventTarget
from ..fetch.clone import message_transfer
from ..fetch.events import MessageEvent

__all__ = ["MessageChannel", "MessagePort"]


@interface('MessagePort', 'EventTarget')
class MessagePort(EventTarget):
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="onmessage", getter="get_onmessage", setter="set_onmessage"),
            AttributeSpec(name="onmessageerror", getter="get_onmessageerror",
                          setter="set_onmessageerror"),
        ),
        methods=(
            MethodSpec(name="postMessage", callback="fn_postMessage", length=1),
            MethodSpec(name="start", callback="fn_start"),
            MethodSpec(name="close", callback="fn_close"),
        ),
        constructor_behavior=0,
        to_string_tag="MessagePort",
        has_constructor=1,
    )

    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **kw):
        if services is pyv8.Undefined:
            services = None
        super().__init__(services=services or _realm, **kw)
        self._twin = None
        #: A port queues what arrives before its queue is enabled, and flushes in order.
        self._pending = []
        #: What was posted before the far end existed, as a shared worker's port is.
        self._outbox = []
        self._enabled = False
        self._closed = False

    def _entangle(self, twin):
        self._twin = twin
        if twin is None:
            return
        outbox, self._outbox = self._outbox, []
        for message in outbox:
            self._send(message)

    def get_onmessage(self): return self._get_handler("onmessage")
    def get_onmessageerror(self): return self._get_handler("onmessageerror")
    def set_onmessage(self, value):
        self._set_handler("onmessage", value)
        if not self._enabled and not is_nullish(value):
            self.fn_start()
    def set_onmessageerror(self, value): self._set_handler("onmessageerror", value)

    def fn_postMessage(self, message=MISSING, options=None, *_extra):
        if message is MISSING:
            raise missing_error('MessagePort', 'postMessage', 1, 0, constructor=False)
        message_transfer(options)
        if self._closed:
            return None
        if self._twin is None:
            self._outbox.append(message)
            return None
        self._send(message)
        return None

    def _send(self, message):
        twin = self._twin
        if twin is None or twin._closed or self._closed:
            return
        services = twin._services
        if services is not None:
            services.realm_services.post_task(lambda: twin._deliver(message))

    def fn_start(self, *_extra):
        if self._enabled:
            return None
        self._enabled = True
        pending, self._pending = self._pending, []
        for data in pending:
            self._deliver(data)
        return None

    def fn_close(self, *_extra):
        self._closed = True
        self._pending.clear()
        return None

    def _deliver(self, data):
        if self._closed:
            return
        if not self._enabled:
            self._pending.append(data)
            return
        services = self._services
        if services is not None:
            services.realm_services.materialize_interface("MessageEvent")
        self.fn_dispatchEvent(MessageEvent("message", {"data": data, "origin": "",
                                                       "isTrusted": True}))


@interface('MessageChannel', None)
class MessageChannel:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="port1", getter="get_port1", side_effect=1),
            AttributeSpec(name="port2", getter="get_port2", side_effect=1),
        ),
        constructor_behavior=1,
        to_string_tag="MessageChannel",
        has_constructor=1,
    )

    def __init__(self, *_extra, services=pyv8.Undefined, _realm=None, **_):
        if services is pyv8.Undefined:
            services = None
        services = services or _realm
        self._port1 = MessagePort(services=services)
        self._port2 = MessagePort(services=services)
        self._port1._entangle(self._port2)
        self._port2._entangle(self._port1)

    def get_port1(self): return self._port1
    def get_port2(self): return self._port2
