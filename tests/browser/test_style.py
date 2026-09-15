"""CSSOM inline style behavior exposed by HTMLElement.style."""

import pytest

from pyv8c import Browser


@pytest.fixture
def page():
    browser = Browser(cache=False, stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content(
        '<div id="target" style="color: red; margin-top: 2px; '
        'color: blue !important; broken"></div>',
        url="http://style.test/",
    )
    try:
        yield page
    finally:
        browser.close()


def test_style_identity_cssom_and_initial_parse(page):
    assert page.evaluate("""(() => {
        const e = target;
        return [e.style === e.style, Object.prototype.toString.call(e.style),
            e.style.cssText, e.style.length, e.style.color,
            e.style.getPropertyPriority('color'), e.style.item(0),
            e.style.item(99), e.style.parentRule === null];
    })()""") == [
        True, "[object CSSStyleDeclaration]",
        "color: blue !important; margin-top: 2px;", 2, "blue", "important",
        "color", "", True,
    ]


def test_style_named_properties_and_custom_properties(page):
    assert page.evaluate("""(() => {
        const s = target.style;
        s.backgroundColor = 'white';
        s['margin-left'] = '3px';
        s['--theme-color'] = '  red   blue ';
        return [s.backgroundColor, s.getPropertyValue('margin-left'),
            s['--theme-color'], s.cssText, target.getAttribute('style')];
    })()""") == [
        "white", "3px", "red blue",
        "color: blue !important; margin-top: 2px; background-color: white; "
        "margin-left: 3px; --theme-color: red blue;",
        "color: blue !important; margin-top: 2px; background-color: white; "
        "margin-left: 3px; --theme-color: red blue;",
    ]


def test_style_methods_priority_removal_and_css_text(page):
    assert page.evaluate("""(() => {
        const s = target.style;
        s.setProperty('padding-top', '4px', 'IMPORTANT');
        const old = s.removeProperty('margin-top');
        const missing = s.removeProperty('does-not-exist');
        s.cssText = 'border: 0; invalid; opacity: .5 ! important; border: 1px';
        return [old, missing, s.cssText, s.getPropertyPriority('opacity'),
            s.getPropertyValue('border'), s.length];
    })()""") == [
        "2px", "", "border: 1px; opacity: .5 !important;", "important",
        "1px", 2,
    ]
    assert page.evaluate("target.style.cssText = ''; [target.style.length, target.hasAttribute('style')]" ) == [0, False]


def test_style_named_properties_only_exist_when_supported(page):
    """``in`` and reads follow the properties Chrome supports."""
    assert page.evaluate("""(() => {
        const s = target.style;
        return [
            'MozAppearance' in s, '-moz-appearance' in s, 'msTransform' in s, 'OTransition' in s,
            'notAProperty' in s, 'zzz' in s,
            'color' in s, 'backgroundColor' in s, 'background-color' in s,
            'webkitTransform' in s, 'WebkitAppearance' in s,
            typeof s.MozAppearance, typeof s.display, s.display, s.color,
            Object.getOwnPropertyDescriptor(s, 'MozAppearance') === undefined,
        ];
    })()""") == [
        False, False, False, False,
        False, False,
        True, True, True,
        True, True,
        "undefined", "string", "", "blue",
        True,
    ]


def test_style_declared_unsupported_name_becomes_visible(page):
    """Assigning a name no property provides still makes it exist."""
    assert page.evaluate("""(() => {
        const s = target.style;
        const before = ['notAProperty' in s, s.notAProperty === undefined, '--theme-color' in s];
        s.notAProperty = '1';
        s['--theme-color'] = 'red';
        return [before, 'notAProperty' in s, s.notAProperty, s['--theme-color']];
    })()""") == [[False, True, True], True, "1", "red"]


def test_style_attribute_changes_refresh_existing_object(page):
    assert page.evaluate("""(() => {
        const s = target.style;
        target.setAttribute('style', 'padding: 1px; color: green');
        const afterSet = [s.cssText, s.padding, s.color];
        target.removeAttribute('style');
        return [afterSet, s.cssText, s.length, s.color];
    })()""") == [["padding: 1px; color: green;", "1px", "green"], "", 0, ""]
