"""Serializer callbacks borrow streams and may never export their native lifetime."""
import subprocess
import sys

import pytest


def child(program):
    result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_serialized_wasm_module_keeps_runtime_alive_until_released():
    child("""
import gc
from pyv8c import pyv8
isolate = pyv8.Isolate()
context = isolate.context(object())
serializer = pyv8.Serializer(context)
module = context.exec_js('new WebAssembly.Module(new Uint8Array([0,97,115,109,1,0,0,0]))')
modules = []
wire = serializer.serialize(module, wasm_modules=modules)
assert len(modules) == 1
del module, serializer
context.close()
isolate.close()
try:
    pyv8.shutdown_v8()
except RuntimeError as error:
    assert 'serialized WebAssembly module' in str(error)
else:
    raise AssertionError('live compiled module must keep the V8 runtime alive')
modules.clear()
gc.collect()
assert pyv8.shutdown_v8() is True
""")


def test_wasm_module_survives_source_isolate_close():
    child("""
from pyv8c import pyv8
source_isolate = pyv8.Isolate()
source = source_isolate.context(object())
module = source.exec_js('new WebAssembly.Module(new Uint8Array([0,97,115,109,1,0,0,0]))')
serializer = pyv8.Serializer(source)
modules = []
wire = serializer.serialize(module, wasm_modules=modules)
del module, serializer
source.close()
source_isolate.close()
target_isolate = pyv8.Isolate()
target = target_isolate.context(object())
serializer = pyv8.Serializer(target)
clone = serializer.deserialize(wire, wasm_modules=modules)
target.expose(clone=clone)
assert target.exec_js('clone instanceof WebAssembly.Module && new WebAssembly.Instance(clone) instanceof WebAssembly.Instance')
del clone, serializer
target.close()
target_isolate.close()
modules.clear()
assert pyv8.shutdown_v8() is True
""")


def test_escaped_streams_reject_every_operation_after_the_callback():
    child("""
from pyv8c import pyv8
class Window: pass
context = pyv8.Context(Window())
serializer = pyv8.Serializer(context)
saved = {}
def write(value, writer):
    saved['writer'] = writer
    writer.write_tag(7)
    return True
def read(reader):
    saved['reader'] = reader
    assert reader.read_tag() == 7
    return context.exec_js('({copy:42})')
try:
    payload = serializer.serialize(context.exec_js('({value:1})'), host_check=lambda _:True, host_writer=write)
    assert serializer.deserialize(payload, host_reader=read) == {'copy':42}
    for method, args in [('write_tag',(1,)), ('write_uint32',(1,)), ('write_uint64',(1,)), ('write_double',(1.,)), ('write_raw_bytes',(b'x',))]:
        try: getattr(saved['writer'], method)(*args)
        except RuntimeError: pass
        else: raise AssertionError(method)
    for method, args in [('read_tag',()), ('read_uint32',()), ('read_uint64',()), ('read_double',()), ('read_raw_bytes',(1,))]:
        try: getattr(saved['reader'], method)(*args)
        except RuntimeError: pass
        else: raise AssertionError(method)
finally: context.close()
""")


@pytest.mark.parametrize("closed", [False, True])
def test_serializer_refuses_foreign_or_closed_handles(closed):
    child("""
from pyv8c import pyv8
class Window: pass
source_isolate = pyv8.Isolate()
target_isolate = pyv8.Isolate()
source = source_isolate.context(Window())
target = target_isolate.context(Window())
buffer = source.exec_js('new ArrayBuffer(8)')
serializer = pyv8.Serializer(target)
""" + ("source.close()\n" if closed else "") + """
try:
    for call in [lambda:serializer.serialize(buffer),
                 lambda:serializer.serialize(0, transfer=[buffer]),
                 lambda:serializer.deserialize(serializer.serialize(0), transfer=[buffer])]:
        try: call()
        except RuntimeError: pass
        else: raise AssertionError('unsafe handle accepted')
    assert target.exec_js('6*7') == 42
finally:
    source.close(); target.close(); source_isolate.close(); target_isolate.close()
""")


def test_streams_reject_other_threads_and_invalidate_on_callback_failure():
    child("""
from pyv8c import pyv8
import threading
class Window: pass
context = pyv8.Context(Window())
serializer = pyv8.Serializer(context)
saved = []
def write(value, writer):
    saved.append(writer)
    errors = []
    def other():
        try: writer.write_tag(1)
        except RuntimeError: errors.append('refused')
    thread=threading.Thread(target=other)
    thread.start(); thread.join(3)
    assert not thread.is_alive() and errors == ['refused']
    assert context.exec_js('6*7') == 42
    raise ValueError('callback failed')
try:
    try: serializer.serialize(context.exec_js('({x:1})'), host_check=lambda _:True, host_writer=write)
    except ValueError as error: assert str(error) == 'callback failed'
    else: raise AssertionError('callback failure was lost')
    try: saved[0].write_tag(1)
    except RuntimeError: pass
    else: raise AssertionError('failed callback retained a stream')
finally: context.close()
""")
