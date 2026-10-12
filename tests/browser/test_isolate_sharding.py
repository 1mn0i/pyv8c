"""Which pages share a V8 isolate: ``Browser(shard_isolates=True)`` gives each page tree its
own, so its scripts run while another page's do (measured 3.70x for four pages on sixteen
cores, against 1.00x shared).

A document and its frames must keep sharing one: their scripts reach each other's objects
directly.  So a frame's realm is built on the inserting page's own thread, under the isolate
that script still holds -- building it on a second thread deadlocks, which is what the last
test guards.
"""
import threading
import time

from pyv8c import Browser


def _pages(browser, count):
    context = browser.new_context()
    pages = [context.new_page() for _ in range(count)]
    for page in pages:
        page.set_content("<body>x</body>")
    return pages


def _bounded(action, seconds=25.0):
    """Run something that may wedge a realm, and answer whether it finished.

    A deadlocked realm holds its owner thread forever, so the test must not wait for it.
    """
    done = threading.Event()

    def run():
        try:
            action()
        except BaseException:  # noqa: BLE001 - the assertion is about finishing at all
            pass
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(seconds)


def _pump(page, seconds=0.4):
    """Let the queue turn: an inserted frame's realm is built on the next task."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001 - a JS error on the way is not this test's
            pass
        time.sleep(0.01)


def _child_page(page):
    frame = page.document.fn_querySelectorAll("iframe")[0]
    return frame._child_services._page_realm.page


def test_pages_share_the_process_isolate_by_default():
    browser = Browser(stub_logging="off")
    try:
        pages = _pages(browser, 3)
        assert len({page.context.isolate_id for page in pages}) == 1
    finally:
        browser.close()


def test_sharded_pages_get_an_isolate_each():
    browser = Browser(stub_logging="off", shard_isolates=True)
    try:
        pages = _pages(browser, 3)
        assert len({page.context.isolate_id for page in pages}) == 3
    finally:
        browser.close()


def test_a_frame_shares_the_isolate_of_its_page_tree():
    browser = Browser(stub_logging="off", shard_isolates=True)
    try:
        page = _pages(browser, 1)[0]
        page.evaluate("document.body.innerHTML = '<iframe srcdoc=\"child\"></iframe>'")
        _pump(page)
        assert _child_page(page).context.isolate_id == page.context.isolate_id
        # What the sharing is for: the child's own objects, reached live from the parent.
        assert page.evaluate("""(() => {
            const child = window.frames[0];
            child.reached = 'live';
            return String(child.document.body.textContent) + ' / ' + String(child.reached);
        })()""") == "child / live"
    finally:
        browser.close()


def test_a_script_inserted_frame_initializes_while_its_isolate_is_held():
    browser = Browser(stub_logging="off", shard_isolates=True)
    try:
        page = browser.new_context().new_page()
        page.set_content("<body>x</body>")
        assert _bounded(lambda: page.evaluate(
            "document.body.innerHTML = '<iframe></iframe>'"))
        _pump(page)
        assert page.evaluate("window.frames.length") == 1
    finally:
        _bounded(browser.close)


def test_a_sharded_isolate_is_closed_with_its_page():
    """The isolate outlives navigation but not the page: a leak would keep its heap alive."""
    browser = Browser(stub_logging="off", shard_isolates=True)
    try:
        page = _pages(browser, 1)[0]
        isolate = page.isolate
        page.evaluate("document.body.innerHTML = '<iframe></iframe>'")
        _pump(page)
        page.close()
        assert isolate.closed
    finally:
        browser.close()
