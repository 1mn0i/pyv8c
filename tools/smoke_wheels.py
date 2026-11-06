"""Exercise the installed wheels outside the source tree."""

from pathlib import Path
from importlib import metadata
import importlib
import sys

from pyv8c import pyv8
from pyv8c import Browser
from pyv8c.engine import binder


class Global:
    __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)


assert (Path(pyv8.__file__).parent / "icudtl.dat").is_file()
assert importlib.import_module('pyv8c.pyv8') is pyv8 is binder.pyv8
assert 'pyv8' not in sys.modules
assert not any(item.lower().startswith("pyv8<") or item.lower().startswith("pyv8>") or item.lower() == "pyv8" for item in metadata.requires("pyv8c") or ())
from pyv8c.pyv8.devtools import DevToolsOptions
assert DevToolsOptions
context = pyv8.Context(Global(), trace=True)
try:
    assert context.exec_js("new Intl.NumberFormat('en-US').format(1234)") == "1,234"
    try:
        context.exec_js("throw new TypeError('wheel smoke')")
    except pyv8.JSException as error:
        assert 'wheel smoke' in str(error)
    else:
        raise AssertionError('JavaScript TypeError was not propagated')
finally:
    context.close()

browser = Browser(real_network=False)
try:
    page = browser.new_profile().new_page()
    assert page.evaluate("[1, 2, 3].reduce((a, b) => a + b, 0)") == 6
finally:
    browser.close()

print(f"Wheel smoke test passed: {pyv8.__file__}")
