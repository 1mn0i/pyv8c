"""The order a real Window realm installs its own properties in.

The offline cases pin the document and the plan; these pin what a realm built
from them actually answers ``Object.getOwnPropertyNames(window)`` with, including
the two constructor blocks, the four member groups and the legacy aliases.
"""
import pytest

from pyv8c.engine.window_order import load

pytestmark = pytest.mark.native


def _names(page):
    return page.evaluate("Object.getOwnPropertyNames(window)")


def test_the_legacy_factories_lead_the_interface_constructors(page):
    order = load()
    names = _names(page)
    start = names.index(order.legacy_factories[0])
    assert names[start:start + len(order.legacy_factories)] == list(order.legacy_factories)
    assert names[start + len(order.legacy_factories)] == order.constructors_before[0]


def test_the_window_members_follow_the_constructor_block(page):
    order = load()
    names = _names(page)
    window = names.index("window")
    assert names[window - 1] == order.constructors_before[-1]
    assert names[window] == order.members["early"][0]


def test_the_context_dependent_members_follow_the_operations(page):
    names = _names(page)
    assert names.index("onclick") < names.index("alert")
    assert names.index("caches") > names.index("fetch")
    assert names.index("cookieStore") > names.index("alert")
    assert names.index("documentPictureInPicture") > names.index("structuredClone")


def test_the_host_globals_precede_the_context_dependent_members(page):
    names = _names(page)
    assert names.index("chrome") > names.index("WebAssembly")
    assert names.index("chrome") < names.index("crashReport")
    assert names.index("crashReport") < names.index("caches")
    assert names.index("caches") < names.index("AbsoluteOrientationSensor")


def test_the_legacy_aliases_share_the_target_constructor(page):
    assert page.evaluate("window.webkitURL === window.URL")
    assert page.evaluate("window.FeaturePolicy === window.PermissionsPolicy")
    assert page.evaluate("window.WebKitCSSMatrix === window.DOMMatrix")
    assert page.evaluate("window.WebKitMutationObserver === window.MutationObserver")


def test_the_realm_answers_the_document_order(page, browser):
    order = load()
    expected = (list(order.legacy_factories)
                + list(order.constructors_before)
                + list(order.members["early"])
                + list(order.members["operations"])
                + list(order.members["late_attributes"])
                + list(order.constructors_after)
                + list(order.members["late_operations"])
                + list(order.members["late_template"])
                + list(order.constructors_after_late)
                + list(order.members["tail"]))
    names = _names(page)
    start = names.index(order.legacy_factories[0])
    actual = names[start:]
    known = set(expected)
    actual = [name for name in actual if name in known]
    profile = browser.bindings.idl.profile
    exposed = profile.exposed if profile is not None else None
    expected = [name for name in expected if exposed is None or name in exposed]
    assert actual == expected


def test_without_order_the_legacy_factories_are_still_installed(monkeypatch):
    from pyv8c import Browser
    from pyv8c.engine.idl import Idl
    idl = Idl.load()
    monkeypatch.setattr(idl, "window_order", None)
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate("['Option', 'Image', 'Audio'].map(n => typeof window[n])") == ["function"] * 3
    finally:
        browser.close()


def test_unrecorded_exposed_interface_is_still_installed(monkeypatch):
    from dataclasses import replace
    from pyv8c import Browser
    from pyv8c.engine.idl import Idl
    idl = Idl.load()
    order = idl.window_order
    name = order.constructors_after[0]
    monkeypatch.setattr(idl, "window_order", replace(order, constructors_after=order.constructors_after[1:]))
    browser = Browser(real_network=False)
    try:
        page = browser.new_profile().new_page()
        assert page.evaluate(f"typeof window.{name}") == "function"
    finally:
        browser.close()


def test_iframe_window_order_and_descriptor_match_parent(page):
    result = page.evaluate("""(() => {
        const frame = document.createElement('iframe');
        document.body.appendChild(frame);
        const own = w => Object.getOwnPropertyNames(w).filter(n => ['Option','Image','window','fetch','caches'].includes(n));
        const desc = w => { const d = Object.getOwnPropertyDescriptor(w, 'caches');
            return d && [d.enumerable, d.configurable, typeof d.get, typeof d.set]; };
        return [own(window), own(frame.contentWindow), desc(window), desc(frame.contentWindow),
                frame.contentWindow.Option !== window.Option];
    })()""")
    assert result[0] == result[1]
    assert result[2] == result[3]
    assert result[4] is True


def test_navigation_preserves_window_install_order(browser):
    page = browser.new_profile().new_page()
    names = ["Option", "Image", "window", "fetch", "caches"]
    before = [name for name in _names(page) if name in names]
    page.goto("data:text/html,<body>next</body>")
    after = [name for name in _names(page) if name in names]
    assert after == before
