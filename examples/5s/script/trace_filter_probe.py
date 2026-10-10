"""Does ``trace_callback`` reach every realm, and can it filter the log?

    python script/trace_filter_probe.py

The trace costs ~100x, so the point of ``trace_callback`` is to drop the noise *before* it is
logged -- one global name is read 670k times in this challenge.  ``script/5s.py --trace-only``
uses it that way, and the log still came out 132 MB, so this measures the mechanism itself,
locally and without third-party traffic:

* how many events the callback is handed (it is the only filter);
* whether the engine's own ``log_trace`` still runs behind it (the default sink would print
  every line: any ``engine.trace`` line in stdout means the filter was bypassed);
* which realms' events arrive at all (page, frame, worker).
"""
import sys
import time

from loguru import logger

from pyv8c import Browser

PAGE = (
    "<!doctype html><body>"
    "<script>"
    "window.__seen = [];"
    "var reflect = window.Reflect, math = window.Math;"
    "var frame = document.createElement('iframe');"
    "frame.setAttribute('srcdoc', '<body><script>var r = window.Reflect;</scr' + 'ipt>');"
    "document.body.appendChild(frame);"
    "</" + "script>"
    "</body>"
)


def callback(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Record every event and log none of them: a filter that works leaves no trace lines."""
    SEEN.append((ctx_type, event, str(name)))
    return None


SEEN = []


def load(url, request):
    return {"body": PAGE, "headers": {"content-type": "text/html"}}


if __name__ == "__main__":
    #: A sink that shows whether anything bypassed the callback.  INFO, not DEBUG: the trace lines
    #: themselves are INFO (``log_trace`` logs at INFO), so they still show as a leak, while the
    #: engine's own DEBUG firehose (every dispatch, every timer) stays out of the way.
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{message}")

    browser = Browser(stub_logging="off", real_network=False, resource_loader=load,
                      trace=True, traceable=True, trace_callback=callback,
                      threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        page.goto("http://filter.test/")
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.1)
        #: ``realms.py`` toggles ``context.trace`` around publishing a window's name (it does not
        #: want that read traced).  If that assignment re-installs the default logger, a filtered
        #: run floods anyway -- and the callback sees nothing to say so, because it is still the
        #: callback the realm was *created* with.
        before = len(SEEN)
        page.trace = False
        page.trace = True
        page.evaluate("[window.Reflect, window.Math, window.JSON].length")
        deadline = time.time() + 0.6
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.1)
        print(f"events before the toggle: {before}; after: {len(SEEN)}")
    finally:
        browser.close()

    realms = {}
    for ctx_type, event, name in SEEN:
        realms.setdefault(str(ctx_type), 0)
        realms[str(ctx_type)] += 1
    print(f"events handed to the callback: {len(SEEN)}")
    print(f"realms: {realms}")
    print(f"names seen: {sorted({name for _, _, name in SEEN})[:12]}")
    leaked = [entry for entry in SEEN if False]  # placeholder: the leak check is the log itself
    print("if any 'engine.trace' line appears above, the callback was bypassed", leaked[:0])
