"""A computed font-size is absolute, and relative lengths resolve where the parent is known."""

import pytest

from pyv8c import Browser
from pyv8c.web.dom import layout


@pytest.fixture
def page():
    browser = Browser(cache=False, stub_logging="off")
    page = browser.new_profile().new_page()
    try:
        yield page
    finally:
        browser.close()


@pytest.mark.parametrize("declared, parent, root, expected", (
    ("16px", 16, 16, 16),
    ("32px", 16, 16, 32),
    ("2em", 16, 16, 32),
    ("2em", 24, 24, 48),
    ("150%", 16, 16, 24),
    ("150%", 24, 24, 36),
    ("1.5rem", 16, 20, 30),
    ("12pt", 16, 16, 16),
    ("1in", 16, 16, 96),
    ("small", 16, 16, 13),
    ("medium", 16, 16, 16),
    ("large", 16, 16, 18),
    ("smaller", 16, 16, 13.333333333333334),
    ("larger", 16, 16, 19.2),
    ("calc(10px + 1em)", 16, 16, 26),
    ("calc(2em - 10px)", 20, 20, 30),
    ("nonsense", 16, 16, 16),
    ("calc(2 * 10px)", 16, 16, 16),
    ("", 16, 16, 16),
))
def test_a_size_resolves_where_css_says_it_does(declared, parent, root, expected):
    assert layout._font_size_px(declared, parent, root) == pytest.approx(expected)


def test_a_computed_size_is_written_the_way_css_writes_it():
    assert layout._px_text(32) == "32px"
    assert layout._px_text(16) == "16px"
    assert layout._px_text(13.333333333333334) == "13.3333px"
    assert layout._px_text(19.2) == "19.2px"


def test_the_relative_sizes_of_a_real_tree_match_the_computed_value(page):
    """The value a page reads and the line box it makes come from the same resolution."""
    page.set_content("""
      <body style="font-size:16px;margin:0;font-family:Arial">
        <div id="em" style="margin:0;font-size:2em">x</div>
        <div id="pct" style="margin:0;font-size:150%">x</div>
        <div id="smaller" style="margin:0;font-size:smaller">x</div>
        <div id="outer" style="margin:0;font-size:24px">
          <span id="inner" style="font-size:150%">x</span>
        </div>
      </body>
    """, url="http://font-size.test/")
    answer = page.evaluate("""(() => {
      const read = (id) => [getComputedStyle(document.getElementById(id)).fontSize,
                            document.getElementById(id).offsetHeight];
      return { em: read('em'), pct: read('pct'), smaller: read('smaller'),
               inner: read('inner') };
    })()""")
    # Chrome 155 on the same machine, reading the same page: the line box is the face's own
    # metrics rounded to whole pixels and added up, so 32px Arial is 29 + 7 + 1 = 37, 24px is
    # 22 + 5 + 1 = 28, 13.3333px is 12 + 3 + 0 = 15 and 36px is 33 + 8 + 1 = 42.
    assert answer["em"] == ["32px", 37], answer
    assert answer["pct"] == ["24px", 28], answer
    assert answer["smaller"] == ["13.3333px", 15], answer
    assert answer["inner"] == ["36px", 42], answer


def test_a_rem_size_reads_the_root_not_the_parent(page):
    page.set_content('<body style="margin:0"><div id="rem" style="font-size:1.5rem">x</div></body>',
                     url="http://font-size.test/")
    answer = page.evaluate("""(() => {
      document.documentElement.style.fontSize = '20px';
      const el = document.getElementById('rem');
      return [getComputedStyle(el).fontSize, el.offsetHeight];
    })()""")
    # 30px of the default serif face: 27 + 6 + 1, the same three numbers a real Chrome adds.
    assert answer == ["30px", 34], answer
