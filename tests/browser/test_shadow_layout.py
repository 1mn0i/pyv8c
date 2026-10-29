"""Rendered shadow-tree geometry and trusted input."""
from pyv8c import Browser


def test_shadow_styles_layout_invalidation_and_document_hit_retargeting():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<style>input { width: 80px }</style><body></body>',
                         url='https://example.test/')
        assert page.evaluate('''(() => {
            const host = document.createElement('div'); document.body.append(host);
            const root = host.attachShadow({mode: 'open'});
            root.innerHTML = '<style>input { width: 24px; height: 20px }</style>' +
                             '<input type="checkbox">';
            const input = root.querySelector('input');
            const rect = input.getBoundingClientRect();
            const result = [rect.width, rect.height,
                document.elementFromPoint(rect.x + 1, rect.y + 1) === host];
            root.querySelector('style').textContent = 'input {width: 30px; height: 22px}';
            result.push(input.getBoundingClientRect().width, input.getBoundingClientRect().height);
            host.remove();
            result.push(input.getBoundingClientRect().width);
            return result;
        })()''') == [24, 20, True, 30, 22, 0]
    finally:
        browser.close()


def test_mouse_click_reaches_checkbox_in_closed_shadow_tree():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('<body></body>', url='https://example.test/')
        page.evaluate('''(() => {
            const root = document.body.attachShadow({mode:'closed'});
            root.innerHTML = '<style>input {width:24px;height:20px}</style>' +
                             '<input type="checkbox">';
            window.clicks = [];
            root.querySelector('input').addEventListener('click', e => {
                clicks.push([e.isTrusted, e.target.checked]);
            });
        })()''')
        checkbox = page.realm.executor.call(
            lambda: page.document.get_body()._shadow_root.fn_querySelector('input'))
        page.mouse.click_element(checkbox)
        assert page.evaluate('clicks') == [[True, True]]
    finally:
        browser.close()


def test_absolute_percent_height_uses_auto_height_positioned_grid():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('''<body><div id="outer" style="position:relative;display:grid;width:120px">
            <span style="display:block;width:40px;height:24px"></span>
            <input id="target" type="checkbox" style="position:absolute;left:0;top:0;
                width:100%;height:100%;margin:0"></div></body>''', url='https://example.test/')
        assert page.evaluate('''(() => {
            const a = document.querySelector('#target').getBoundingClientRect();
            const b = document.querySelector('#outer').getBoundingClientRect();
            return [a.width, a.height, b.width, b.height];
        })()''') == [120, 24, 120, 24]
    finally:
        browser.close()


def test_iframe_viewport_tracks_its_content_box_and_resize():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('''<body><iframe style="width:300px;height:65px;
                          padding:10px;border:2px solid"></iframe></body>''',
                         url='https://example.test/')
        assert page.evaluate('''(() => {
            const frame = document.querySelector('iframe');
            const child = frame.contentWindow;
            const first = [child.innerWidth, child.innerHeight, frame.clientWidth,
                           frame.clientHeight, child.document.documentElement.clientWidth,
                           child.visualViewport.width, child.outerWidth === window.outerWidth];
            frame.style.width = '120px'; frame.style.height = '40px';
            return [first, [child.innerWidth, child.innerHeight,
                            child.document.documentElement.clientWidth]];
        })()''') == [[300, 65, 320, 85, 300, 300, True], [120, 40, 120]]
    finally:
        browser.close()


def test_flex_item_with_implicit_grid_tracks_uses_content_width():
    browser = Browser(real_network=False, stub_logging='off')
    try:
        page = browser.new_profile().new_page()
        page.set_content('''<body><div style="display:flex;width:300px"><div style="display:grid">
            <div style="display:flex"><label style="display:grid;position:relative">
            <input type="checkbox" style="position:absolute;width:100%;height:100%;margin:0">
            <span style="grid-area:1/1;width:24px;height:24px"></span>
            <span style="grid-column:2;margin-left:8px;width:98px;height:18px"></span>
            </label></div></div></div></body>''', url='https://example.test/')
        assert page.evaluate('''(() => {
            const a = document.querySelector('input').getBoundingClientRect();
            const b = document.querySelector('label').getBoundingClientRect();
            return [a.width, a.height, b.width, b.height];
        })()''') == [130, 24, 130, 24]
    finally:
        browser.close()
