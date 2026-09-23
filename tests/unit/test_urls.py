"""Origin primitives, which need no JavaScript context."""
from pyv8c.foundation.urls import origin_tuple


def test_origin_normalization_and_unique_opaque_origins():
    assert origin_tuple('HTTP://EXAMPLE.TEST:80/a?b#c') == ('http', 'example.test', 80)
    assert origin_tuple('https://example.test') == ('https', 'example.test', 443)
    assert origin_tuple('data:text/html,x') != origin_tuple('data:text/html,x')
