"""Layout and computed-style behavior needed by browser scripts."""

import os

import pytest

from pyv8c import Browser
from pyv8c.foundation import system_fonts
from pyv8c.foundation import text as text_engine
from pyv8c.web.dom import layout


@pytest.fixture
def page():
    browser = Browser(cache=False, stub_logging="off")
    page = browser.new_profile().new_page()
    page.set_content("<body></body>", url="http://layout.test/")
    try:
        yield page
    finally:
        browser.close()


def test_a_family_is_measured_with_the_face_that_family_names():
    """One table answers both "is this font here" and "how wide is this text", so a face
    ``local()`` finds is the face the page then measures with."""
    record = next((item for item in system_fonts.records() if item.path), None)
    if record is None:
        pytest.skip("this machine has no fonts to measure with")
    face = layout._face_for([record.family])
    assert face is not None
    assert system_fonts.normalize(face.family) == system_fonts.normalize(record.family)


def test_a_collection_face_is_measured_at_its_own_index():
    """A face inside a ``.ttc`` is a face of its own: measuring it needs the index."""
    record = next((item for item in system_fonts.records() if item.index), None)
    if record is None:
        pytest.skip("this machine has no font collection")
    path = system_fonts.file_for(record)
    assert path is not None
    assert text_engine.shape("A", path, index=record.index, size=16.0).width > 0


def test_adjacent_inline_elements_keep_cross_element_kerning(page):
    if not any(item.family == "Noto Sans SC" and system_fonts.file_for(item)
               for item in system_fonts.records()):
        pytest.skip("Noto Sans SC is not installed")
    assert page.evaluate("""(() => {
        const host = document.createElement('div');
        host.style.cssText = 'white-space:nowrap;font-family:"Noto Sans SC"';
        host.innerHTML = '<span>υ</span><span>τ</span>';
        document.body.appendChild(host);
        return [...host.children].map(child => child.getBoundingClientRect().width);
    })()""") == [8.765625, 8.234375]


def test_windows_cambria_math_uses_hinted_advance():
    if os.name != "nt":
        pytest.skip("Windows font metrics")
    record = next((item for item in system_fonts.records()
                   if item.family == "Cambria Math" and system_fonts.file_for(item)), None)
    if record is None:
        pytest.skip("Cambria Math is not installed")
    assert text_engine.shape("𝔈", system_fonts.file_for(record),
                             index=record.index, size=16).width == 11


def test_an_uninstalled_family_falls_back_instead_of_failing():
    """CSS walks the family list and then uses the platform's default, so a name this
    machine does not have still measures -- which is what an uninstalled font looks like."""
    if not system_fonts.records():
        pytest.skip("this machine has no fonts to measure with")
    assert layout._face_for(["Definitely Not A Font 123"]) is not None
    assert layout.text_width("abc", {"font-family": "Definitely Not A Font 123"}) > 0


def test_nested_layout_reuses_cascade_within_one_pass_and_recomputes_after_change(page, monkeypatch):
    from pyv8c.web.css import cascade
    original = cascade._selector_matches
    calls = 0

    def counted(element, selector):
        nonlocal calls
        calls += 1
        return original(element, selector)

    monkeypatch.setattr(cascade, "_selector_matches", counted)
    page.set_content("<style>div { color: red } .box { padding: 1px }</style>"
                     + "<div class='box'>" * 35 + "x" + "</div>" * 35,
                     url="http://layout.test/")
    calls = 0
    assert page.evaluate("document.querySelector('.box').offsetWidth > 0")
    assert calls < 300  # linear passes rather than every ancestor per element
    assert page.evaluate("""(() => {
        const node = document.querySelector('.box');
        node.style.width = '123px';
        return node.offsetWidth >= 123;
    })()""")


def test_geometry_reuses_boxes_within_one_property_read(page, monkeypatch):
    page.set_content("<div id='box' style='width:40px;height:20px'></div>",
                     url="http://layout.test/")
    original = layout.layout
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(layout, "layout", counted)
    assert page.evaluate("document.getElementById('box').scrollWidth") >= 40
    assert calls == 2  # public lookup plus the snapshot's one full pass
    calls = 0
    page.evaluate("document.getElementById('box').offsetLeft")
    assert calls == 0  # offset lookup reuses the snapshot directly


def test_hit_testing_reuses_layout_styles_and_sees_mutations(page, monkeypatch):
    page.set_content("<style>#box { width: 40px; height: 20px }</style>"
                     "<div id='box'></div>", url="http://layout.test/")
    assert page.evaluate("document.elementFromPoint(10, 10).id") == "box"
    original = layout.computed_style

    def forbidden(*_args, **_kwargs):
        raise AssertionError("hit test computed styles again after layout")

    monkeypatch.setattr(layout, "computed_style", forbidden)
    assert page.evaluate("document.elementFromPoint(10, 10).id") == "box"
    monkeypatch.setattr(layout, "computed_style", original)
    assert page.evaluate("""(() => {
        document.getElementById('box').style.display = 'none';
        return document.elementFromPoint(10, 10).id;
    })()""") != "box"


def test_explicit_empty_rules_remain_empty(page):
    page.set_content("<style>#box { width: 300px }</style><div id='box'></div>",
                     url="http://layout.test/")
    node = page.document.fn_getElementById("box")
    assert layout.layout(node).width == 300
    assert layout.layout(node, rules=()).width != 300


def test_layout_snapshot_reuses_pass_and_invalidates_on_dom_changes(page, monkeypatch):
    page.set_content("<div id='box' style='width:40px;height:20px'>x</div>",
                     url="http://layout.test/")
    original = layout._place
    roots = 0

    def counted(element, *args, **kwargs):
        nonlocal roots
        if element is page.document.get_documentElement():
            roots += 1
        return original(element, *args, **kwargs)

    monkeypatch.setattr(layout, "_place", counted)
    assert page.evaluate("""(() => {
        let b = document.getElementById('box');
        return [b.offsetWidth, b.clientWidth, b.scrollWidth,
                b.getBoundingClientRect().width, document.elementFromPoint(10, 10).id];
    })()""")[:4] == [40, 40, 40, 40]
    assert roots == 1
    assert page.evaluate("document.getElementById('box').offsetWidth") == 40
    assert roots == 1
    assert page.evaluate("""(() => {
        let b = document.getElementById('box');
        b.style.width = '60px';
        return b.offsetWidth;
    })()""") == 60
    assert roots == 2
    assert page.evaluate("""(() => {
        let b = document.getElementById('box');
        b.firstChild.data = 'a longer text';
        return b.offsetWidth;
    })()""") == 60
    assert roots == 3
    assert page.evaluate("""(() => {
        let b = document.getElementById('box');
        b.remove();
        return b.offsetWidth;
    })()""") == 0
    assert roots == 4


def test_layout_snapshot_observes_class_and_viewport(page, monkeypatch):
    page.set_content("<style>.wide { width: 90px }</style><div id='box'></div>",
                     url="http://layout.test/")
    node = page.document.fn_getElementById('box')
    before = layout.layout(node).width
    node.attributes['class'] = 'wide'
    assert layout.layout(node).width == 90
    assert before != 90
    original = layout._viewport_width
    monkeypatch.setattr(layout, '_viewport_width',
                        lambda element: (400.0, original(element)[1]))
    assert layout.layout(page.document.get_body()).width == 384


def test_layout_snapshot_observes_stylesheet_fonts_and_navigation(page):
    page.set_content("<style>#box { width: 40px }</style><div id='box'></div>",
                     url="http://layout.test/")
    node = page.document.fn_getElementById('box')
    assert layout.layout(node).width == 40
    old_snapshot = page.document._layout_snapshot
    assert page.evaluate("""(() => {
        document.querySelector('style').textContent = '#box { width: 70px }';
        return document.getElementById('box').offsetWidth;
    })()""") == 70
    assert page.document._layout_snapshot is not old_snapshot

    fonts = page.document.get_fonts()
    face = page.evaluate("new FontFace('LayoutTest', 'local(Arial)')")
    before = page.document.layout_version
    fonts.fn_add(face)
    assert page.document.layout_version > before
    assert page.document._layout_snapshot is None

    previous = page.document
    page.set_content("<div id='box' style='width:25px'></div>",
                     url="http://layout.test/next")
    assert page.document is not previous
    assert page.evaluate("document.getElementById('box').offsetWidth") == 25


def test_layout_snapshot_starts_fresh_at_event_boundary(page):
    page.set_content("<button id='box' style='width:40px;height:20px'>ok</button>",
                     url="http://layout.test/")
    assert page.evaluate("document.getElementById('box').offsetWidth") == 40
    previous = page.document._layout_snapshot
    assert previous is not None
    page.dispatch_event('click', selector='#box')
    assert page.document._layout_snapshot is None
    assert page.evaluate("document.getElementById('box').offsetWidth") == 40
    assert page.document._layout_snapshot is not previous


def test_link_sheet_attribute_change_reparses_rules(page):
    page.set_content("<link id='sheet' rel='stylesheet'><div id='box'></div>",
                     url="http://layout.test/")
    sheet = page.document.fn_getElementById('sheet')
    box = page.document.fn_getElementById('box')
    sheet._stylesheet_text = '#box { width: 70px }'
    page.document.style_version += 1
    assert layout.layout(box).width == 70
    sheet.attributes['rel'] = 'alternate'
    assert layout.layout(box).width != 70


def test_hidden_element_keeps_layout_and_font_size_changes_height(page):
    # Chrome uses the profile's default font for the unknown family; its line is 165px.
    assert page.evaluate("""(() => {
        const div = document.createElement('div');
        div.style.visibility = 'hidden';
        document.body.appendChild(div);
        div.innerHTML = '<span style="font-family:mmllii;font-size:114px">mmmmmmmmmmmlliii</span>';
        div.style.fontFamily = 'DFPhelvetica';
        return [div.offsetWidth === document.body.clientWidth, div.offsetHeight,
            getComputedStyle(div).fontFamily,
            getComputedStyle(div.firstElementChild).fontSize,
            div.getBoundingClientRect().height > 0];
    })()""") == [True, 165, "DFPhelvetica", "114px", True]


def test_the_cascade_orders_by_importance_and_specificity(page):
    """Important beats plain, and specificity beats the order the rules were written in."""
    assert page.evaluate("""(() => {
        document.body.innerHTML =
            '<style>#a { height: 9px } div { height: 1px }'
            + ' .b { height: 3px !important } .b { height: 7px }</style>'
            + '<div id="a"></div><div class="b" style="height:5px"></div>';
        return [document.getElementById('a').offsetHeight,
                document.querySelector('.b').offsetHeight];
    })()""") == [9, 3]


def test_a_fixed_box_measures_from_the_viewport(page):
    """A fixed box measures against the viewport, and has no offset parent."""
    assert page.evaluate("""(() => {
        document.body.innerHTML = '<div style="position:relative;padding:40px">'
            + '<div id="t" style="position:fixed;left:0;top:0;width:10px;height:10px"></div>'
            + '</div>';
        const box = document.getElementById('t');
        const rect = box.getBoundingClientRect();
        return [rect.left, rect.top, String(box.offsetParent)];
    })()""") == [0, 0, "null"]


def test_auto_margins_centre_the_box(page):
    """``margin: auto`` shares the free space, so a declared width sits in the middle."""
    assert page.evaluate("""(() => {
        document.body.innerHTML = '<div id="t" style="width:100px;height:10px;margin:0 auto"></div>';
        const box = document.getElementById('t');
        const left = box.getBoundingClientRect().left - document.body.getBoundingClientRect().left;
        return [left, (document.body.clientWidth - 100) / 2, left === (document.body.clientWidth - 100) / 2];
    })()""")[2] is True


def test_style_rules_and_basic_flex_grid_geometry(page):
    assert page.evaluate("""(() => {
        document.body.innerHTML = '<style>.item { height: 10px } span { color: red }</style>'
            + '<div id="flex" style="display:flex"><div class="item"></div><div class="item"></div></div>'
            + '<span>text</span>';
        const flex = document.querySelector('#flex');
        const span = document.querySelector('span');
        return [flex.offsetHeight, getComputedStyle(span).color,
            span.offsetHeight, flex.clientWidth === flex.offsetWidth];
    })()""") == [10, "rgb(255, 0, 0)", 24, True]


def test_nowrap_inline_width_includes_inter_element_space(page):
    assert page.evaluate("""(() => {
      document.body.innerHTML = '<span style="white-space:nowrap">'
        + '<b>first</b> <b>second</b></span>';
      const span = document.querySelector('span');
      const children = span.querySelectorAll('b');
      return span.getBoundingClientRect().width >
        children[0].getBoundingClientRect().width +
        children[1].getBoundingClientRect().width;
    })()""") is True


def test_css_rotation_changes_client_rect_but_not_offset_size(page):
    assert page.evaluate("""(() => {
      const box = document.createElement('div');
      box.style.cssText = 'width:16px;height:15px;transform:rotate(13deg)';
      document.body.appendChild(box);
      const rect = box.getBoundingClientRect();
      return [box.offsetWidth, box.offsetHeight, rect.width, rect.height];
    })()""") == [16, 15, 18.964187622070312, 18.214767456054688]


def test_absolute_css_units_have_chrome_used_pixel_sizes(page):
    assert page.evaluate("""(() => {
      const parent = document.createElement('div');
      parent.style.cssText = 'position:fixed;left:-99999px;top:0;width:0;height:0;'
          + 'visibility:hidden;pointer-events:none;contain:strict';
      document.body.appendChild(parent);
      return ['1.03in', '2.6162cm', '74.16pt', '1.0053in'].map(value => {
        const box = document.createElement('div');
        box.style.cssText = `position:absolute;width:${value};height:${value}`;
        parent.appendChild(box);
        return [box.style.width, getComputedStyle(box).width,
                box.getBoundingClientRect().width, box.offsetWidth];
      });
    })()""") == [
        ['1.03in', '98.875px', 98.875, 99],
        ['2.6162cm', '98.875px', 98.875, 99],
        ['74.16pt', '98.875px', 98.875, 99],
        ['1.0053in', '96.5px', 96.5, 97],
    ]


def test_css_math_functions_produce_used_widths(page):
    assert page.evaluate("""(() => {
      return ['calc(1px * pi)', 'calc(1px * e)',
              'calc(1px * sqrt(16))',
              'calc(1px * (1 + sin(0)))', 'calc(1px * tan(0.5))'].map(value => {
        const box = document.createElement('div');
        box.style.width = value;
        document.body.appendChild(box);
        return [box.style.width, getComputedStyle(box).width,
                box.getBoundingClientRect().width];
      });
    })()""") == [
        ['calc(3.14159px)', '3.14062px', 3.140625],
        ['calc(2.71828px)', '2.70312px', 2.703125],
        ['calc(4px)', '4px', 4],
        ['calc(1px)', '1px', 1],
        ['calc(0.546302px)', '0.53125px', 0.53125],
    ]


def test_css_math_keeps_used_precision_through_cssom_writes(page):
    assert page.evaluate("""(() => {
      const box = document.createElement('div');
      box.style.width = 'calc(1px * (12345.678901234))';
      document.body.appendChild(box);
      const read = () => [box.style.width, getComputedStyle(box).width,
                          box.getBoundingClientRect().width];
      const result = [read()];
      box.style.color = 'red';
      result.push(read());
      box.setAttribute('style', 'width:calc(2px)');
      result.push(read());
      return result;
    })()""") == [
        ['calc(12345.7px)', '12345.7px', 12345.671875],
        ['calc(12345.7px)', '12345.7px', 12345.671875],
        ['calc(2px)', '2px', 2],
    ]


def test_nested_css_math_keeps_blink_trigonometry_and_layout_units(page):
    assert page.evaluate("""(() => {
      const expressions = [
        'calc( 1px * ( ( 2.71828 * 0.987654321 - 0.123456789 / ( 987654.321 * 12345.678 ) ) + 0.5555555 / sin( sin( 10000.1 * tan( 50000 ) / tan( 20000 ) + 1.0 / pi * 5.0 - 0.1111 ) / 100.0 + tan( 30000 + 40000 * 50000 + 0.0001 ) / 9999.9 * pi ) - 0.999999 * -100000.5 ) )',
        'calc( 1px * ( ( 3.14159 * 0.543210987 - 0.987654321 / ( 123456.789 * 98765.432 ) ) + 0.8888888 / cos( cos( 12345.6 * tan( 65432.1 ) / tan( 10987.6 ) + 2.0 / pi * 7.0 - 0.2222 ) / 200.0 + sqrt( 50000 + 60000 * 70000 + 0.0002 ) / 8888.8 * pi ) - 0.777777 * -200000.7 ) )',
      ];
      return expressions.map(expression => {
        const box = document.createElement('div');
        box.style.cssText = 'container-type:inline-size;padding:10px;'
            + 'text-align:center;font-weight:bold;box-sizing:content-box';
        box.style.width = expression;
        box.textContent = '$#QKmn__gabenforgiveme';
        document.body.appendChild(box);
        return [box.style.width, getComputedStyle(box).width,
                box.getBoundingClientRect().width];
      });
    })()""") == [
        ['calc(100075px)', '100075px', 100094.984375],
        ['calc(155556px)', '155556px', 155576.203125],
    ]


def test_default_and_variable_font_weights_match_chrome_geometry(page):
    assert page.evaluate("""(() => {
      const result = [];
      for (const weight of ['400', '700']) {
        const box = document.createElement('div');
        box.style.cssText = 'position:absolute;font-family:"Noto Sans SC";font-weight:'
            + weight;
        box.textContent = 'a';
        document.body.appendChild(box);
        result.push([getComputedStyle(box).fontWeight,
                     getComputedStyle(box).width,
                     box.getBoundingClientRect().width]);
      }
      return result;
    })()""") == [
        ['400', '9.01562px', 9.015625],
        ['700', '9.46875px', 9.46875],
    ]


def test_root_client_box_uses_viewport_even_with_empty_body(page):
    assert page.evaluate("""[document.documentElement.clientWidth === innerWidth,
      document.documentElement.clientHeight === innerHeight,
      document.body.clientHeight]""") == [True, True, 0]


def test_negative_absolute_positions_keep_computed_precision_and_quantize_geometry(page):
    assert page.evaluate("""(() => {
      const box = document.createElement('div');
      box.style.cssText = 'position:fixed;left:-1.0053in;top:-1.03in;'
          + 'width:1in;height:1in';
      document.body.appendChild(box);
      const style = getComputedStyle(box);
      const rect = box.getBoundingClientRect();
      return [style.left, style.top, rect.left, rect.top];
    })()""") == ['-96.5088px', '-98.88px', -96.5, -98.875]


def test_rect_applies_transform_origin_and_ancestor_transform(page):
    assert page.evaluate('''(() => {
        const outer = document.createElement('div');
        outer.style.cssText = 'position:absolute;left:0;top:0;transform:translateX(100px)';
        const inner = document.createElement('div');
        inner.style.cssText = 'width:100px;height:50px;transform:rotate(90deg);transform-origin:0 0';
        outer.appendChild(inner); document.body.appendChild(outer);
        const r = inner.getBoundingClientRect();
        return [r.left, r.top, r.width, r.height];
    })()''') == [50, 0, 50, 100]
