"""Lazy interface materialisation on the global object.

Interfaces beyond the eager set are declared as native accessors at Page
creation. They stay unbuilt until JavaScript first touches them, so a cold
Browser materialises only the eager core while still exposing every name.
"""
import pytest

pytest.importorskip("pyv8")
from pyv8c import Browser


def test_cold_start_materialises_no_lazy_interface():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        session = page.binding_session
        materialized = set(session.materialized_interfaces)
        eager = set(browser.interface_universe.eager_names("Window"))
        lazy = set(session.lazy_names("Window"))

        assert eager
        # The eager core is ready, but nothing declared lazily has been built.
        # Ancestors of eager interfaces (WindowProperties) are expected too.
        assert eager <= materialized
        assert not (materialized & lazy)
        assert browser.startup_stats["lazy_registered"] == len(lazy)
    finally:
        browser.close()


def test_window_exposes_every_declared_interface_name():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        expected = set(page.binding_session.interface_names("Window"))
        exposed = set(page.evaluate("Object.getOwnPropertyNames(window)"))
        assert expected <= exposed
    finally:
        browser.close()


def test_first_access_materialises_a_constructor():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        # XPathEvaluator has no hand-written implementation, so it can only
        # come from the baseline via lazy materialisation.
        assert page.evaluate("typeof XPathEvaluator") == "function"
        assert page.evaluate("XPathEvaluator.name") == "XPathEvaluator"
        assert page.evaluate(
            "Object.prototype.toString.call(XPathEvaluator.prototype)"
        ) == "[object XPathEvaluator]"
        # Ancestors are materialised alongside the requested interface.
        assert "EventTarget" in page.binding_session.materialized_interfaces
        assert "XPathEvaluator" in page.binding_session.materialized_interfaces
    finally:
        browser.close()


def test_undetectable_callable_interface_materialises_safely():
    browser = Browser(real_network=False, stub_logging="off")
    page = browser.new_context().new_page()
    try:
        assert page.evaluate("typeof HTMLAllCollection") == "function"
        assert page.evaluate("HTMLAllCollection.prototype[Symbol.toStringTag]") == "HTMLAllCollection"
    finally:
        browser.close()


def test_repeated_access_does_not_rematerialise():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        page.evaluate("typeof XPathEvaluator")
        after_first = page.binding_session.materialized_interfaces

        page.evaluate("typeof XPathEvaluator")
        assert page.binding_session.materialized_interfaces == after_first
        # The cached template keeps returning the same constructor.
        assert page.evaluate("XPathEvaluator === XPathEvaluator")
    finally:
        browser.close()


def test_lazy_materialisation_resolves_native_constants():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        # Values come from the generated Chromium constants in the baseline.
        assert page.evaluate("GPUBufferUsage.COPY_SRC") == 4
        assert page.evaluate("GPUBufferUsage.COPY_DST") == 8
    finally:
        browser.close()


def test_lazy_names_not_in_the_realm_resolve_to_undefined():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        names = set(page.binding_session.interface_names("Window"))
        assert names
        # A Worker-only interface must not appear as a Window constructor.
        worker_only = next(
            (name for name in page.binding_session.interface_names("Worker")
             if name not in names),
            None,
        )
        if worker_only is not None:
            assert page.evaluate("typeof " + worker_only) == "undefined"
    finally:
        browser.close()


def test_materialisation_is_isolated_between_pages():
    browser = Browser(stub_logging="off")
    first = browser.new_context().new_page()
    second = browser.new_context().new_page()
    try:
        assert first.evaluate("typeof XPathEvaluator") == "function"
        assert "XPathEvaluator" in first.binding_session.materialized_interfaces
        assert "XPathEvaluator" not in second.binding_session.materialized_interfaces

        # The second page builds its own constructor for the same interface.
        assert second.evaluate("typeof XPathEvaluator") == "function"
        assert second.evaluate("XPathEvaluator") is not first.evaluate("XPathEvaluator")
    finally:
        browser.close()


def test_eager_interfaces_still_work_after_lazy_declaration():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        page.set_content("<html><body><div id='app'>ready</div></body></html>")
        assert page.evaluate("document.querySelector('#app').textContent") == "ready"
        page.run_until_idle()
        assert page.evaluate("document.readyState") == "complete"
        assert page.evaluate("new URL('https://example.test/a').pathname") == "/a"
        assert page.evaluate("new URLSearchParams('a=1').get('a')") == "1"
    finally:
        browser.close()


def test_unknown_global_name_is_not_materialised():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    try:
        assert page.evaluate("typeof DefinitelyNotAnInterface") == "undefined"
    finally:
        browser.close()
