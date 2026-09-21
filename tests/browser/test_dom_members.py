"""DOM members a real page asked for, plus the URL and stylesheet plumbing.

The member tests all came out of a live page that reported them as
unimplemented.  The URL attributes share one resolution helper, and the
stylesheet tests guard the cascade entry points that keep ``layout`` from
rescanning the document on every read.
"""
import pytest

from pyv8c import Browser

CONTENT = """
<html><head><title>  Live   Page </title>
<style>
  .item { color: red; }
  div { font-size: 20px; }
  #sized { width: 40px; height: 10px; }
</style></head>
<body>
  <form name="login"><input name="user"><input name="user"></form>
  <p name="user"></p>
  <div class="item"><span id="sized">x</span></div>
  <div class="item" style="color: blue">inline</div>
  <link rel="stylesheet" media="">
  <textarea></textarea>
  <select><option value="a">A</option><option>B</option></select>
  <a id="link" href="/dir/target?q=1#frag">link</a>
  <script>window.__current = document.currentScript && document.currentScript.tagName;</script>
</body></html>
"""


@pytest.fixture
def page():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content(CONTENT, url="https://dom.test/dir/page")
    page.run_until_idle(timeout=1)
    try:
        yield page
    finally:
        browser.close()


def test_document_title_reads_collapsed_text_and_writes_it(page):
    assert page.evaluate("document.title") == "Live Page"
    assert page.evaluate("(document.title = ' New  Title ', document.title)") == "New Title"
    # The element keeps what was written; only the IDL attribute collapses.
    assert page.evaluate(
        "(function(){ const t = document.querySelector('title'); return t.textContent; })()"
    ) == " New  Title "


def test_get_elements_by_name_is_live(page):
    assert page.evaluate("document.getElementsByName('user').length") == 3
    assert page.evaluate("document.getElementsByName('user')[0].tagName") == "INPUT"
    assert page.evaluate("document.getElementsByName('absent').length") == 0
    # A NodeList that follows the tree, like the baseline declares it.
    assert page.evaluate("""
        (function () {
            const list = document.getElementsByName('user');
            const before = list.length;
            document.body.appendChild(document.createElement('i')).setAttribute('name', 'user');
            return [before, list.length];
        })()
    """) == [3, 4]


def test_document_implementation_factories(page):
    assert page.evaluate("document.implementation === document.implementation") is True
    assert page.evaluate("document.implementation.hasFeature()") is True
    assert page.evaluate("""
        (function () {
            const created = document.implementation.createHTMLDocument('T');
            return [created.title, created.documentElement.tagName, created.head.tagName,
                    created.body.tagName, created.URL];
        })()
    """) == ["T", "HTML", "HEAD", "BODY", "about:blank"]
    assert page.evaluate("""
        (function () {
            const doctype = document.implementation.createDocumentType('html', 'p', 's');
            const doc = document.implementation.createDocument(null, 'root');
            return [doctype.name, doctype.publicId, doctype.systemId,
                    doc.documentElement.tagName, doc.childNodes.length];
        })()
    """) == ["html", "p", "s", "ROOT", 1]


def test_current_script_is_set_only_while_a_script_runs(page):
    assert page.evaluate("window.__current") == "SCRIPT"
    assert page.evaluate("""
        (function () {
            let seen = 'unset';
            document.body.addEventListener('probe', () => { seen = String(document.currentScript); });
            document.body.dispatchEvent(new Event('probe'));
            return seen;
        })()
    """) == "null"
    assert page.evaluate("String(document.currentScript)") == "null"


def test_document_fragment_queries_its_own_subtree(page):
    assert page.evaluate("""
        (function () {
            const fragment = document.createDocumentFragment();
            const item = document.createElement('div');
            item.className = 'item';
            fragment.appendChild(item);
            fragment.appendChild(document.createElement('span'));
            return [fragment.querySelectorAll('.item').length,
                    fragment.querySelector('span').tagName,
                    fragment.querySelectorAll('div').length];
        })()
    """) == [1, "SPAN", 1]


def test_link_media_and_anchor_url_members(page):
    assert page.evaluate("""
        (function () {
            const link = document.querySelector('link');
            link.media = 'print';
            return [link.media, link.getAttribute('media')];
        })()
    """) == ["print", "print"]
    assert page.evaluate("""
        (function () {
            const anchor = document.querySelector('#link');
            return [anchor.href, anchor.pathname, anchor.hostname, anchor.origin,
                    anchor.search, anchor.hash, anchor.protocol, String(anchor)];
        })()
    """) == ["https://dom.test/dir/target?q=1#frag", "/dir/target", "dom.test",
             "https://dom.test", "?q=1", "#frag", "https:", "https://dom.test/dir/target?q=1#frag"]
    # Assigning a component rewrites the href attribute, as URLUtils requires.
    assert page.evaluate("""
        (function () {
            const anchor = document.createElement('a');
            anchor.setAttribute('href', 'https://u:p@h.test:81/a?x=1#h');
            const credentials = [anchor.username, anchor.password, anchor.host, anchor.port];
            anchor.pathname = '/b';
            anchor.hash = '#z';
            const rewritten = anchor.href;
            anchor.hostname = 'other.test';
            anchor.port = '';
            return [credentials, rewritten, anchor.href];
        })()
    """) == [["u", "p", "h.test:81", "81"], "https://u:p@h.test:81/b?x=1#z", "https://u:p@other.test/b?x=1#z"]
    assert page.evaluate("String(document.createElement('a').protocol)") == ":"
    assert page.evaluate("document.createElement('a').origin") == ""


def test_textarea_option_and_select_values(page):
    assert page.evaluate("""
        (function () {
            const area = document.querySelector('textarea');
            const empty = area.defaultValue;
            area.defaultValue = 'raw text';
            return [empty, area.defaultValue, area.textContent];
        })()
    """) == ["", "raw text", "raw text"]
    assert page.evaluate("""
        (function () {
            const select = document.querySelector('select');
            const [first, second] = select.querySelectorAll('option');
            const initial = [select.value, first.selected, second.selected, first.value, second.value];
            select.value = 'B';
            const afterB = [select.value, first.selected, second.selected];
            select.value = 'missing';
            return [initial, afterB, [select.value, first.selected, second.selected]];
        })()
    """) == [["a", True, False, "a", "B"], ["B", False, True], ["", False, False]]


def test_select_options_collection_is_live_and_indexable(page):
    assert page.evaluate("""
        (function () {
            const select = document.querySelector('select');
            const options = select.options;
            const before = options.length;
            const added = document.createElement('option');
            added.value = 'c';
            select.appendChild(added);
            return [before, options.length, options === select.options, select.length,
                    options[2] === added, options.item(0).value, options.item(9) === null];
        })()
    """) == [2, 3, True, 3, True, "a", True]
    assert page.evaluate("""
        (function () {
            const select = document.querySelector('select');
            const second = select.querySelectorAll('option')[1];
            second.setAttribute('id', 'second');
            return [select.options.namedItem('second') === second, select.options.item(-1) === null];
        })()
    """) == [True, True]


def test_select_selected_index_reads_and_moves_the_selection(page):
    assert page.evaluate("""
        (function () {
            const select = document.querySelector('select');
            const initial = [select.selectedIndex, select.options.selectedIndex,
                             select.selectedOptions.length];
            select.selectedIndex = 1;
            const moved = [select.selectedIndex, select.value, select.selectedOptions.length,
                           select.options.selectedIndex];
            select.selectedIndex = -1;
            return [initial, moved,
                    [select.selectedIndex, select.value, select.selectedOptions.length]];
        })()
    """) == [[0, 0, 1], [1, "B", 1, 1], [-1, "", 0]]


def test_select_options_collection_moves_the_selection_too(page):
    assert page.evaluate("""
        (function () {
            const select = document.querySelector('select');
            select.options.selectedIndex = 1;
            return [select.value, select.selectedIndex];
        })()
    """) == ["B", 1]


def test_form_elements_collects_its_listed_elements(page):
    assert page.evaluate("""
        (() => {
            const form = document.createElement('form');
            form.id = 'created';
            document.body.appendChild(form);
            for (const tag of ['input', 'fieldset', 'select', 'textarea']) {
                const element = document.createElement(tag);
                element.id = tag;
                form.appendChild(element);
            }
            const image = document.createElement('input');
            image.id = 'image';
            image.type = 'image';
            form.appendChild(image);
            const external = document.createElement('input');
            external.id = 'external';
            external.setAttribute('form', 'created');
            document.body.appendChild(external);
            return [Array.from(form.elements, element => element.id), form.length,
                    form.elements === form.elements,
                    form.elements.namedItem('external') === external];
        })()
    """) == [["input", "fieldset", "select", "textarea", "external"], 5, True, True]


def test_a_control_belongs_to_the_form_its_attribute_names(page):
    assert page.evaluate("""
        (() => {
            const first = document.createElement('form');
            first.id = 'first';
            const second = document.createElement('form');
            second.id = 'second';
            document.body.appendChild(first);
            document.body.appendChild(second);
            const plain = document.createElement('input');
            plain.id = 'plain';
            const missing = document.createElement('input');
            missing.id = 'missing';
            missing.setAttribute('form', 'nosuch');
            const moved = document.createElement('input');
            moved.id = 'moved';
            moved.setAttribute('form', 'second');
            for (const element of [plain, missing, moved]) first.appendChild(element);
            return [Array.from(first.elements, element => element.id),
                    Array.from(second.elements, element => element.id)];
        })()
    """) == [["plain"], ["moved"]]


def test_a_form_control_reports_its_name(page):
    assert page.evaluate("""
        (() => {
            const select = document.createElement('select');
            select.name = 's';
            const area = document.createElement('textarea');
            area.name = 't';
            const button = document.createElement('button');
            button.name = 'b';
            return [select.name, area.name, button.name,
                    select.getAttribute('name'), area.getAttribute('name')];
        })()
    """) == ["s", "t", "b", "s", "t"]


def test_html_button_reflects_name_value_and_type(page):
    assert page.evaluate("""
        (() => {
            const button = document.createElement('button');
            const empty = [button.name, button.value, button.type,
                           button instanceof HTMLButtonElement];
            button.name = 'n';
            button.value = 'v';
            button.setAttribute('type', 'RESET');
            const reflected = [button.name, button.value, button.type,
                               button.getAttribute('name'), button.getAttribute('value')];
            button.type = 'bogus';
            return [empty, reflected, [button.getAttribute('type'), button.type]];
        })()
    """) == [["", "", "submit", True], ["n", "v", "reset", "n", "v"],
             ["bogus", "submit"]]
    assert page.evaluate("document.createElement('form') instanceof HTMLFormElement") is True


def test_canvas_text_baseline_round_trips(page):
    assert page.evaluate("""
        (function () {
            const context = document.createElement('canvas').getContext('2d');
            const initial = context.textBaseline;
            context.textBaseline = 'middle';
            const set = context.textBaseline;
            context.textBaseline = 'not-a-baseline';
            const ignored = context.textBaseline;
            context.save();
            context.textBaseline = 'top';
            context.restore();
            return [initial, set, ignored, context.textBaseline];
        })()
    """) == ["alphabetic", "middle", "middle", "middle"]


def test_stylesheet_cascade_and_layout_still_apply(page):
    """The cascade reads the sheet once per entry point, not once per element."""
    assert page.evaluate("getComputedStyle(document.querySelector('.item')).color") == "red"
    # Inherited through the parent chain.
    assert page.evaluate("getComputedStyle(document.getElementById('sized')).fontSize") == "20px"
    # An inline declaration outranks the sheet.
    assert page.evaluate("getComputedStyle(document.querySelector('.item[style]')).color") == "blue"
    assert page.evaluate("""
        (function () {
            const sized = document.getElementById('sized');
            return [sized.offsetWidth, sized.offsetHeight, document.body.offsetWidth];
        })()
    """) == [40, 10, 1280]


def test_url_attributes_resolve_and_write_the_attribute(page):
    """The URL attributes read back absolute and keep the attribute written."""
    assert page.evaluate("""
        (function () {
            const build = (tag, name, value) => {
                const node = document.createElement(tag);
                node.setAttribute(name, value);
                return node;
            };
            return [
                build('img', 'src', 'pics/a.png').src,
                build('script', 'src', '/js/app.js').src,
                build('link', 'href', '../style.css').href,
                build('input', 'src', 'icon.png').src,
                build('iframe', 'src', 'frame.html').src,
                build('img', 'src', 'https://cdn.test/x.png').src,
                build('img', 'src', '').src,
                build('img', 'src', 'pics/a.png').getAttribute('src'),
                build('link', 'href', '../style.css').getAttribute('href'),
            ];
        })()
    """) == [
        "https://dom.test/dir/pics/a.png",
        "https://dom.test/js/app.js",
        "https://dom.test/style.css",
        "https://dom.test/dir/icon.png",
        "https://dom.test/dir/frame.html",
        "https://cdn.test/x.png",
        "",
        "pics/a.png",
        "../style.css",
    ]
    # Assigning through the IDL member writes the attribute, not the resolved URL.
    assert page.evaluate("""
        (function () {
            const link = document.createElement('link');
            link.href = 'now.css';
            const input = document.createElement('input');
            input.src = 'pic.png';
            const anchor = document.createElement('a');
            anchor.href = '/deep/page';
            return [link.getAttribute('href'), link.href,
                    input.getAttribute('src'), input.src,
                    anchor.getAttribute('href'), anchor.href];
        })()
    """) == ["now.css", "https://dom.test/dir/now.css",
             "pic.png", "https://dom.test/dir/pic.png",
             "/deep/page", "https://dom.test/deep/page"]


STYLE_CONTENT = """
<html><head><style>#target { color: red }</style></head>
<body><div id="target">x</div></body></html>
"""


@pytest.fixture
def style_page():
    browser = Browser(stub_logging="off")
    page = browser.new_context().new_page()
    page.set_content(STYLE_CONTENT, url="https://dom.test/dir/page")
    page.run_until_idle(timeout=1)
    try:
        yield page
    finally:
        browser.close()


def test_stylesheet_cache_follows_runtime_changes(style_page):
    """The sheet is parsed once per change, not once per measurement."""
    from pyv8c.web.dom.layout import document_rules

    document = style_page.document

    def color():
        return style_page.evaluate("getComputedStyle(document.getElementById('target')).color")

    assert color() == "red"
    cached = document_rules(document)
    assert document_rules(document) is cached, "an unchanged document reuses its sheet"

    # A style element added at runtime.
    style_page.evaluate("""
        (function () {
            const style = document.createElement('style');
            style.textContent = '#target { color: green }';
            document.head.appendChild(style);
            window.__style = style;
        })()
    """)
    assert document_rules(document) is not cached
    assert color() == "green"

    # Editing the rules the element holds, through the tree and through a text node.
    style_page.evaluate("window.__style.textContent = '#target { color: purple }'")
    assert color() == "purple"
    style_page.evaluate("window.__style.firstChild.nodeValue = '#target { color: teal }'")
    assert color() == "teal"

    # Removing it brings the original rule back.
    style_page.evaluate("window.__style.parentNode.removeChild(window.__style)")
    assert color() == "red"

    # A subtree carrying a style element that is only inserted afterwards.
    style_page.evaluate("""
        (function () {
            const holder = document.createElement('div');
            holder.innerHTML = '<style>#target { color: blue }</style>';
            document.head.appendChild(holder);
        })()
    """)
    assert color() == "blue"
