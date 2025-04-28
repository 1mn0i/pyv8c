import os

from pyv8env import _pyv8env


_pyv8env.init_v8(
    icudtl_path=os.path.join(os.path.dirname(__file__), r'icudtl.dat'),
    params=[
        "--expose-gc",
        # "--no-freeze-flags-after-init",
        # "--max_old_space_size=4098",
        # "--max-shared-heap-size=4024",
        # "--max_semi_space_size=8096",
        # "--initial-shared-heap-size=4024",
        # "--single-threaded",
        # "--allow-natives-syntax"
        ]
)

Context = _pyv8env.Context
Script = _pyv8env.Script
Debugger = _pyv8env.Debugger
JSObject = _pyv8env.JSObject
JSPromise = _pyv8env.JSPromise
JSTypeArray = _pyv8env.JSTypeArray
JSFunction = _pyv8env.JSFunction
JSException = _pyv8env.JSException
JavaScriptTerminated = _pyv8env.JavaScriptTerminated
Null = _pyv8env.Null
Undefined = _pyv8env.Undefined
current_context = _pyv8env.current_context
new = _pyv8env.new
v8_gc = _pyv8env.v8_gc


class ArrayType:
    Uint32Array = 1
    Uint16Array = 2
    Uint8Array = 3
    Int32Array = 4
    Int16Array = 5
    Int8Array = 6
    Float64Array = 7
    Float32Array = 8
    Uint8ClampedArray = 9


from .debug import Debugger, DebuggerError
from .devtools import start_devtools
