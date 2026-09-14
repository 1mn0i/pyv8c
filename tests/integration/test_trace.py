"""Actual browser trace events, rather than Context constructor arguments."""

import pytest
from loguru import logger

from pyv8c import Browser


def test_browser_events_lazy_interfaces_and_page_override():
    events = []
    browser = Browser(trace=True, trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        events.clear()
        assert page.evaluate("""(() => {
            const element = document.createElement('div');
            element.id = 'traced';
            const lazy = new AbortController();
            lazy.extra = 42;
            lazy.abort();
            return [element.id, lazy.extra, new Event('ready').type];
        })()""") == ['traced', 42, 'ready']
        assert any(event[0] == 'method' and event[2] == 'createElement' for event in events)
        assert any(event[0] == 'set' and event[2] == 'id' and event[4] == 'traced' for event in events)
        assert any(event[0] == 'get' and event[2] == 'id' and event[4] == 'traced' for event in events)
        assert any(event[0] == 'construct' and event[2] == 'Event' for event in events)
        assert any(event[0] == 'method' and event[2] == 'abort' for event in events)
        assert any(event[2] == 'extra' for event in events)
        for name in page.binding_session.materialized_interfaces:
            cls = page.binding_session.class_for(name)
            assert not any(key.endswith('_hook__') for base in cls.__mro__ for key in base.__dict__)
        quiet = browser.new_context().new_page(trace=False)
        events.clear()
        quiet.evaluate("document.createElement('div').id")
        assert events == []
    finally:
        browser.close()


def test_default_browser_sink_really_logs():
    messages = []
    sink = logger.add(lambda message: messages.append(message.record['message']))
    browser = Browser(trace=True, stub_logging="off")
    try:
        page = browser.new_context().new_page()
        messages.clear()
        page.evaluate("document.createElement('div').id")
        assert any('method: Document.createElement' in message for message in messages)
        assert any('getter: HTMLDivElement.id' in message for message in messages)
    finally:
        browser.close()
        logger.remove(sink)


def test_worker_inherits_context_sink_without_faker_switch():
    events = []
    browser = Browser(trace=True, trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.evaluate("""(() => {
            const source = new Blob(["postMessage(new Event('worker-trace').type)"], {type:'text/javascript'});
            const worker = new Worker(URL.createObjectURL(source));
            worker.onmessage = event => window.workerResult = event.data;
        })()""")
        page.run_until_idle(timeout=5)
        assert page.evaluate("workerResult") == 'worker-trace'
        assert any(event[7] == 'Worker' and event[0] == 'construct' and event[2] == 'Event' for event in events)
    finally:
        browser.close()


def test_trace_keeps_cross_origin_access_denied():
    browser = Browser(trace=True, trace_callback=lambda *event: None, stub_logging="off",
                      resource_loader=lambda url, request: {'body': '<body>child</body>', 'url': url}, cache=False)
    try:
        page = browser.new_context().new_page()
        page.set_content('<iframe src="http://b.test/child"></iframe>', url='http://a.test/main')
        page.run_until_idle(timeout=2)
        assert page.evaluate("try { window[0].document; } catch(error) { error.name; }") == 'SecurityError'
    finally:
        browser.close()


@pytest.mark.parametrize("initial", [False, True])
def test_page_trace_can_pause_and_resume(initial):
    events = []
    browser = Browser(trace=initial, traceable=True,
                      trace_callback=lambda *event: events.append(event), stub_logging="off")
    try:
        page = browser.new_context().new_page()
        quiet = browser.new_context().new_page(trace=False)
        page.evaluate("window.element = document.createElement('div')")
        original_context = page.context
        for enabled in (True, False, True, False):
            page.trace = enabled
            events.clear()
            assert page.evaluate("element.id='value';element.id") == 'value'
            assert bool(events) is enabled
            assert page.trace is enabled
            assert page.context.trace is enabled
            assert page.services.trace is enabled
            assert page.context is original_context
            events.clear()
            quiet.evaluate("document.createElement('div').id")
            assert events == []
        with pytest.raises(TypeError, match='bool'):
            page.trace = 1
        page.close()
        with pytest.raises(RuntimeError):
            page.trace = True
    finally:
        browser.close()


def test_page_without_instrumentation_rejects_activation():
    browser = Browser(stub_logging='off')
    try:
        page = browser.new_context().new_page()
        with pytest.raises(RuntimeError, match='traceable=True'):
            page.trace = True
        assert page.trace is False
        assert page.services.trace is False
    finally:
        browser.close()


def test_invalid_browser_sink():
    with pytest.raises(TypeError, match='trace_callback'):
        Browser(trace_callback=42)
