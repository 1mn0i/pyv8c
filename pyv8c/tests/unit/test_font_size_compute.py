"""A computed font-size is absolute, and relative lengths resolve where the parent is known."""

import pytest

from pyv8c.foundation.font_sizes import font_size_px, px_text


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
    assert font_size_px(declared, parent, root) == pytest.approx(expected)


def test_a_computed_size_is_written_the_way_css_writes_it():
    assert px_text(32) == "32px"
    assert px_text(16) == "16px"
    assert px_text(13.333333333333334) == "13.3333px"
    assert px_text(19.2) == "19.2px"
