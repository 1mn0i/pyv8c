"""Media queries exercised by the Costco frame, compared with local Chrome 155."""

from pyv8c import Browser


def test_resolution_screen_and_desktop_media_features():
    browser = Browser(real_network=False, stub_logging="off", environment={
        "viewport.width": 1280, "viewport.height": 720,
        "screen.width": 2560, "screen.height": 1080,
        "viewport.device_scale_factor": 1,
    })
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        assert page.evaluate("""(() => {
            const queries = [
                '(min-resolution:0.625dppx)', '(min-resolution:5dppx)',
                '(min-resolution:5.960464477539062e-7dppx)',
                '(min-resolution:62.5dpi)', '(min-resolution:125dpi)',
                '(min-resolution:25dpcm)', '(min-resolution:50dpcm)',
                '(resolution = 0dpi)', '(width = 0px)', '(device-width = 0px)',
                '(aspect-ratio = 0.25)', '(device-aspect-ratio = 0.25)',
                '(color = 0)', '(color-index = 0)',
                '(dynamic-range: standard)', '(video-dynamic-range: standard)',
                '(prefers-reduced-transparency: no-preference)',
                '(device-posture: continuous)', '(orientation: landscape)'
            ];
            return queries.map(query => matchMedia(query).matches);
        })()""") == [True, False, True, True, False, True, False,
                    False, False, False, False, False, False, True,
                    True, False, True, True, True]
    finally:
        browser.close()


def test_media_query_list_initializes_event_target_and_uses_dpr():
    browser = Browser(real_network=False, stub_logging="off", environment={
        "viewport.device_scale_factor": 2,
    })
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="https://example.test/")
        assert page.evaluate("""(() => {
            const query = '(min-resolution:2dppx)';
            const list = matchMedia(query);
            const listener = () => {};
            list.addEventListener('change', listener);
            list.removeEventListener('change', listener);
            list.addListener(listener);
            list.removeListener(listener);
            list.onchange = listener;
            return [list.matches, list.media, list.onchange === listener,
                    matchMedia('(min-resolution:2.1dppx)').matches,
                    matchMedia('(resolution:192dpi)').matches];
        })()""") == [True, "(min-resolution:2dppx)", True, False, True]
    finally:
        browser.close()


def test_dpcm_rounds_to_hundredths_of_dppx():
    browser = Browser(real_network=False, stub_logging="off", environment={
        "viewport.device_scale_factor": 1,
    })
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate("""[
            matchMedia('(min-resolution:37.89dpcm)').matches,
            matchMedia('(min-resolution:37.99dpcm)').matches,
            matchMedia('(max-resolution:37.89dpcm)').matches,
            matchMedia('(min-resolution:96.1dpi)').matches
        ]""") == [True, False, True, False]
    finally:
        browser.close()
