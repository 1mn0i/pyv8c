"""``document.all`` browser object checks.

``tests/browser`` collects checks that a JavaScript-visible object matches the
object a real browser exposes. Each module covers three questions about one
object:

* **类型** - ``typeof``, prototype, ``Symbol.toStringTag``, constructibility and
  the matching Python type behind the V8 wrapper.
* **使用** - how scripts reach into the object: indexed access, named access,
  methods, the legacy call form and enumeration.
* **等待** - whether the object stays live while the page is still settling:
  after a queued task runs, after ``run_until_idle()`` and after a later node
  is inserted.
"""

import pytest

pytest.importorskip("pyv8")
import pyv8
from pyv8c import Browser
from pyv8c.dom.model import HTML_CLASSES, Element, HTMLAllCollection, HTMLCollection

DOCUMENT = (
    "<html><head><title>t</title></head><body>"
    "<div id='app'>1</div>"
    "<p name='note'>2</p>"
    "<span id='dup'>3</span><span id='dup'>4</span>"
    "</body></html>"
)

# Tree order of DOCUMENT: html, head, title, body, div, p, span, span.
TAGS = ["HTML", "HEAD", "TITLE", "BODY", "DIV", "P", "SPAN", "SPAN"]


@pytest.fixture
def browser():
    instance = Browser(stub_logging="off")
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def page(browser):
    """A page that has finished parsing DOCUMENT."""
    instance = browser.new_context().new_page()
    instance.set_content(DOCUMENT)
    instance.run_until_idle()
    return instance


class TestDocumentAllType:
    """The interface shape Chromium gives ``document.all``."""

    def test_typeof_is_undefined(self, page):
        # The interface is marked undetectable, the historical document.all quirk.
        assert page.evaluate("typeof document.all") == "undefined"

    def test_object_tag_is_html_all_collection(self, page):
        assert page.evaluate("Object.prototype.toString.call(document.all)") == "[object HTMLAllCollection]"

    def test_instance_of_html_all_collection(self, page):
        assert page.evaluate("document.all instanceof HTMLAllCollection") is True
        assert page.evaluate("Object.getPrototypeOf(document.all) === HTMLAllCollection.prototype") is True
        assert page.evaluate("HTMLAllCollection.prototype[Symbol.toStringTag]") == "HTMLAllCollection"

    def test_undetectable_object_is_falsy_and_loosely_null(self, page):
        assert page.evaluate("Boolean(document.all)") is False
        assert page.evaluate("!document.all") is True
        assert page.evaluate("document.all == null") is True
        assert page.evaluate("document.all == undefined") is True
        # ... but it is still a real object, not null or undefined.
        assert page.evaluate("document.all === null") is False
        assert page.evaluate("document.all === undefined") is False
        assert page.evaluate("document.all ? 'truthy' : 'falsy'") == "falsy"

    def test_python_type_is_the_implementation_class(self, page):
        assert isinstance(page.evaluate("document.all"), HTMLAllCollection)
        # Elements behind the collection keep their concrete Python class.
        assert isinstance(page.evaluate("document.all[0]"), Element)
        assert isinstance(page.evaluate("document.all.app"), HTML_CLASSES["HTMLDivElement"])
        assert isinstance(page.evaluate("document.all.dup"), HTMLCollection)

    def test_interface_constructor_is_exposed_but_not_constructible(self, page):
        assert page.evaluate("typeof HTMLAllCollection") == "function"
        assert page.evaluate("HTMLAllCollection.name") == "HTMLAllCollection"
        assert page.evaluate("document.all.constructor === HTMLAllCollection") is True
        for script in ("new HTMLAllCollection()", "HTMLAllCollection()"):
            with pytest.raises(pyv8.JSException) as caught:
                page.evaluate(script)
            assert "Illegal constructor" in str(caught.value)

    def test_document_owns_all_as_a_prototype_accessor(self, page):
        descriptor = "Object.getOwnPropertyDescriptor(Document.prototype, 'all')"
        assert page.evaluate(f"typeof {descriptor}.get") == "function"
        assert page.evaluate(f"{descriptor}.set") is pyv8.Undefined
        assert page.evaluate(f"{descriptor}.enumerable") is True
        assert page.evaluate(f"{descriptor}.configurable") is True


class TestDocumentAllUsage:
    """How scripts read a document through ``document.all``."""

    def test_length_counts_every_element(self, page):
        assert page.evaluate("document.all.length") == len(TAGS)
        assert page.evaluate("document.all.length") == page.evaluate("document.getElementsByTagName('*').length")

    def test_indexed_access_follows_tree_order(self, page):
        assert page.evaluate("[].slice.call(document.all).map(function(e){return e.tagName})") == TAGS
        assert page.evaluate("document.all[0].tagName") == "HTML"
        assert page.evaluate("document.all[4].id") == "app"

    def test_index_out_of_range_is_undefined(self, page):
        assert page.evaluate("document.all[document.all.length]") is pyv8.Undefined
        assert page.evaluate("document.all[99]") is pyv8.Undefined
        assert page.evaluate("document.all.item(99)") is pyv8.Null
        assert page.evaluate("99 in document.all") is False

    def test_item_matches_indexed_access(self, page):
        assert page.evaluate("document.all.item(0) === document.all[0]") is True
        assert page.evaluate("document.all.item(4).id") == "app"
        assert page.evaluate("document.all.item()") is pyv8.Null
        assert page.evaluate("document.all.item(-1)") is pyv8.Null
        assert page.evaluate("document.all.item('0') === document.all[0]") is True
        assert page.evaluate("document.all.item('04')") is pyv8.Null
        assert page.evaluate("document.all.item(true)") is pyv8.Null
        assert page.evaluate("document.all.item(null)") is pyv8.Null
        assert page.evaluate("document.all.item('app') === document.all.namedItem('app')") is True
        assert page.evaluate("document.all.item('missing')") is pyv8.Null

    def test_named_item_by_id_and_by_name(self, page):
        assert page.evaluate("document.all.namedItem('app').tagName") == "DIV"
        # ``name`` only participates for the HTML elements listed by the spec;
        # an arbitrary ``<p name=...>`` must not become a named property.
        assert page.evaluate("document.all.namedItem('note')") is pyv8.Null
        assert page.evaluate("document.all.namedItem('missing')") is pyv8.Null

    def test_named_access_by_id_and_by_name(self, page):
        assert page.evaluate("document.all.app.tagName") == "DIV"
        assert page.evaluate("document.all.note") is pyv8.Undefined
        assert page.evaluate("document.all['app'] === document.all.app") is True
        assert page.evaluate("'app' in document.all") is True

    def test_unknown_name_is_undefined_but_lookup_is_null(self, page):
        assert page.evaluate("document.all.missing") is pyv8.Undefined
        assert page.evaluate("document.all.namedItem('missing')") is pyv8.Null
        assert page.evaluate("'missing' in document.all") is False

    def test_duplicate_names_return_a_live_html_collection(self, page):
        assert page.evaluate("typeof document.all.dup") == "object"
        assert page.evaluate("document.all.dup instanceof HTMLCollection") is True
        assert page.evaluate("document.all.dup.length") == 2
        assert page.evaluate("document.all.namedItem('dup').length") == 2
        assert page.evaluate("document.all.dup[0] === document.all[6]") is True
        assert page.evaluate("document.all.dup[1] === document.all[7]") is True

    def test_legacy_call_resolves_names_and_indices(self, page):
        assert page.evaluate("document.all('app').tagName") == "DIV"
        assert page.evaluate("document.all('note')") is pyv8.Null
        assert page.evaluate("document.all(0) === document.all[0]") is True
        assert page.evaluate("document.all('0') === document.all[0]") is True
        assert page.evaluate("document.all(4).id") == "app"
        assert page.evaluate("document.all('missing')") is pyv8.Null
        assert page.evaluate("document.all()") is pyv8.Null

    def test_named_item_requires_an_argument(self, page):
        with pytest.raises(pyv8.JSException) as caught:
            page.evaluate("document.all.namedItem()")
        assert "namedItem" in str(caught.value)
        assert "argument" in str(caught.value)

    def test_identity_is_stable_across_reads(self, page):
        assert page.evaluate("document.all === document.all") is True
        page.evaluate("window.__cached = document.all")
        assert page.evaluate("window.__cached === document.all") is True

    def test_named_properties_are_not_enumerable(self, page):
        # [LegacyUnenumerableNamedProperties]: reachable, but never enumerated.
        keys = page.evaluate("Object.keys(document.all)")
        assert keys == [str(i) for i in range(len(TAGS))]
        assert page.evaluate("Object.getOwnPropertyNames(document.all)") == keys + ["app", "dup"]
        assert page.evaluate("Object.getOwnPropertyDescriptor(document.all, 'app').enumerable") is False
        assert page.evaluate("Object.getOwnPropertyDescriptor(document.all, 'app').writable") is False
        assert page.evaluate("Object.getOwnPropertyDescriptor(document.all, 'app').configurable") is True

    def test_legacy_named_properties_do_not_mask_the_prototype(self, page):
        page.evaluate(
            "document.body.innerHTML += '<div id=item></div><div id=length></div>"
            "<div id=constructor></div>'"
        )
        assert page.evaluate("typeof document.all.item") == "function"
        assert page.evaluate("document.all.length") == len(TAGS) + 3
        assert page.evaluate("document.all.constructor === HTMLAllCollection") is True

    def test_empty_names_and_forbidden_name_attributes_are_ignored(self, page):
        assert page.evaluate("document.all.namedItem('')") is pyv8.Null
        assert page.evaluate("document.all['']") is pyv8.Undefined
        assert page.evaluate("'' in document.all") is False
        assert page.evaluate("document.all.note") is pyv8.Undefined
        page.evaluate("document.body.appendChild(document.createElement('input')).setAttribute('name', 'field')")
        assert page.evaluate("document.all.field.tagName") == "INPUT"

    def test_collection_is_iterable(self, page):
        assert page.evaluate("typeof document.all[Symbol.iterator]") == "function"
        assert page.evaluate("Array.from(document.all).length") == len(TAGS)

    def test_legacy_mutations_follow_strictness_and_do_not_define_properties(self, page):
        assert page.evaluate("(document.all.app = 1, document.all.app.id)") == "app"
        assert page.evaluate("(delete document.all.app, document.all.app.id)") == "app"
        assert page.evaluate("(document.all.created = 1, document.all.created)") == 1
        with pytest.raises(pyv8.JSException):
            page.evaluate("'use strict'; document.all.app = 1")
        with pytest.raises(pyv8.JSException):
            page.evaluate("'use strict'; delete document.all.app")

    def test_for_in_only_yields_indices_and_prototype_members(self, page):
        seen = page.evaluate("(function(){var out=[];for(var k in document.all){out.push(k)}return out})()")
        assert [key for key in seen if key.isdigit()] == [str(i) for i in range(len(TAGS))]
        assert ["length", "item", "namedItem"] == [key for key in seen if not key.isdigit()]

    def test_empty_document_has_an_empty_collection(self, browser):
        empty = browser.new_context().new_page()
        assert empty.evaluate("document.all.length") == 0
        assert empty.evaluate("typeof document.all") == "undefined"
        assert empty.evaluate("document.all[0]") is pyv8.Undefined


class TestDocumentAllWaiting:
    """``document.all`` stays live while the page keeps changing."""

    def test_settling_the_page_keeps_length_consistent(self, browser):
        instance = browser.new_context().new_page()
        instance.set_content(DOCUMENT)
        instance.run_until_idle()
        assert instance.evaluate("document.readyState") == "complete"
        assert instance.evaluate("document.all.length") == len(TAGS)
        assert instance.evaluate("document.all.app.id") == "app"

    def test_length_updates_after_a_queued_task_appends(self, page):
        before = page.evaluate("document.all.length")
        page.evaluate(
            "setTimeout(function(){ var s = document.createElement('section');"
            " s.id = 'late'; document.body.appendChild(s); }, 0)"
        )
        page.run_until_idle()
        assert page.evaluate("document.all.length") == before + 1
        assert page.evaluate("document.all.late.tagName") == "SECTION"
        assert page.evaluate("document.all[document.all.length - 1].id") == "late"

    def test_cached_reference_tracks_later_insertions(self, page):
        page.evaluate("window.__cached = document.all")
        page.evaluate("document.body.appendChild(document.createElement('hr'))")
        assert page.evaluate("window.__cached.length") == len(TAGS) + 1
        assert page.evaluate("window.__cached.length === document.all.length") is True
        assert page.evaluate("window.__cached[window.__cached.length - 1].tagName") == "HR"

    def test_removal_shortens_the_collection(self, page):
        page.evaluate("document.body.removeChild(document.getElementById('app'))")
        assert page.evaluate("document.all.length") == len(TAGS) - 1
        assert page.evaluate("document.all.app") is pyv8.Undefined
        assert page.evaluate("document.all.namedItem('app')") is pyv8.Null

    def test_new_element_without_id_is_only_reachable_by_index(self, page):
        page.evaluate("document.body.appendChild(document.createElement('b'))")
        assert page.evaluate("document.all.length") == len(TAGS) + 1
        assert page.evaluate("document.all[document.all.length - 1].tagName") == "B"

    def test_renaming_an_id_changes_the_named_lookup(self, page):
        page.evaluate("document.getElementById('app').id = 'renamed'")
        assert page.evaluate("document.all.app") is pyv8.Undefined
        assert page.evaluate("document.all.renamed.tagName") == "DIV"
        assert page.evaluate("'app' in document.all") is False
        assert page.evaluate("'renamed' in document.all") is True


class TestDocumentAllMaterialisation:
    """``document.all`` works on every binding path, including cold starts."""

    def test_lazy_interface_is_materialised_on_first_use(self, browser):
        page = browser.new_context().new_page()
        session = page.binding_session
        assert "HTMLAllCollection" not in session.materialized_interfaces
        page.set_content(DOCUMENT)
        page.run_until_idle()
        assert page.evaluate("document.all.length") == len(TAGS)
        assert "HTMLAllCollection" in session.materialized_interfaces

    def test_full_materialisation_path(self):
        browser = Browser(stub_logging="off", lazy_interfaces=False)
        try:
            page = browser.new_context().new_page()
            page.set_content(DOCUMENT)
            page.run_until_idle()
            assert page.evaluate("typeof document.all") == "undefined"
            assert page.evaluate("document.all instanceof HTMLAllCollection") is True
            assert page.evaluate("document.all('app').id") == "app"
        finally:
            browser.close()

    def test_strict_validation_accepts_the_interface(self):
        browser = Browser(stub_logging="off", strict_validation=True)
        try:
            page = browser.new_context().new_page()
            page.set_content(DOCUMENT)
            page.run_until_idle()
            assert page.evaluate("document.all.length") == len(TAGS)
        finally:
            browser.close()
