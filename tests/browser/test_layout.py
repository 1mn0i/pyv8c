"""Layout and computed-style behavior needed by browser scripts."""

import pytest

from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(cache=False, stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content("<body></body>", url="http://layout.test/")
    try:
        yield page
    finally:
        browser.close()


def test_hidden_element_keeps_layout_and_font_size_changes_height(page):
    assert page.evaluate("""(() => {
        const div = document.createElement('div');
        div.style.visibility = 'hidden';
        document.body.appendChild(div);
        div.innerHTML = '<span style="font-family:mmllii;font-size:114px">mmmmmmmmmmmlliii</span>';
        div.style.fontFamily = 'DFPhelvetica';
        return [div.offsetWidth, div.offsetHeight,
            getComputedStyle(div).fontFamily,
            getComputedStyle(div.firstElementChild).fontSize,
            div.getBoundingClientRect().height > 0];
    })()""") == [1280, 137, "DFPhelvetica", "114px", True]


def test_style_rules_and_basic_flex_grid_geometry(page):
    assert page.evaluate("""(() => {
        document.body.innerHTML = '<style>.item { height: 10px } span { color: red }</style>'
            + '<div id="flex" style="display:flex"><div class="item"></div><div class="item"></div></div>'
            + '<span>text</span>';
        const flex = document.querySelector('#flex');
        const span = document.querySelector('span');
        return [flex.offsetHeight, getComputedStyle(span).color,
            span.offsetHeight, flex.clientWidth === flex.offsetWidth];
    })()""") == [10, "red", 19, True]
