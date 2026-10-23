from pyv8c import Browser


def test_performance_can_register_and_dispatch_events():
    browser = Browser(stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body></body>', url='https://example.test/')
        assert page.evaluate("""
            (() => {
              let calls = 0;
              performance.addEventListener('resourcetimingbufferfull', () => calls++);
              performance.dispatchEvent(new Event('resourcetimingbufferfull'));
              return calls;
            })()
        """) == 1
    finally:
        browser.close()


def test_performance_resource_methods_accept_same_origin_frame_receiver():
    browser = Browser(stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body></body>', url='https://example.test/')
        assert page.evaluate("""
            (() => {
              const frame = document.createElement('iframe');
              document.body.appendChild(frame);
              const target = frame.contentWindow.performance;
              performance.setResourceTimingBufferSize.call(target, 1000);
              performance.clearResourceTimings.call(target);
              return true;
            })()
        """) is True
    finally:
        browser.close()
