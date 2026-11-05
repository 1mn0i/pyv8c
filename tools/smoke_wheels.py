"""Exercise the installed wheels outside the source tree."""

from pathlib import Path

import pyv8
from pyv8c import Browser


class Global:
    __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)


assert (Path(pyv8.__file__).parent / "icudtl.dat").is_file()
context = pyv8.Context(Global())
try:
    assert context.exec_js("new Intl.NumberFormat('en-US').format(1234)") == "1,234"
finally:
    context.close()

browser = Browser(real_network=False)
try:
    page = browser.new_profile().new_page()
    assert page.evaluate("[1, 2, 3].reduce((a, b) => a + b, 0)") == 6
finally:
    browser.close()

print(f"Wheel smoke test passed: {pyv8.__file__}")
