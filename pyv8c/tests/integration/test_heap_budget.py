"""A page that outgrows the isolate's heap budget is stopped, and the host lives."""
import pytest
import subprocess
import sys
import textwrap

#: The budget is a process-wide V8 setting, so this runs in a process of its own.
SCRIPT = """
import pyv8

pyv8.configure(params=["--max-old-space-size=256"])

from pyv8c import Browser
from pyv8c.web.fetch.response import Response

HTML = ("<body><script>var a = [];"
        " for (let i = 0; i < 200000000; i++) a.push(i);</script></body>")


def loader(url, request):
    return Response(HTML, {"url": url, "content-type": "text/html"})


browser = Browser(resource_loader=loader, stub_logging="off")
try:
    page = browser.new_profile().new_page()
    try:
        page.set_content(HTML, url="http://example.test/")
    except pyv8.JavaScriptTerminated:
        print("terminated")
    else:
        raise AssertionError("heap termination was swallowed by script execution")
    print("readyState", page.evaluate("document.readyState"))
    print("terminations", page.v8_context.heap_terminations)
finally:
    browser.close()
print("closed")
"""


@pytest.mark.slow  # a V8 heap limit takes the process down, not just the test
def test_a_page_that_outgrows_the_heap_is_stopped_and_the_host_survives():
    """The host receives heap termination and can still inspect and close the page."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(SCRIPT)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    tokens = result.stdout.split()
    assert tokens[0] == "terminated"
    assert tokens[1] == "readyState" and tokens[2] in ("loading", "interactive", "complete")
    assert tokens[3] == "terminations" and int(tokens[4]) >= 1
    assert tokens[5:] == ["closed"]
