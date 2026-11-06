import json
from pyv8c import pyv8
import pytest


def test_navigation_rebinds_all_sessions_and_keeps_runtime_enabled():
    isolate = pyv8.Isolate()
    old = isolate.context(object())
    new = isolate.context(object())
    class Debugger(pyv8.Debugger):
        def handle(self, message):
            self.messages.append(json.loads(message))
    sessions = [Debugger(old), Debugger(old)]
    try:
        old.exec_js('globalThis.value=1')
        new.exec_js('globalThis.value=2')
        for session in sessions:
            session.messages = []
            session.send(b'{"id":1,"method":"Runtime.enable"}')
        sessions[0].rebind_context(new)
        old.close()
        for session in sessions:
            session.send(b'{"id":2,"method":"Runtime.evaluate","params":{"expression":"value"}}')
            reply = next(message for message in session.messages if message.get('id') == 2)
            assert reply['result']['result']['value'] == 2
    finally:
        for session in sessions:
            session.disconnect()
        old.close()
        new.close()
        isolate.close()


def test_rebinding_rejects_another_isolate():
    left, right = pyv8.Isolate(), pyv8.Isolate()
    a, b = left.context(object()), right.context(object())
    debugger = pyv8.Debugger(a)
    try:
        with pytest.raises(ValueError, match='same isolate'):
            debugger.rebind_context(b)
    finally:
        debugger.disconnect()
        a.close()
        b.close()
        left.close()
        right.close()
