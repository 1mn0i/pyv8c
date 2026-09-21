"""Behaviour this environment claims, in a form a second browser can answer.

A case is a page, one script, and what that script must observe.  ``expected`` is
pyv8c's answer and is asserted on every run; the same case also runs in the locally
installed Chrome, one record each, so a claim about page behaviour is checked against
a second implementation instead of only against what this repository believed last
time.

``known_divergence`` marks a case where the two are known to differ and carries the
reason.  A case that diverges without one fails, and a case whose divergence has gone
away fails too -- so the list cannot rot in either direction.

Keep the corpus small and purposeful: one corner per case, both engines able to
answer it, and an observable that survives being written down (a value, not a
timing).
"""
from __future__ import annotations

import dataclasses
from typing import Any

URL = "http://repro.test/dir/page"


def page(head="", body=""):
    """The document a case starts from."""
    return f"<html><head>{head}</head><body>{body}</body></html>"


#: A form holding one of everything the collection rules tell apart: an image button
#: (submittable, not listed), controls outside it that name it with ``form=``, and an
#: element whose tag is unmodelled here but is still a listed element in both engines.
FORM_PAGE = page(body="""
    <form id=f>
      <input id=one name=a>
      <input type=image id=image name=img>
      <fieldset id=fs><input id=nested name=n></fieldset>
      <object id=obj name=o></object>
      <output id=out name=u></output>
      <button id=btn name=btn value=bv>x</button>
      <select id=sel name=s></select>
      <textarea id=ta name=t></textarea>
    </form>
    <input id=outside form=f>
    <input id=other>
""")

#: A document with an empty body.  The cases that put nodes in the tree need one;
#: a document built from no markup at all is a different question, and it is asked
#: separately.
EMPTY_PAGE = page(body="")


@dataclasses.dataclass(frozen=True)
class Repro:
    name: str
    script: str
    expected: Any = None
    html: str = ""
    note: str = ""
    url: str = URL
    expect_error: str = ""
    known_divergence: str = ""


CASES = (
    Repro(
        name="title_collapses_whitespace",
        note="The IDL attribute collapses; the element keeps what was written.",
        html=page(head="<title>  Live   Page </title>"),
        script="document.title",
        expected="Live Page",
    ),
    Repro(
        name="title_element_keeps_raw_text",
        html=page(head="<title>  Live   Page </title>"),
        script="document.querySelector('title').textContent",
        expected="  Live   Page ",
    ),
    Repro(
        name="get_elements_by_name_is_live",
        note="The list follows the tree; querySelectorAll does not.",
        html=page(body="<form name=login><input name=user><input name=user></form>"
                       "<p name=user></p>"),
        script="""
            (() => {
              const list = document.getElementsByName('user');
              const before = list.length;
              document.body.appendChild(document.createElement('i'))
                .setAttribute('name', 'user');
              return [before, list.length];
            })()
        """,
        expected=[3, 4],
    ),
    Repro(
        name="get_elements_by_name_keeps_document_order",
        html=page(body="<form name=login><input name=user><input name=user></form>"
                       "<p name=user></p>"),
        script="Array.from(document.getElementsByName('user'), node => node.tagName)",
        expected=["INPUT", "INPUT", "P"],
    ),
    Repro(
        name="static_and_live_collections_differ",
        html=page(body="<div id=one></div>"),
        script="""
            (() => {
              const static_list = document.querySelectorAll('div');
              const live_list = document.getElementsByTagName('div');
              const before = [static_list.length, live_list.length];
              document.body.appendChild(document.createElement('div'));
              return [before, [static_list.length, live_list.length]];
            })()
        """,
        expected=[[1, 1], [1, 2]],
    ),
    Repro(
        name="anchor_href_resolves_against_the_document",
        html=page(body="<a id=link href=\"/dir/target?q=1#frag\">link</a>"),
        script="""
            (() => {
              const anchor = document.getElementById('link');
              return [anchor.href, anchor.protocol, anchor.pathname, anchor.search,
                      anchor.hash];
            })()
        """,
        expected=["http://repro.test/dir/target?q=1#frag", "http:", "/dir/target",
                  "?q=1", "#frag"],
    ),
    Repro(
        name="select_value_clears_selection_when_nothing_matches",
        note="Setting .value clears every option first, then selects the first match --"
        " so a value that matches nothing, 'b' against an option worth 'B', leaves the"
        " select with no selection at all instead of keeping the previous one.",
        html=page(body="<select><option value=a>A</option><option>B</option></select>"),
        script="""
            (() => {
              const options = document.querySelectorAll('option');
              document.querySelector('select').value = 'b';
              return [options[0].selected, options[1].selected, options[1].value];
            })()
        """,
        expected=[False, False, "B"],
    ),
    Repro(
        name="a_select_exposes_its_options_collection",
        note="The collection is the same object every read, and it is live.",
        html=page(body="<select><option value=a>A</option><option value=b>B</option></select>"),
        script="""
            (() => {
              const select = document.querySelector('select');
              const options = select.options;
              const before = options.length;
              const added = document.createElement('option');
              added.value = 'c';
              select.appendChild(added);
              return [before, options.length, options[2] === added, options.item(1).value,
                      select.length, options === select.options];
            })()
        """,
        expected=[2, 3, True, "b", 3, True],
    ),
    Repro(
        name="a_select_reports_its_own_value_and_index",
        html=page(body="<select><option value=a>A</option></select>"),
        script="""
            (() => {
              const select = document.querySelector('select');
              select.value = 'a';
              return [typeof select.selectedIndex, select.value];
            })()
        """,
        expected=["number", "a"],
    ),
    Repro(
        name="selected_index_moves_the_selection",
        note="A position outside the list clears the selection rather than clamping it.",
        html=page(body="<select><option value=a>A</option><option value=b>B</option>"
                       "<option value=c>C</option></select>"),
        script="""
            (() => {
              const select = document.querySelector('select');
              select.selectedIndex = 2;
              const chosen = [select.selectedIndex, select.value,
                              select.options.selectedIndex, select.selectedOptions.length];
              select.selectedIndex = -1;
              return [chosen, [select.selectedIndex, select.value,
                               select.selectedOptions.length]];
            })()
        """,
        expected=[[2, "c", 2, 1], [-1, "", 0]],
    ),
    Repro(
        name="form_controls_are_in_document_order",
        html=page(body="<form><input name=a><input name=b><button name=c></button></form>"),
        script="""
            (() => {
              const controls = document.forms[0].querySelectorAll('input, button');
              return [controls.length, controls[1].name, controls[2].tagName,
                      Array.from(controls, element => element.tagName).join(',')];
            })()
        """,
        expected=[3, "b", "BUTTON", "INPUT,INPUT,BUTTON"],
    ),
    Repro(
        name="a_button_reflects_name_value_and_type",
        note="type reports one of its three states: a value it does not know, and a"
        " missing attribute, are both submit.",
        script="""
            (() => {
              const button = document.createElement('button');
              const empty = [typeof button.name, button.name, button.value, button.type];
              button.name = 'n';
              button.value = 'v';
              const written = [button.name, button.getAttribute('name'),
                               button.getAttribute('value')];
              button.setAttribute('type', 'reset');
              const reset = button.type;
              button.type = 'bogus';
              return [empty, written, reset, [button.getAttribute('type'), button.type]];
            })()
        """,
        expected=[["string", "", "", "submit"], ["n", "n", "v"], "reset", ["bogus", "submit"]],
    ),
    Repro(
        name="a_form_control_reports_its_name",
        html=FORM_PAGE,
        script="[document.getElementById('sel').name, document.getElementById('ta').name,"
               " document.getElementById('btn').name]",
        expected=["s", "t", "btn"],
    ),
    Repro(
        name="form_elements_follows_the_document_order",
        note="Ordered by the document, not by the form: a control that names the form"
        " with form= sits where the document puts it, and an image button is not listed.",
        html=FORM_PAGE,
        script="""
            (() => {
              const form = document.forms[0];
              return [Array.from(form.elements, element => element.id), form.elements.length,
                      form.length, form.elements === form.elements];
            })()
        """,
        expected=[["one", "fs", "nested", "obj", "out", "btn", "sel", "ta", "outside"],
                  9, 9, True],
    ),
    Repro(
        name="form_elements_is_indexed_named_and_live",
        html=FORM_PAGE,
        script="""
            (() => {
              const form = document.forms[0];
              const elements = form.elements;
              const before = elements.length;
              const added = document.createElement('input');
              added.id = 'late';
              form.appendChild(added);
              return [elements.namedItem('outside') === document.getElementById('outside'),
                      elements.one === document.getElementById('one'),
                      elements[1] === document.getElementById('fs'),
                      before, elements.length,
                      Array.from(elements, element => element.id).pop()];
            })()
        """,
        expected=[True, True, True, 9, 10, "outside"],
    ),
    Repro(
        name="a_control_belongs_to_the_form_its_attribute_names",
        note="form= wins over the ancestor: naming a form that is not there leaves the"
        " control owned by nobody rather than by the form it sits in.",
        html=page(body="<form id=first><input id=inside form=nosuch><input id=plain>"
                       "<input id=moved form=second></form><form id=second></form>"),
        script="[Array.from(document.forms[0].elements, element => element.id),"
               " Array.from(document.forms[1].elements, element => element.id)]",
        expected=[["plain"], ["moved"]],
    ),
    Repro(
        name="computed_style_follows_the_cascade",
        html=page(
            head="<style>div { font-size: 20px } #sized { width: 40px; display: block }</style>",
            body="<div><span id=sized>x</span></div>",
        ),
        script="""
            (() => {
              const style = getComputedStyle(document.getElementById('sized'));
              return [style.fontSize, style.width];
            })()
        """,
        expected=["20px", "40px"],
    ),
    Repro(
        name="innerhtml_round_trip",
        html=page(body="<div id=host><b>x</b></div>"),
        script="""
            (() => {
              const host = document.getElementById('host');
              host.innerHTML = '<i>y</i>';
              return [host.innerHTML, host.outerHTML, host.childNodes.length];
            })()
        """,
        expected=["<i>y</i>", '<div id="host"><i>y</i></div>', 1],
    ),
    Repro(
        name="class_list_add_and_contains",
        script="""
            (() => {
              const element = document.createElement('div');
              element.classList.add('a', 'b');
              return [element.className, element.classList.length,
                      element.classList.contains('b'), element.classList.contains('c')];
            })()
        """,
        expected=["a b", 2, True, False],
    ),
    Repro(
        name="an_element_exposes_its_dataset",
        script="typeof document.createElement('div').dataset",
        expected="object",
    ),
    Repro(
        name="dataset_writes_reads_and_deletes_its_attributes",
        note="A map name and its data-* attribute are the same value from two sides.",
        script="""
            (() => {
              const element = document.createElement('div');
              element.dataset.fooBar = 'x';
              const written = element.getAttribute('data-foo-bar');
              delete element.dataset.fooBar;
              return [written, element.hasAttribute('data-foo-bar'),
                      typeof element.dataset.fooBar];
            })()
        """,
        expected=["x", False, "undefined"],
    ),
    Repro(
        name="dataset_reads_attributes_written_as_attributes",
        script="""
            (() => {
              const element = document.createElement('div');
              element.setAttribute('data-x-y', 'v');
              element.setAttribute('data-plain', '2');
              return [element.dataset.xY, element.dataset.plain,
                      Object.keys(element.dataset).sort(),
                      'xY' in element.dataset, 'absent' in element.dataset];
            })()
        """,
        expected=["v", "2", ["plain", "xY"], True, False],
    ),
    Repro(
        name="document_cookie_writes_and_reads_back",
        note="Needs a real origin, which the reference side gets from its routed URL.",
        script="(() => { document.cookie = 'a=1'; return document.cookie; })()",
        expected="a=1",
    ),
    Repro(
        name="instanceof_reaches_the_interfaces",
        html=page(body="<p id=para>x</p>"),
        script="[document instanceof Document, window instanceof Window,"
               " document.body instanceof HTMLElement, document.body instanceof Node]",
        expected=[True, True, True, True],
    ),
    Repro(
        name="a_thrown_error_keeps_its_name",
        script="(() => { throw new TypeError('boom'); })()",
        expect_error="TypeError",
    ),
    Repro(
        name="json_crosses_the_boundary_unchanged",
        note="Sanity for the harness itself: the record has to survive the transport.",
        script="JSON.stringify({a: [1, 2], b: 'x'})",
        expected='{"a":[1,2],"b":"x"}',
    ),
    # -- events ----------------------------------------------------------------
    Repro(
        name="event_listener_order_is_capture_then_target_then_bubble",
        note="A capture listener on the ancestor runs before the target's own, and a "
             "bubble one on that ancestor after it, in registration order.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const log = [];
              const outer = document.createElement('div');
              const inner = document.createElement('span');
              outer.appendChild(inner);
              document.body.appendChild(outer);
              outer.addEventListener('click', () => log.push('outer-capture'), true);
              outer.addEventListener('click', () => log.push('outer-bubble'));
              inner.addEventListener('click', () => log.push('inner'));
              outer.addEventListener('click', () => log.push('outer-bubble-2'));
              inner.dispatchEvent(new Event('click', {bubbles: true}));
              return log;
            })()
        """,
        expected=["outer-capture", "inner", "outer-bubble", "outer-bubble-2"],
    ),
    Repro(
        name="event_once_and_removed_listeners",
        note="A once listener is gone after its first call, and removeEventListener "
             "after that has nothing left to remove.",
        script="""
            (() => {
              const target = document.createElement('div');
              const log = [];
              const first = () => log.push('first');
              target.addEventListener('x', first, {once: true});
              target.addEventListener('x', () => log.push('second'));
              target.dispatchEvent(new Event('x'));
              target.dispatchEvent(new Event('x'));
              target.removeEventListener('x', first);
              target.dispatchEvent(new Event('x'));
              return log;
            })()
        """,
        expected=["first", "second", "second", "second"],
    ),
    Repro(
        name="event_prevent_default_decides_the_return_value",
        note="dispatchEvent reports whether the event was not cancelled, so only a "
             "cancelable event a listener cancelled answers false.",
        script="""
            (() => {
              const target = document.createElement('div');
              const results = [];
              target.addEventListener('y', event => event.preventDefault());
              results.push(target.dispatchEvent(new Event('y', {cancelable: true})));
              results.push(target.dispatchEvent(new Event('y')));
              results.push(target.dispatchEvent(new Event('z')));
              return results;
            })()
        """,
        expected=[False, True, True],
    ),
    Repro(
        name="event_stop_propagation_keeps_the_event_off_the_ancestor",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const log = [];
              const outer = document.createElement('div');
              const inner = document.createElement('span');
              outer.appendChild(inner);
              document.body.appendChild(outer);
              outer.addEventListener('click', () => log.push('outer'));
              inner.addEventListener('click', event => {
                log.push('inner');
                event.stopPropagation();
              });
              inner.dispatchEvent(new Event('click', {bubbles: true}));
              return log;
            })()
        """,
        expected=["inner"],
    ),
    Repro(
        name="event_reports_its_dispatch_state",
        script="""
            (() => {
              const target = document.createElement('div');
              let seen = null;
              target.addEventListener('k', event => {
                seen = [event.type, event.target === target, event.currentTarget === target,
                        event.eventPhase, event.bubbles, event.cancelable,
                        event.defaultPrevented];
              });
              target.dispatchEvent(new Event('k', {bubbles: true, cancelable: true}));
              return seen;
            })()
        """,
        expected=["k", True, True, 2, True, True, False],
    ),
    # -- tree and node semantics -----------------------------------------------
    Repro(
        name="insert_before_places_and_appends",
        note="A second argument of null appends, and the node lands where it was "
             "asked for.",
        script="""
            (() => {
              const names = ['a', 'b', 'c', 'd', 'e'];
              const parent = document.createElement('ul');
              const nodes = {};
              for (const name of ['a', 'b', 'c']) {
                nodes[name] = document.createElement('li');
                parent.appendChild(nodes[name]);
              }
              nodes.d = document.createElement('li');
              parent.insertBefore(nodes.d, nodes.b);
              nodes.e = document.createElement('li');
              parent.insertBefore(nodes.e, null);
              return [Array.from(parent.children,
                                 node => names.find(name => nodes[name] === node)),
                      parent.childNodes.length];
            })()
        """,
        expected=[["a", "d", "b", "c", "e"], 5],
    ),
    Repro(
        name="insert_before_moves_an_existing_node",
        script="""
            (() => {
              const parent = document.createElement('div');
              const nodes = {};
              for (const id of ['a', 'b', 'c']) {
                nodes[id] = document.createElement('p');
                nodes[id].id = id;
                parent.appendChild(nodes[id]);
              }
              parent.insertBefore(nodes.c, nodes.a);
              return [Array.from(parent.children, node => node.id), parent.children.length];
            })()
        """,
        expected=[["c", "a", "b"], 3],
    ),
    Repro(
        name="clone_node_is_shallow_or_deep_but_never_the_same_object",
        script="""
            (() => {
              const node = document.createElement('div');
              node.id = 'x';
              node.innerHTML = '<span id="child">t</span>';
              const shallow = node.cloneNode(false);
              const deep = node.cloneNode(true);
              return [shallow.childNodes.length, deep.childNodes.length, deep.firstChild.id,
                      deep === node, deep.isEqualNode(node),
                      deep.firstChild === node.firstChild, deep.isSameNode(node)];
            })()
        """,
        expected=[0, 1, "child", False, True, False, False],
    ),
    Repro(
        name="text_content_skips_comments",
        note="A comment is character data with a textContent of its own, but it is "
             "not text and so contributes nothing to an ancestor's.",
        script="""
            (() => {
              const div = document.createElement('div');
              div.innerHTML = 'a<b>c</b>d<!--e-->';
              const text = document.createTextNode('t');
              const comment = document.createComment('c');
              return [div.textContent, div.childNodes.length, text.textContent,
                      comment.textContent, text.nodeValue, comment.data,
                      div.firstChild.data];
            })()
        """,
        expected=["acd", 4, "t", "c", "t", "c", "a"],
    ),
    Repro(
        name="contains_and_document_position",
        note="compareDocumentPosition reports 20 for a descendant: following and "
             "contained by.",
        script="""
            (() => {
              const parent = document.createElement('div');
              const child = document.createElement('span');
              parent.appendChild(child);
              const other = document.createElement('p');
              return [parent.contains(child), child.contains(parent), parent.contains(parent),
                      other.contains(child), parent.compareDocumentPosition(child)];
            })()
        """,
        expected=[True, False, True, False, 20],
    ),
    Repro(
        name="normalize_merges_adjacent_text_and_drops_the_empty_one",
        script="""
            (() => {
              const div = document.createElement('div');
              div.appendChild(document.createTextNode('a'));
              div.appendChild(document.createTextNode('b'));
              div.appendChild(document.createElement('i'));
              div.appendChild(document.createTextNode(''));
              const before = div.childNodes.length;
              div.normalize();
              return [before, div.childNodes.length, div.textContent,
                      Array.from(div.childNodes, node => node.nodeName)];
            })()
        """,
        expected=[4, 2, "ab", ["#text", "I"]],
    ),
    # -- reflection -------------------------------------------------------------
    Repro(
        name="input_reflects_its_constraints",
        note="maxlength does not truncate a value a script writes.",
        script="""
            (() => {
              const input = document.createElement('input');
              input.type = 'text';
              input.maxLength = 5;
              input.value = 'abcdefgh';
              const required = document.createElement('input');
              required.required = true;
              const disabled = document.createElement('input');
              disabled.disabled = true;
              return [input.maxLength, input.getAttribute('maxlength'), input.value,
                      required.required, required.hasAttribute('required'),
                      disabled.disabled, disabled.hasAttribute('disabled'), input.type];
            })()
        """,
        expected=[5, "5", "abcdefgh", True, True, True, True, "text"],
    ),
    Repro(
        name="an_option_derives_text_label_and_value_from_its_text",
        note="With no value or label attribute, all three are the option's own text, "
             "with whitespace collapsed.",
        script="""
            (() => {
              const option = document.createElement('option');
              option.textContent = '  Spaced   text ';
              const derived = [option.text, option.label, option.value];
              option.label = 'L';
              const labelled = [option.label, option.getAttribute('label'), option.text];
              option.value = 'v';
              return [derived, labelled, option.value];
            })()
        """,
        expected=[["Spaced text", "Spaced text", "Spaced text"],
                  ["L", "L", "Spaced text"], "v"],
    ),
    Repro(
        name="image_dimensions_are_dimension_values",
        note="width and height read the number an attribute starts with, so a "
             "dimension written with a unit still reads back, and a negative one is "
             "stored as 0 rather than wrapped around.",
        script="""
            (() => {
              const read = value => {
                const image = document.createElement('img');
                image.setAttribute('width', value);
                return [value, image.width];
              };
              const image = document.createElement('img');
              const empty = [image.width, image.height];
              image.height = '30';
              image.width = -5;
              return [empty, read('12px'), read('12%'), read('12.9'), read('abc'),
                      read('-5'), read(' 12 '),
                      [image.width, image.getAttribute('width'), image.height,
                       typeof image.width]];
            })()
        """,
        expected=[[0, 0], ["12px", 12], ["12%", 12], ["12.9", 12], ["abc", 0],
                  ["-5", 0], [" 12 ", 12], [0, "0", 30, "number"]],
    ),
    Repro(
        name="selector_collections_and_closest",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const root = document.createElement('div');
              root.innerHTML = '<p class="a b" id="one"><span class="b" id="two">t</span></p>'
                             + '<p class="a" id="three"></p>';
              document.body.appendChild(root);
              const two = document.getElementById('two');
              return [root.getElementsByClassName('a b').length,
                      root.querySelectorAll('p.a').length,
                      root.querySelectorAll('p > span').length,
                      two.closest('p').id, two.matches('.b'), two.matches('p span'),
                      Array.from(root.children, node => node.id)];
            })()
        """,
        expected=[1, 2, 1, "one", True, True, ["one", "three"]],
    ),
    # -- URLs ------------------------------------------------------------------
    Repro(
        name="url_search_params_mutation_reaches_the_url",
        script="""
            (() => {
              const url = new URL('https://example.test:8443/a/b?x=1&y=2#frag');
              const params = url.searchParams;
              params.append('z', '3');
              params.delete('x');
              return [url.href, url.port, url.pathname, url.search, url.hash,
                      params.toString(), params.get('y'), params.has('x'), url.origin,
                      params.size];
            })()
        """,
        expected=["https://example.test:8443/a/b?y=2&z=3#frag", "8443", "/a/b", "?y=2&z=3",
                  "#frag", "y=2&z=3", "2", False, "https://example.test:8443", 2],
    ),
    Repro(
        name="url_search_params_iterators",
        note="Duplicated names keep their order in every view.",
        script="""
            (() => {
              const params = new URLSearchParams('a=1&b=2&a=3');
              return [Array.from(params.keys()), Array.from(params.values()),
                      Array.from(params.entries()), params.size];
            })()
        """,
        expected=[["a", "b", "a"], ["1", "2", "3"],
                  [["a", "1"], ["b", "2"], ["a", "3"]], 3],
    ),
    Repro(
        name="url_relative_resolution",
        script="new URL('../x?q=1', 'https://example.test/dir/page').href",
        expected="https://example.test/x?q=1",
    ),
    Repro(
        name="url_spaces_are_percent_encoded_wherever_they_sit",
        note="Including the authority and the userinfo; a space in the port is what "
             "no URL can have.",
        script="""
            (() => {
              const encoded = new URL('../x?q=1 2', 'https://example.test/dir/page');
              const made = ['https://a.te st/', 'https://u s@a.test/', 'https://a.test:80 80/'];
              return [encoded.href, new URL('https://a.test/p q#f 3').href,
                      new URL('  https://a.test/ok\\t').href,
                      made.map(text => {
                        try { return new URL(text).href; } catch (error) { return 'REJECTED'; }
                      })];
            })()
        """,
        expected=["https://example.test/x?q=1%202", "https://a.test/p%20q#f%203",
                  "https://a.test/ok",
                  ["https://a.te%20st/", "https://u%20s@a.test/", "REJECTED"]],
    ),
    # -- what is still missing here --------------------------------------------
    Repro(
        name="table_rows_and_cells",
        note="Excluded: HTMLTableElement.rows is not implemented, so a table cannot "
             "be walked the way a page walks it.",
        script="""
            (() => {
              const table = document.createElement('table');
              table.innerHTML = '<tbody><tr><td>1</td><td>2</td></tr>'
                              + '<tr><td>3</td></tr></tbody>';
              const rows = table.rows;
              return [rows.length, rows[0].cells.length, rows[1].cells[0].cellIndex,
                      rows[1].rowIndex, rows[0].cells[0].parentNode.tagName];
            })()
        """,
        expect_error="TypeError",
        known_divergence="HTMLTableElement.rows and the row/cell collections are not"
        " implemented; Chrome answers [2, 2, 0, 1, 'TR']",
    ),
    Repro(
        name="a_label_element_and_its_html_for",
        note="Excluded: <label> is not a modelled tag here, so it comes out as an "
             "HTMLUnknownElement with no htmlFor.",
        html=page(body="<label for=f>L</label>"),
        script="(() => { const label = document.querySelector('label');"
               " return [label.constructor.name, typeof label.htmlFor]; })()",
        expected=["HTMLUnknownElement", "undefined"],
        known_divergence="HTMLLabelElement is not modelled; Chrome reports"
        " ['HTMLLabelElement', 'string'] for the same page",
    ),
    Repro(
        name="an_empty_document_still_has_html_head_and_body",
        note="Excluded: parsed from no markup at all this build has no document "
             "element, where a browser builds the skeleton every document has.",
        script="[document.documentElement.tagName, document.body !== null]",
        expect_error="TypeError",
        known_divergence="a document set from an empty string has no html/head/body"
        " here; Chrome reports ['HTML', true]",
    ),
    Repro(
        name="url_search_params_is_itself_iterable",
        note="Excluded: the views iterate, but the object does not, so Array.from of "
             "the params themselves finds nothing.",
        script="Array.from(new URLSearchParams('a=1&b=2'))",
        expected=[],
        known_divergence="URLSearchParams has no @@iterator of its own; Chrome yields"
        " [['a', '1'], ['b', '2']]",
    ),
)
