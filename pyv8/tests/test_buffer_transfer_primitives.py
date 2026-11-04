import pyv8
import pytest


def test_buffer_brands_and_detach_ignore_page_hooks():
    ctx = pyv8.Context(object())
    try:
        buffer = ctx.exec_js('globalThis.calls=0;globalThis.b=new ArrayBuffer(4);'
                             'Object.defineProperty(b,"constructor",{get(){calls++;throw 1}});'
                             'b.transfer=()=>{calls++;throw 2};b')
        assert buffer._buffer_kind() == 'buffer'
        assert bytes(buffer) == b'\x00'*4
        assert ctx.exec_js('new DataView(b,1,2)')._buffer_kind() == 'view'
        assert ctx.exec_js('({[Symbol.toStringTag]:"ArrayBuffer"})')._buffer_kind() is None
        buffer._detach_buffer()
        assert buffer._buffer_kind() == 'detached'
        assert ctx.exec_js('calls') == 0
    finally:
        ctx.close()
    with pytest.raises(RuntimeError):
        buffer._buffer_kind()
