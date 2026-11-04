"""Python collection must not wait for a paused/busy isolate to accept work."""
import subprocess
import sys
import pytest


@pytest.mark.parametrize("kind", ["context", "object", "function", "symbol", "script", "resolver"])
def test_collection_does_not_wait_for_active_realm(kind):
    result = subprocess.run([sys.executable, "-c", r'''
import gc
import threading
import pyv8
import sys

class Window: pass

gc.disable()
isolate = pyv8.Isolate()
active = isolate.context(Window())
kind = sys.argv[1]
held = []
if kind == 'context':
    garbage_window = Window()
    garbage = isolate.context(garbage_window)
    garbage_window.context = garbage
    del garbage, garbage_window
elif kind == 'script':
    held.append(pyv8.Script(active, '42'))
elif kind == 'resolver':
    held.append(pyv8.PromiseResolver(active))
else:
    held.append(active.exec_js({'object':'({})', 'function':'(()=>42)', 'symbol':"Symbol('test')"}[kind]))
entered, release, collected = threading.Event(), threading.Event(), threading.Event()
errors = []
def block():
    entered.set()
    assert release.wait(5)
active.expose(block=block)
def execute():
    try: active.exec_js('block()')
    except BaseException as error: errors.append(error)
def collect():
    held.clear()
    gc.collect()
    collected.set()
owner = threading.Thread(target=execute)
collector = threading.Thread(target=collect)
owner.start()
assert entered.wait(3)
collector.start()
try:
    nonblocking = collected.wait(1)
finally:
    release.set()
    owner.join(5)
    collector.join(5)
assert not owner.is_alive() and not collector.is_alive()
assert not errors, errors
active.close()
gc.collect()
isolate.close()
assert nonblocking, 'GC waited for the active isolate, blocking control-thread progress'
''', kind], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
