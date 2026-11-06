"""Native faults and lock cycles must be tested outside the pytest process."""
import ctypes
import subprocess
import sys

import pytest
from pyv8c import pyv8


class Window:
    pass


def child(program):
    result = subprocess.run([sys.executable, "-c", program], capture_output=True,
                            text=True, timeout=12)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("buffer", ["ArrayBuffer", "SharedArrayBuffer"])
@pytest.mark.parametrize("view", ["b", "new Uint8Array(b)", "new DataView(b, 1, 2)"])
def test_resizable_storage_refuses_zero_copy_but_allows_snapshot(buffer, view):
    option = "maxByteLength"
    child(f"""
from pyv8c import pyv8
class Window: pass
c = pyv8.Context(Window)
try:
    value = c.exec_js('globalThis.b = new {buffer}(65536, {{{option}:131072}}); new Uint8Array(b).fill(65); {view}')
    snapshot = bytes(value)
    try: value._buffer_view()
    except TypeError as error: assert 'non-resizable' in str(error)
    else: raise AssertionError('unsafe zero-copy view accepted')
    c.exec_js('b.' + ('resize(0)' if '{buffer}' == 'ArrayBuffer' else 'grow(131072)'))
    assert snapshot and all(byte == 65 for byte in snapshot)
finally: c.close()
""")


class Buffer(ctypes.Structure):
    _fields_ = [("buf", ctypes.c_void_p), ("obj", ctypes.c_void_p),
                ("len", ctypes.c_ssize_t), ("itemsize", ctypes.c_ssize_t),
                ("readonly", ctypes.c_int), ("ndim", ctypes.c_int),
                ("format", ctypes.c_void_p), ("shape", ctypes.c_void_p),
                ("strides", ctypes.c_void_p), ("suboffsets", ctypes.c_void_p),
                ("internal", ctypes.c_void_p)]


@pytest.mark.parametrize("flags", [0, 8, 0x18, 0x11C])
def test_readonly_export_honors_requested_metadata_and_rejects_writable(flags):
    get = ctypes.pythonapi.PyObject_GetBuffer
    get.argtypes = [ctypes.py_object, ctypes.POINTER(Buffer), ctypes.c_int]
    get.restype = ctypes.c_int
    release = ctypes.pythonapi.PyBuffer_Release
    release.argtypes = [ctypes.POINTER(Buffer)]
    release.restype = None
    context = pyv8.Context(Window)
    try:
        view = context.exec_js("new Uint8Array([1,2,3])")._buffer_view()
        export = Buffer()
        assert get(view.obj, ctypes.byref(export), flags) == 0
        try:
            assert export.readonly and export.len == 3 and export.itemsize == 1
            if flags & 8:
                assert export.shape
                assert ctypes.cast(export.shape, ctypes.POINTER(ctypes.c_ssize_t))[0] == 3
            if flags & 0x10:
                assert export.strides
            if flags & 4:
                assert ctypes.string_at(export.format) == b"B"
        finally:
            release(ctypes.byref(export))
        with pytest.raises(BufferError):
            get(view.obj, ctypes.byref(Buffer()), flags | 1)
        assert bytes(view) == b"\x01\x02\x03"
        context.close()
        assert bytes(view) == b"\x01\x02\x03"
    finally:
        context.close()


@pytest.mark.parametrize("operation", ["execute", "close", "query"])
def test_cross_isolate_contention_fails_without_waiting_for_outer_locks(operation):
    child("""
from pyv8c import pyv8
import threading
class Window: pass
a, b = pyv8.Isolate(), pyv8.Isolate()
x, y = a.context(Window), b.context(Window)
entered, attempted = threading.Barrier(2), threading.Barrier(2)
refused, failures = [], []
def call(target):
    entered.wait(3)
    try: target.exec_js('1')
    except RuntimeError as error:
        assert 'nested execution' in str(error)
        refused.append(str(error))
    attempted.wait(3)
    return 42
x.expose(left=lambda: call(y)); y.expose(right=lambda: call(x))
def run(context, source):
    try: assert context.exec_js(source) == 42
    except BaseException as error: failures.append(error)
threads = [threading.Thread(target=run, args=(x,'left()')),
           threading.Thread(target=run, args=(y,'right()'))]
try:
    for thread in threads: thread.start()
    for thread in threads: thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not failures, failures
    assert len(refused) == 2
    assert x.exec_js('2') == 2 and y.exec_js('3') == 3
finally:
    x.close(); y.close(); a.close(); b.close()
""".replace("target.exec_js('1')", {"execute": "target.exec_js('1')", "close": "target.close()",
       "query": "pyv8.current_context(b if target is y else a)"}[operation]))


def test_uncontended_cross_isolate_nesting_and_return_to_outer_isolate():
    child("""
from pyv8c import pyv8
class Window: pass
a,b = pyv8.Isolate(),pyv8.Isolate()
x,y = a.context(Window),b.context(Window)
try:
    y.expose(back=lambda: x.exec_js('21'))
    x.expose(next_realm=lambda: y.exec_js('back()*2'))
    assert x.exec_js('next_realm()') == 42
finally:
    x.close(); y.close(); a.close(); b.close()
""")


def test_implicit_context_queries_use_active_isolate_even_when_default_is_busy():
    child("""
from pyv8c import pyv8
import threading
class Window: pass
default = pyv8.Context(Window)
isolate = pyv8.Isolate()
other = isolate.context(Window)
entered, queried = threading.Barrier(2), threading.Barrier(2)
failures = []
def query(context):
    entered.wait(3)
    try:
        assert pyv8.current_context() is context
        assert pyv8.current_context(None) is context
        assert pyv8.incumbent_context() is context
        assert pyv8.incumbent_context(None) is context
        if context is other:
            # Legacy module entries still target the default isolate, but must
            # report contention as Python errors rather than unwind CPython.
            for operation in (pyv8.v8_gc, pyv8.low_memory_notification):
                try: operation()
                except RuntimeError as error: assert 'nested execution' in str(error)
                else: raise AssertionError('contended default isolate entered')
    except BaseException as error: failures.append(error)
    queried.wait(3)
    return 42
default.expose(probe=lambda: query(default))
other.expose(probe=lambda: query(other))
def run(context):
    try: assert context.exec_js('probe()') == 42
    except BaseException as error: failures.append(error)
threads=[threading.Thread(target=run,args=(context,)) for context in (default,other)]
try:
    for thread in threads: thread.start()
    for thread in threads: thread.join(5)
    assert not any(thread.is_alive() for thread in threads)
    assert not failures, failures
    assert pyv8.current_context() is None
    assert pyv8.incumbent_context() is None
finally:
    default.close();other.close();isolate.close()
""")


@pytest.mark.parametrize("depth", [7, 8, 9, 12, 24])
def test_outer_deadline_survives_deep_nesting_and_unwind(depth):
    child(f"""
from pyv8c import pyv8
class Window: pass
c=pyv8.Context(Window)
def descend(n):
    if n: c.exec_js('descend(%d)' % (n-1))
c.expose(descend)
try:
    try: c.exec_js('descend({depth}); while(true) {{}}', timeout=.15)
    except pyv8.JavaScriptTerminated: pass
    else: raise AssertionError('outer deadline lost')
    assert c.exec_js('6*7') == 42
finally: c.close()
""")


def test_short_inner_deadline_beyond_old_limit_and_exception_unwind():
    child("""
from pyv8c import pyv8
class Window: pass
c=pyv8.Context(Window)
def descend(n):
    if n: c.exec_js('descend(%d)' % (n-1))
    else: c.exec_js('while(true) {}',timeout=.05)
c.expose(descend)
try:
    try: c.exec_js('descend(12)',timeout=2)
    except pyv8.JavaScriptTerminated: pass
    else: raise AssertionError('inner deadline lost')
    assert c.exec_js('42') == 42
    def fail(n):
        if n: c.exec_js('fail(%d)' % (n-1))
        else: raise ValueError('host failure')
    c.expose(fail)
    try: c.exec_js('fail(12)',timeout=.05)
    except ValueError: pass
    else: raise AssertionError('host exception lost')
    c.exec_js('var t=Date.now();while(Date.now()-t<150){}')
    assert c.exec_js('42') == 42
finally: c.close()
""")


@pytest.mark.parametrize("expression", ["({a:1})", "Symbol('value')"])
@pytest.mark.parametrize("closing", ["target", "source"])
def test_import_revalidates_context_and_handle_after_waiting(expression, closing):
    child(f"""
from pyv8c import pyv8
import threading,time
class Window: pass
isolate=pyv8.Isolate()
source,target=isolate.context(Window),isolate.context(Window)
handle=source.exec_js({expression!r})
entered,attempt=threading.Event(),threading.Event()
results=[]
def hold():
    entered.set()
    assert attempt.wait(3)
    time.sleep(.15)
    {closing}.close()
    return 0
source.expose(hold)
def importing():
    attempt.set()
    try: target.import_handle(handle)
    except RuntimeError as error: results.append(str(error))
    else: results.append('incorrect success')
holder=threading.Thread(target=lambda: source.exec_js('hold()'))
holder.start()
assert entered.wait(3)
waiter=threading.Thread(target=importing)
waiter.start()
holder.join(4);waiter.join(4)
assert not holder.is_alive() and not waiter.is_alive()
assert len(results)==1 and 'closed' in results[0], results
source.close();target.close();isolate.close()
""")
