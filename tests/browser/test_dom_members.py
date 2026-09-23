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
    # href/pathname/search/hash/protocol: corpus ``anchor_href_resolves_against_the_document``.
    assert page.evaluate("""
        (function () {
            const anchor = document.querySelector('#link');
            return [anchor.hostname, anchor.origin, String(anchor)];
        })()
    """) == ["dom.test", "https://dom.test", "https://dom.test/dir/target?q=1#frag"]
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


def test_a_mutation_observer_reports_child_and_attribute_changes(page):
    page.evaluate("""(() => {
        window.__records = [];
        const target = document.createElement('div');
        document.body.appendChild(target);
        new MutationObserver(records => {
          window.__records.push(...records.map(
              record => [record.type, record.target.tagName, record.addedNodes.length]));
        }).observe(target, {childList: true, attributes: true});
        target.appendChild(document.createElement('span'));
        target.setAttribute('data-x', '1');
        target.removeAttribute('data-x');
    })()""")
    assert page.evaluate("window.__records") == [
        ["childList", "DIV", 1], ["attributes", "DIV", 0], ["attributes", "DIV", 0],
    ]


def test_a_mutation_observer_hears_only_what_it_asked_for(page):
    """Records are queued, not delivered: ``takeRecords`` takes them first."""
    assert page.evaluate("""(() => {
        const heard = [];
        const target = document.createElement('div');
        document.body.appendChild(target);
        const observer = new MutationObserver(records => {
          heard.push(...records.map(record => record.type));
        });
        observer.observe(target, {attributes: true});
        target.appendChild(document.createElement('i'));
        target.setAttribute('data-y', '2');
        const taken = observer.takeRecords().map(record => record.type);
        observer.disconnect();
        target.setAttribute('data-z', '3');
        return [taken, observer.takeRecords().length, heard.length];
    })()""") == [["attributes"], 0, 0]


def test_xpath_walks_the_document(page):
    # Predicates and snapshots: corpus ``xpath_predicates_and_snapshots``.
    page.set_content('<p class="x">one</p><p class="x">two</p><p>three</p>',
                     url="https://secure.test/")
    assert page.evaluate("""(() => {
        const href = document.evaluate("//a/@href", document, null,
            XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
        const second = document.createExpression("//p[2]", null)
            .evaluate(document, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
        return [href.snapshotLength, second.singleNodeValue.textContent];
    })()""") == [0, "two"]


def test_a_frames_window_keeps_its_identity_across_a_navigation(page):
    page.set_content('<body><iframe id="f" src="about:blank"></iframe></body>',
                     url="https://frame.test/")
    assert page.evaluate("""(() => {
        window.__saved = document.getElementById('f').contentWindow;
        return window.__saved === document.getElementById('f').contentWindow;
    })()""") is True
    page.evaluate("document.getElementById('f').srcdoc = '<p>second</p>'")
    assert page.evaluate("""(() => {
        const frame = document.getElementById('f');
        return [window.__saved === frame.contentWindow,
                window.__saved.document === frame.contentDocument,
                frame.contentDocument.body.textContent];
    })()""") == [True, True, "second"]


def test_a_navigation_gives_the_page_a_new_window_and_keeps_the_context(page):
    page.set_content("<body>first</body>", url="https://nav.test/one")
    page.evaluate("""(() => {
        window.__mark = 'first';
        window.name = 'kept';
        sessionStorage.setItem('k', 'v');
    })()""")
    page.set_content("<body>second</body>", url="https://nav.test/two")
    assert page.evaluate("""(() => {
        return [window.__mark === undefined, window.name,
                sessionStorage.getItem('k'), location.href,
                window.top === window, window.parent === window,
                window.window === window, document.body.textContent];
    })()""") == [True, "kept", "v", "https://nav.test/two", True, True, True, "second"]


def test_a_frames_top_and_parent_follow_their_pages_navigations(page):
    page.set_content('<body><iframe src="about:blank"></iframe></body>',
                     url="https://top.test/first")
    child = next(iter(page._owned.frames))
    assert child.evaluate("[parent.location.href, top.location.href]") == \
        ["https://top.test/first", "https://top.test/first"]
    page.set_content("<body></body>", url="https://top.test/second")
    assert child.evaluate("[parent.location.href, top.location.href]") == \
        ["https://top.test/second", "https://top.test/second"]


def test_low_frequency_child_objects_are_created_on_first_read(page):
    page.set_content("<body></body>", url="https://secure.test/")
    screen = page.window.screen
    assert screen._values.cached_names == frozenset()
    assert page.evaluate("screen.orientation === screen.orientation") is True
    assert screen._values.cached_names == {"orientation"}

    navigator = page.window.navigator
    assert navigator._features.cached_names == frozenset()
    assert page.evaluate("navigator.locks === navigator.locks") is True
    assert navigator._features.cached_names == {"locks"}


def test_a_lock_request_runs_its_callback(page):
    page.set_content("<body></body>", url="https://secure.test/")
    page.evaluate("""window.__held = [];
        navigator.locks.request('res', lock => {
          window.__held.push(lock.name, lock.mode);
          return 'done';
        });""")
    assert page.evaluate("window.__held") == ["res", "exclusive"]
    assert page.evaluate("typeof navigator.locks.query") == "function"


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
    """) == [40, 10, 1904]


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
