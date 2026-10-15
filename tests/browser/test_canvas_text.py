"""A canvas measures text with the same font table the layout does.

``measureText`` is a page-readable measurement, so it reads the profile's fonts too: a
canvas width and the width of the same text in a box have to agree, or a page can tell
one engine from another by comparing them.
"""

import pytest

from pyv8c import Browser
from pyv8c.foundation import system_fonts
from pyv8c.foundation import text as text_engine
from pyv8c.foundation.environment import BrowserEnvironment
from pyv8c.web.dom import layout

TEXT = "mmmmmmmmmm"

MEASURE = """
(() => {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = %s;
  return ctx.measureText(%s).width;
})()
"""


def _measure(environment=None, font='16px "Arial"'):
    import json

    browser = Browser(cache=False, stub_logging="off", real_network=False,
                      environment=environment)
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="http://canvas.test/")
        return page.evaluate(MEASURE % (json.dumps(font), json.dumps(TEXT)))
    finally:
        browser.close()


def _latin_family():
    record = next((item for item in system_fonts.records()
                   if item.family and item.path and item.style == "Regular"), None)
    if record is None:
        pytest.skip("this machine has no regular face to measure with")
    return record.family


def test_a_canvas_width_is_the_width_the_same_text_has_in_a_box():
    family = _latin_family()
    measured = _measure(font=f'16px "{family}"')
    expected = layout.text_width(TEXT, {"font-size": "16px", "font-family": family},
                                 BrowserEnvironment())
    assert measured == pytest.approx(expected, abs=0.5), (measured, expected)


def test_a_canvas_follows_a_profile_that_carries_its_own_fonts():
    """The point of the shared table: a profile changes what a canvas measures, too."""
    fake = {"postscript_name": "FakeOnly-Regular", "full_name": "FakeOnly",
            "family": "FakeOnly", "style": "Regular",
            "path": r"C:\Windows\Fonts\consola.ttf", "index": 0}
    environment = BrowserEnvironment({"fonts.records": [fake]})
    measured = _measure(environment={"fonts.records": [fake]}, font='16px "FakeOnly"')
    expected = layout.text_width(TEXT, {"font-size": "16px", "font-family": "FakeOnly"},
                                 environment)
    assert measured == pytest.approx(expected, abs=0.5), (measured, expected)
    # The same canvas with the machine's own table measures something else for that name:
    # it has no such face, so the default family stands in.
    assert _measure(font='16px "FakeOnly"') != pytest.approx(measured, abs=0.5)


def test_a_family_no_table_knows_measures_like_the_standard_font():
    """CSS falls back to the standard font, which is the serif family and not the sans one.

    Chrome measures a family nothing knows with Times New Roman for that reason; measuring
    it with Arial instead was the difference this test exists to notice.
    """
    unknown = _measure(font='16px "Definitely Not A Font 123"')
    standard = _measure(font='16px serif')
    assert unknown == pytest.approx(standard, abs=0.5)


def test_a_point_size_is_read_as_pixels_at_96_dpi():
    """``12pt`` is 16px, which is the conversion the layout uses as well."""
    assert _measure(font='12pt "Arial"') == pytest.approx(
        _measure(font='16px "Arial"'), abs=0.5)


BOXES = """
(() => {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = %s;
  const m = ctx.measureText(%s);
  return { width: m.width, left: m.actualBoundingBoxLeft, right: m.actualBoundingBoxRight,
           ascent: m.actualBoundingBoxAscent, descent: m.actualBoundingBoxDescent,
           fontAscent: m.fontBoundingBoxAscent, fontDescent: m.fontBoundingBoxDescent,
           emAscent: m.emHeightAscent, emDescent: m.emHeightDescent,
           hanging: m.hangingBaseline, alphabetic: m.alphabeticBaseline,
           ideographic: m.ideographicBaseline };
})()
"""


def _boxes(font, text=TEXT):
    import json

    browser = Browser(cache=False, stub_logging="off", real_network=False)
    try:
        page = browser.new_profile().new_page()
        page.set_content("<body></body>", url="http://canvas.test/")
        return page.evaluate(BOXES % (json.dumps(font), json.dumps(text)))
    finally:
        browser.close()


def test_the_boxes_come_from_the_face_that_was_measured_with():
    family = _latin_family()
    answer = _boxes(f'16px "{family}"')
    record = layout._face_for([family], BrowserEnvironment())
    path = system_fonts.file_for(record)
    assert path is not None
    found = text_engine.metrics(path, index=record.index, size=16.0)
    assert answer["fontAscent"] == found.ascent, answer
    assert answer["fontDescent"] == found.descent, answer
    assert answer["ascent"] > 0, answer
    # The ink is the glyphs' own box counted from the origin, so its right edge lands on
    # either side of the advance: Chrome reads 1..9 for "x" and a right edge past the advance
    # for Times.  Hinting is on this engine's not-aligned list, so the box is held to the
    # relation rather than to the last decimal Chrome reports.
    assert -2 <= answer["left"] <= 2, answer
    assert answer["right"] >= answer["width"] - 2, answer


def test_an_empty_run_has_the_face_box_and_no_ink():
    answer = _boxes('16px "Arial"', text="")
    assert answer["width"] == 0 and answer["ascent"] == 0 and answer["descent"] == 0, answer
    assert answer["fontAscent"] > 0, answer


def test_the_baselines_are_the_known_zero():
    """The surface publishes ten members; the em-height pair and the three baselines stay
    zero because that is what a page reads from Chrome 155 as well."""
    answer = _boxes('16px "Arial"')
    assert answer["hanging"] == 0 and answer["alphabetic"] == 0, answer
    assert answer["ideographic"] == 0, answer
    # The em-height pair is not one of the ten members this surface publishes, and Chrome
    # 155 answers undefined for it as well -- so the reading is not a number, here or there.
    assert not isinstance(answer["emAscent"], (int, float)), answer
    assert not isinstance(answer["emDescent"], (int, float)), answer
