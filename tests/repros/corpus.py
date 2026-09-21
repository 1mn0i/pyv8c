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
        note="A table is walked the way a page walks it: its rows in order, then the "
             "cells of a row.",
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
        expected=[2, 2, 0, 1, "TR"],
    ),
    Repro(
        name="rows_come_from_the_sections_in_document_order",
        note="A row's place is its place among all the table's rows, whichever "
             "section holds it.",
        script="""
            (() => {
              const table = document.createElement('table');
              document.body.appendChild(table);
              table.innerHTML = '<thead><tr><td>h</td></tr></thead>'
                              + '<tbody><tr><td>b</td></tr></tbody>'
                              + '<tfoot><tr><td>f</td></tr></tfoot>';
              return [table.rows.length, table.rows[0].cells[0].textContent,
                      table.rows[2].cells[0].textContent, table.rows[1].rowIndex];
            })()
        """,
        expected=[3, "h", "f", 1],
    ),
    Repro(
        name="a_nested_table_keeps_its_own_rows",
        note="A table nested inside a cell is a table of its own: its rows belong to "
             "it, not to the table around it.",
        script="""
            (() => {
              const table = document.createElement('table');
              document.body.appendChild(table);
              table.innerHTML = '<tr><td>1</td></tr>'
                              + '<tr><td><table><tr><td>n</td></tr></table></td></tr>';
              return [table.rows.length, table.rows[1].cells.length,
                      table.querySelectorAll('tr').length];
            })()
        """,
        expected=[2, 1, 3],
    ),
    Repro(
        name="a_th_counts_as_a_cell",
        note="A heading cell is a cell: it is in the row's collection and knows its "
             "place in it.",
        script="""
            (() => {
              const row = document.createElement('tr');
              const head = document.createElement('th');
              row.appendChild(head);
              document.body.appendChild(row);
              return [row.cells.length, head.cellIndex,
                      head instanceof HTMLTableCellElement, head.constructor.name,
                      row.constructor.name];
            })()
        """,
        expected=[1, 0, True, "HTMLTableCellElement", "HTMLTableRowElement"],
    ),
    Repro(
        name="row_and_cell_indexes_outside_a_table",
        note="A cell knows its place as soon as a row holds it, while a row that no "
             "table owns has no place to report.",
        script="""
            (() => {
              const row = document.createElement('tr');
              const cell = document.createElement('td');
              row.appendChild(cell);
              return [row.rowIndex, cell.cellIndex, document.createElement('td').cellIndex,
                      document.createElement('tr').rowIndex];
            })()
        """,
        expected=[-1, 0, -1, -1],
    ),
    Repro(
        name="rows_and_cells_are_live_and_held_onto",
        note="Reaching for the collection twice gives the same live object, so it "
             "shows a row or cell added afterwards.",
        script="""
            (() => {
              const table = document.createElement('table');
              document.body.appendChild(table);
              table.innerHTML = '<tr><td>1</td></tr>';
              const rows = table.rows;
              const cells = rows[0].cells;
              const added = document.createElement('tr');
              table.appendChild(added);
              rows[0].appendChild(document.createElement('td'));
              return [rows.length, cells.length, table.rows === rows,
                      rows[0].cells === cells, added.rowIndex, table.rows[1] === added];
            })()
        """,
        expected=[2, 2, True, True, 1, True],
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
        note="No markup at all still parses into the skeleton every document has.",
        script="[document.documentElement.tagName, document.body !== null,"
               " document.head !== null]",
        expected=["HTML", True, True],
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
    # -- fetch types -----------------------------------------------------------
    Repro(
        name="headers_construction_and_lookup",
        note="Names are case-insensitive and appending combines with ', '.",
        script="""
            (() => {
              const headers = new Headers({'content-type': 'text/html'});
              headers.append('x-a', '1');
              headers.append('x-a', '2');
              headers.set('X-B', 'b');
              headers.delete('content-type');
              return [headers.get('X-A'), headers.get('x-a'), headers.has('Content-Type'),
                      headers.get('x-b'), headers.has('nope')];
            })()
        """,
        expected=["1, 2", "1, 2", False, "b", False],
    ),
    Repro(
        name="headers_copy_and_override",
        script="""
            (() => {
              const first = new Headers([['a', '1'], ['b', '2']]);
              const second = new Headers(first);
              second.set('A', '9');
              return [first.get('a'), second.get('a'), second.get('b'),
                      new Headers().get('a') === null];
            })()
        """,
        expected=["1", "9", "2", True],
    ),
    Repro(
        name="headers_iterate_sorted_by_name",
        note="The list is sorted by name however it was built.",
        script="""
            (() => {
              const headers = new Headers([['b', '2'], ['a', '1']]);
              return [Array.from(headers.keys()), Array.from(headers.values()),
                      Array.from(headers.entries())];
            })()
        """,
        expected=[["a", "b"], ["1", "2"], [["a", "1"], ["b", "2"]]],
    ),
    Repro(
        name="request_fields",
        script="""
            (() => {
              const request = new Request('http://example.test/a?x=1');
              return [request.method, request.url, request.headers.get('content-type'),
                      request.bodyUsed, request.destination, request.mode];
            })()
        """,
        expected=["GET", "http://example.test/a?x=1", None, False, "", "cors"],
    ),
    Repro(
        name="request_resolves_a_relative_url_and_keeps_its_init",
        script="""
            (() => {
              const request = new Request('/x', {method: 'post', headers: {'x-a': '1'}});
              return [request.url, request.method, request.headers.get('X-A')];
            })()
        """,
        expected=["http://repro.test/x", "POST", "1"],
    ),
    Repro(
        name="response_fields",
        note="A response a script builds is type 'default', not 'basic'.",
        script="""
            (() => {
              const response = new Response('hello', {status: 201, statusText: 'Created',
                                                      headers: {'x-a': '1'}});
              return [response.status, response.ok, response.statusText,
                      response.headers.get('x-a'), response.bodyUsed, response.type,
                      response.redirected, response.url];
            })()
        """,
        expected=[201, True, "Created", "1", False, "default", False, ""],
    ),
    Repro(
        name="response_clone_is_independent",
        script="""
            (() => {
              const response = new Response('hello', {status: 201, headers: {'x-a': '1'}});
              const copy = response.clone();
              copy.headers.set('x-a', '2');
              return [copy instanceof Response, copy !== response, copy.bodyUsed,
                      response.bodyUsed, [response.status, copy.status],
                      [response.headers.get('x-a'), copy.headers.get('x-a')]];
            })()
        """,
        expected=[True, True, False, False, [201, 201], ["1", "2"]],
    ),
    # -- storage ----------------------------------------------------------------
    # Chromium's key order is its own, so a case that reads the keys sorts them.
    Repro(
        name="storage_round_trip_length_and_order",
        html=EMPTY_PAGE,
        script="""
            (() => {
              localStorage.clear();
              localStorage.setItem('a', '1');
              localStorage.setItem('b', '2');
              localStorage.setItem('c', '3');
              const keys = [];
              for (let index = 0; index < localStorage.length; index += 1) {
                keys.push(localStorage.key(index));
              }
              const removed = localStorage.removeItem('b');
              return [localStorage.getItem('a'), localStorage.getItem('b'), keys.sort(),
                      localStorage.length, removed, localStorage.key(9)];
            })()
        """,
        expected=["1", None, ["a", "b", "c"], 2, None, None],
    ),
    Repro(
        name="storage_coerces_values_and_keys",
        html=EMPTY_PAGE,
        script="""
            (() => {
              localStorage.clear();
              localStorage.setItem(7, 42);
              return [localStorage.getItem('7'), localStorage.getItem(7),
                      localStorage.length, localStorage.getItem('missing')];
            })()
        """,
        expected=["42", "42", 1, None],
    ),
    Repro(
        name="storage_clears_and_separates_the_two_kinds",
        html=EMPTY_PAGE,
        script="""
            (() => {
              localStorage.clear();
              sessionStorage.clear();
              localStorage.setItem('a', '1');
              sessionStorage.setItem('a', '2');
              const both = [localStorage.getItem('a'), sessionStorage.getItem('a')];
              localStorage.clear();
              return [both, [localStorage.length, sessionStorage.length],
                      localStorage === localStorage];
            })()
        """,
        expected=[["1", "2"], [0, 1], True],
    ),
    Repro(
        name="storage_as_named_properties",
        note="A stored name is also a property, and the prototype's members stay "
             "themselves.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              localStorage.clear();
              localStorage.token = 'v';
              const read = [localStorage.token, localStorage.getItem('token'),
                            'token' in localStorage];
              delete localStorage.token;
              return [read, [localStorage.token, localStorage.getItem('token')],
                      localStorage.getItem === undefined ? 'shadowed' : 'kept'];
            })()
        """,
        expected=[["v", "v", True], [None, None], "kept"],
    ),
    # -- selectors --------------------------------------------------------------
    Repro(
        name="attribute_and_child_selectors",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const root = document.createElement('div');
              root.innerHTML = '<p class="x" data-kind="a">1</p><p data-kind="b">2</p>'
                             + '<span data-kind="a">3</span>';
              document.body.appendChild(root);
              return [root.querySelectorAll('p[data-kind]').length,
                      root.querySelectorAll('[data-kind="a"]').length,
                      root.querySelectorAll('div > p').length,
                      root.querySelectorAll('p + p').length,
                      root.querySelector('p[data-kind="b"]').textContent];
            })()
        """,
        expected=[2, 2, 2, 1, "2"],
    ),
    Repro(
        name="nth_child_and_first_last_selectors",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const list = document.createElement('ul');
              list.innerHTML = '<li>1</li><li>2</li><li>3</li><li>4</li>';
              document.body.appendChild(list);
              return [list.querySelectorAll('li:first-child').length,
                      list.querySelectorAll('li:last-child').length,
                      list.querySelectorAll('li:nth-child(2)').length,
                      list.querySelectorAll('li:nth-child(odd)').length,
                      list.querySelector('li:nth-child(3)').textContent];
            })()
        """,
        expected=[1, 1, 1, 2, "3"],
    ),
    Repro(
        name="not_and_group_selectors",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const root = document.createElement('div');
              root.innerHTML = '<p class="a">1</p><p class="b">2</p><span class="a">3</span>';
              document.body.appendChild(root);
              return [root.querySelectorAll('p:not(.a)').length,
                      root.querySelectorAll('p, span').length,
                      root.querySelectorAll('.a').length,
                      root.querySelectorAll('p:not(.b):not(.c)').length];
            })()
        """,
        expected=[1, 3, 2, 1],
    ),
    Repro(
        name="selector_lists_are_document_order_and_unique",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const root = document.createElement('div');
              root.innerHTML = '<i class="hit">1</i><b class="hit">2</b><u class="hit">3</u>';
              document.body.appendChild(root);
              const names = Array.from(root.querySelectorAll('.hit, .hit, b'),
                                       node => node.tagName);
              return [names, root.querySelectorAll('.hit').length];
            })()
        """,
        expected=[["I", "B", "U"], 3],
    ),
    # -- classList and CSSOM ----------------------------------------------------
    Repro(
        name="class_list_toggle_replace_and_value",
        note="toggle with force reports the state it left, and value writes the "
             "attribute verbatim.",
        script="""
            (() => {
              const element = document.createElement('div');
              element.className = 'a b';
              const toggled = [element.classList.toggle('c'),
                               element.classList.toggle('a'),
                               element.classList.toggle('c', true),
                               element.classList.contains('c')];
              const replaced = [element.classList.replace('b', 'd'), element.className];
              element.classList.value = 'x  y';
              return [toggled, replaced, element.className, element.classList.length];
            })()
        """,
        expected=[[True, False, True, True], [True, "d c"], "x  y", 2],
    ),
    Repro(
        name="style_set_property_and_css_text",
        note="cssText replaces the whole declaration block, and removing the last "
             "declaration leaves the attribute behind but empty.",
        script="""
            (() => {
              const element = document.createElement('div');
              element.style.setProperty('color', 'red');
              element.style.setProperty('margin-top', '4px', 'important');
              const read = [element.style.getPropertyValue('color'),
                            element.style.getPropertyPriority('margin-top'),
                            element.getAttribute('style')];
              element.style.cssText = 'padding-top: 2px; padding-left: 3px';
              return [read, [element.style.length, element.style.item(0),
                             element.style.getPropertyValue('color'),
                             element.style.cssText]];
            })()
        """,
        expected=[["red", "important", "color: red; margin-top: 4px !important;"],
                  [2, "padding-top", "", "padding-top: 2px; padding-left: 3px;"]],
    ),
    Repro(
        name="style_remove_property_returns_the_old_value",
        script="""
            (() => {
              const element = document.createElement('div');
              element.style.color = 'blue';
              const removed = element.style.removeProperty('color');
              const missing = element.style.removeProperty('color');
              return [removed, missing, element.getAttribute('style')];
            })()
        """,
        expected=["blue", "", ""],
    ),
    Repro(
        name="emptying_the_style_block_leaves_the_attribute_behind",
        note="Removing the last declaration, assigning cssText or assigning an empty "
             "value all leave style=\"\"; an element that never carried one has no "
             "attribute at all.",
        script="""
            (() => {
              const emptied = kind => {
                const element = document.createElement('div');
                element.style.color = 'blue';
                if (kind === 'remove') element.style.removeProperty('color');
                else if (kind === 'text') element.style.cssText = '';
                else element.style.color = '';
                return [element.getAttribute('style'), element.hasAttribute('style')];
              };
              const untouched = document.createElement('div');
              untouched.style.color = '';
              return [emptied('remove'), emptied('text'), emptied('value'),
                      [untouched.getAttribute('style'), untouched.hasAttribute('style')]];
            })()
        """,
        expected=[["", True], ["", True], ["", True], [None, False]],
    ),
    Repro(
        name="computed_style_reports_declared_values",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const root = document.createElement('div');
              root.innerHTML = '<div id="outer" style="color: rgb(1, 2, 3); font-size: 12px">'
                             + '<span id="inner">t</span></div>';
              document.body.appendChild(root);
              return [getComputedStyle(document.getElementById('inner')).fontSize,
                      getComputedStyle(document.getElementById('outer')).color,
                      root.querySelector('#outer').style.color];
            })()
        """,
        expected=["12px", "rgb(1, 2, 3)", "rgb(1, 2, 3)"],
    ),
    # -- what is still missing here ---------------------------------------------
    Repro(
        name="response_body_is_a_stream",
        note="Excluded: there is no ReadableStream here, so a response has no body "
             "to read as a stream.",
        script="""
            (() => {
              const response = new Response('hello');
              return [typeof response.body, response.body === null];
            })()
        """,
        expected=["undefined", False],
        known_divergence="ReadableStream is not modelled; Chrome reports ['object',"
        " false]",
    ),
    Repro(
        name="storage_spread_copies_the_entries",
        note="Excluded: Object.keys lists the stored names and reading one works, but "
             "copying them into an object does not.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              localStorage.clear();
              localStorage.setItem('a', '1');
              return JSON.stringify({...localStorage});
            })()
        """,
        expected="{}",
        known_divergence="spreading a Storage copies nothing: the own-property"
        " definer hook that path needs is not wired; Chrome yields '{\"a\":\"1\"}'",
    ),
    Repro(
        name="computed_style_inherits_an_inline_colour",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const outer = document.createElement('div');
              outer.setAttribute('style', 'color: rgb(1, 2, 3)');
              const inner = document.createElement('span');
              outer.appendChild(inner);
              document.body.appendChild(outer);
              return getComputedStyle(inner).color;
            })()
        """,
        expected="rgb(1, 2, 3)",
    ),
    Repro(
        name="computed_style_inherits_from_a_stylesheet_rule",
        note="Both children read the rule's colour, and the one that declares its own "
             "spacing keeps it.",
        html=page(body="""
            <style>.outer { color: rgb(4, 5, 6); font-weight: 700; }
                   .inner { letter-spacing: 3px; }</style>
            <div class="outer"><span class="inner">t</span></div>
            <div class="outer"><span>t</span></div>
        """),
        script="""
            (() => {
              const inner = document.querySelector('span.inner');
              const plain = document.querySelectorAll('span')[1];
              return [getComputedStyle(inner).color, getComputedStyle(plain).color,
                      getComputedStyle(plain).fontWeight,
                      getComputedStyle(inner).fontWeight,
                      getComputedStyle(inner).letterSpacing];
            })()
        """,
        expected=["rgb(4, 5, 6)", "rgb(4, 5, 6)", "700", "700", "3px"],
    ),
    Repro(
        name="a_fragment_gets_a_body_of_its_own",
        note="Markup that carries no html/body is wrapped, so the fragment's element "
             "is a child of the body rather than a node that is the body.",
        html='<div id="x" style="color: red">hi</div>',
        script="""
            (() => {
              const element = document.getElementById('x');
              return [document.body === element, document.body.tagName,
                      document.body.getAttribute('id'), document.body.getAttribute('style'),
                      document.body.childNodes.length,
                      document.documentElement.tagName,
                      document.body.parentNode === document.documentElement];
            })()
        """,
        expected=[False, "BODY", None, None, 1, "HTML", True],
    ),
    Repro(
        name="the_navigation_api_reports_the_current_entry",
        note="A page that has not moved has one entry, nothing behind or ahead of it,"
             " and no transition.",
        html=page(body=""),
        script="""
            (() => {
              const current = navigation.currentEntry;
              return [navigation.canGoBack, navigation.canGoForward,
                      navigation.entries.length, current === navigation.currentEntry,
                      current.index, current.url, current.sameDocument,
                      current.getState(), navigation.transition,
                      typeof navigation.activation, typeof current.key];
            })()
        """,
        expected=[False, False, 0, True, 0, "http://repro.test/dir/page", True,
                  None, None, "object", "string"],
    ),
    Repro(
        name="trusted_types_wraps_what_a_policy_returns",
        note="A policy calls the page's own callback and wraps the string it returns.",
        html=page(body=""),
        script="""
            (() => {
              const policy = trustedTypes.createPolicy('repro', {
                createHTML: value => '<b>' + value,
                createScriptURL: value => '/safe/' + value,
              });
              const html = policy.createHTML('x');
              const url = policy.createScriptURL('x.js');
              return [trustedTypes.emptyHTML.toString(), trustedTypes.emptyScript.toString(),
                      trustedTypes.emptyHTML === trustedTypes.emptyHTML,
                      trustedTypes.defaultPolicy === null, policy.name,
                      html.toString(), String(html), typeof html,
                      trustedTypes.isHTML(html), trustedTypes.isHTML('x'),
                      trustedTypes.isScriptURL(url), trustedTypes.isScriptURL(html),
                      url.toString()];
            })()
        """,
        expected=["", "", True, True, "repro", "<b>x", "<b>x", "object", True, False,
                  True, False, "/safe/x.js"],
    ),
    Repro(
        name="trusted_types_answers_nothing_about_attribute_types",
        note="Excluded: the spec's attribute and property type tables are not modelled,"
             " so the queries answer nothing.",
        html=page(body=""),
        script="""
            (() => {
              return [trustedTypes.getAttributeType('div', 'onclick'),
                      trustedTypes.getPropertyType('div', 'innerHTML'),
                      trustedTypes.getTypeMapping()];
            })()
        """,
        expected=["", None, None],
        known_divergence="attribute and property types come from a spec table this DOM"
        " does not model, so the queries answer nothing where Chrome consults the table",
    ),
    Repro(
        name="get_image_data_reads_the_drawn_pixels",
        note="A drawn pixel comes back as its own RGBA bytes, the array is a real"
             " Uint8ClampedArray, and outside the canvas pixels are transparent black.",
        html=page(body='<canvas id="c" width="4" height="2"></canvas>'),
        script="""
            (() => {
              const canvas = document.getElementById('c');
              const context = canvas.getContext('2d');
              context.fillStyle = '#ff0000';
              context.fillRect(0, 0, 2, 1);
              const image = context.getImageData(0, 0, 4, 2);
              const outside = context.getImageData(-1, -1, 1, 1);
              return [image.width, image.height, image.data.length,
                      image.data instanceof Uint8ClampedArray, image.colorSpace,
                      image.data === image.data, Array.from(image.data.slice(0, 4)),
                      [outside.width, outside.height, outside.data.length, outside.data[3]]];
            })()
        """,
        expected=[4, 2, 32, True, "srgb", True, [255, 0, 0, 255], [1, 1, 4, 0]],
    ),
    Repro(
        name="window_accessories_report_their_idle_state",
        note="Each accessory object is present and says nothing is happening.",
        html=page(body=""),
        script="""
            (() => {
              return [external.constructor.name, typeof external.AddSearchProvider,
                      styleMedia.type, styleMedia.matchMedium('screen'),
                      styleMedia.matchMedium('print'),
                      crashReport.constructor.name, typeof crashReport.set,
                      launchQueue.constructor.name, typeof launchQueue.setConsumer,
                      fence === null, typeof viewport.segments];
            })()
        """,
        expected=["External", "function", "screen", True, False,
                  "CrashReportContext", "function", "LaunchQueue", "function",
                  True, "object"],
    ),
    Repro(
        name="speech_synthesis_and_the_scheduler_start_idle",
        note="Nothing is spoken, and scheduling hands back a promise.",
        html=page(body=""),
        script="""
            (() => {
              return [speechSynthesis.pending, speechSynthesis.speaking,
                      speechSynthesis.paused, speechSynthesis.getVoices().length,
                      typeof speechSynthesis.speak,
                      scheduler.constructor.name, typeof scheduler.postTask,
                      typeof scheduler.yield];
            })()
        """,
        expected=[False, False, False, 0, "function",
                  "Scheduler", "function", "function"],
    ),
    Repro(
        name="a_xpath_expression_walks_the_document",
        note="One compiled expression, evaluated against the document and walked a "
             "node at a time.",
        html=page(body="<p>one</p>"),
        script="""
            (() => {
              const expression = document.createExpression('//html', null);
              const result = expression.evaluate(document, XPathResult.ANY_TYPE, null);
              const first = result.iterateNext();
              return [result.resultType, first.tagName, result.iterateNext(),
                      result instanceof XPathResult];
            })()
        """,
        expected=[4, "HTML", None, True],
    ),
    Repro(
        name="xpath_predicates_and_snapshots",
        note="An attribute predicate and a positional one select what a page expects, "
             "and a snapshot reports its length and hands nodes back by index.",
        html=page(body='<p class="x">1</p><p class="x">2</p><p>3</p>'),
        script="""
            (() => {
              const snapshot = document.evaluate("//p[@class='x']", document, null,
                  XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
              const second = document.evaluate("//p[2]", document, null,
                  XPathResult.FIRST_ORDERED_NODE_TYPE, null);
              const none = document.evaluate("//p[@class='z']", document, null,
                  XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
              return [snapshot.snapshotLength, snapshot.snapshotItem(0).textContent,
                      snapshot.snapshotItem(1).textContent, second.singleNodeValue.textContent,
                      none.snapshotLength, snapshot.snapshotItem(9)];
            })()
        """,
        expected=[2, "1", "2", "2", 0, None],
    ),
    Repro(
        name="an_xpath_function_is_outside_the_subset",
        note="Excluded: functions and the other axes are refused rather than answered "
             "with the wrong nodes.",
        html=page(body="<p>one</p>"),
        script="""
            (() => {
              return document.evaluate("count(//p)", document, null,
                  XPathResult.NUMBER_TYPE, null).numberValue;
            })()
        """,
        expect_error="NotSupportedError",
        known_divergence="this DOM evaluates a path subset only, so count(//p) raises "
        "NotSupportedError where Chrome answers 1",
    ),
    Repro(
        name="a_lock_manager_needs_a_secure_context",
        note="No lock manager is offered outside a secure context, which is where "
             "Chrome offers one too.",
        html=page(body=""),
        script="navigator.locks === undefined",
        expected=True,
    ),
    Repro(
        name="a_custom_element_registry_remembers_its_definitions",
        note="Defining a name makes it readable back, and an unregistered class has"
             " no name at all.",
        html=page(body=""),
        script="""
            (() => {
              const Ctor = function () {};
              const unknown = [customElements.get('x-unknown'), customElements.getName(Ctor)];
              customElements.define('x-corpus', Ctor);
              return [unknown, customElements.get('x-corpus') === Ctor,
                      customElements.getName(Ctor),
                      typeof customElements.whenDefined('x-corpus').then];
            })()
        """,
        expected=[[None, None], True, "x-corpus", "function"],
    ),
    Repro(
        name="defining_a_name_without_a_dash_is_rejected",
        html=page(body=""),
        script="customElements.define('nodash', function () {})",
        expect_error="SyntaxError",
    ),
    Repro(
        name="the_visual_viewport_matches_the_window_here",
        html=page(body=""),
        script="""
            (() => {
              const view = visualViewport;
              return [view.width === innerWidth, view.height === innerHeight, view.scale,
                      view.offsetLeft, view.offsetTop, view.pageLeft, view.pageTop,
                      view === visualViewport];
            })()
        """,
        expected=[True, True, 1, 0, 0, 0, 0, True],
    ),
    Repro(
        name="create_event_makes_an_uninitialised_event",
        html=page(body=""),
        script="""
            (() => {
              const made = ['Event', 'HTMLEvents', 'MouseEvent'].map(
                  name => document.createEvent(name).constructor.name);
              return [made, document.createEvent('Event').type,
                      document.createEvent('MouseEvent').type];
            })()
        """,
        expected=[["Event", "Event", "MouseEvent"], "", ""],
    ),
    Repro(
        name="create_event_rejects_a_type_it_does_not_know",
        html=page(body=""),
        script="document.createEvent('Nope')",
        expect_error="NotSupportedError",
    ),
    Repro(
        name="a_form_action_falls_back_to_the_document_url",
        note="With no action attribute the submission URL is the document's own, and a"
             " declared one resolves with its fragment dropped.",
        html=page(body='<form id=f action="/go#frag"></form>'),
        script="""
            (() => {
              const form = document.getElementById('f');
              const declared = [form.action, form.getAttribute('action')];
              form.removeAttribute('action');
              return [declared, form.action, form.getAttribute('action')];
            })()
        """,
        expected=[["http://repro.test/go#frag", "/go#frag"],
                  "http://repro.test/dir/page", None],
    ),
    Repro(
        name="a_scripts_inner_text_is_its_source",
        html=page(body="<script>window.__seen = 1;</script>"),
        script="document.querySelector('script').innerText",
        expected="window.__seen = 1;",
    ),
    Repro(
        name="screen_avail_left_and_top_and_do_not_track",
        html=page(body=""),
        script="[screen.availLeft, screen.availTop, navigator.doNotTrack]",
        expected=[0, 0, None],
    ),
    Repro(
        name="an_event_target_prototype_has_no_handler_attributes",
        note="Handler attributes belong to the interfaces that own them, so they do"
             " not show up on EventTarget, which a browser gives three methods.",
        html=page(body=""),
        script="Object.getOwnPropertyNames(EventTarget.prototype)"
               ".filter(name => name.startsWith('on'))",
        expected=[],
    ),
    Repro(
        name="a_window_handler_slot_starts_empty",
        note="A handler attribute is null until a page sets one, and holds what it set.",
        html=page(body=""),
        script="""
            (() => {
              const before = window.ononline;
              const handler = () => {};
              window.ononline = handler;
              const held = window.ononline === handler;
              window.ononline = null;
              return [before === null, held, window.ononline === null,
                      typeof window.onresize];
            })()
        """,
        expected=[True, True, True, "object"],
    ),
    Repro(
        name="a_style_added_by_script_applies",
        note="Rules a page builds at runtime are read from the tree, so the cached "
             "sheet has to be dropped when a style element arrives.",
        html=page(body="<div id=t>t</div>"),
        script="""
            (() => {
              const style = document.createElement('style');
              style.textContent = '#t { color: rgb(4, 5, 6); }';
              document.body.appendChild(style);
              return getComputedStyle(document.getElementById('t')).color;
            })()
        """,
        expected="rgb(4, 5, 6)",
    ),
    Repro(
        name="an_undeclared_colour_is_the_initial_one",
        note="Nothing declared anywhere: the computed colour is the browser's initial "
             "black, not an empty value.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const element = document.createElement('div');
              document.body.appendChild(element);
              return [getComputedStyle(element).color, element.style.color];
            })()
        """,
        expected=["rgb(0, 0, 0)", ""],
    ),
    Repro(
        name="every_tag_starts_from_its_own_display",
        note="The initial display is picked per tag: a span is inline where a div is"
             " block, head content is out of the flow, a table part says which part it"
             " is, and a tag this DOM does not model is inline like a custom element.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const tags = ['span', 'div', 'img', 'input', 'select', 'button', 'head',
                            'style', 'template', 'table', 'tr', 'td', 'x-thing'];
              const out = {};
              for (const tag of tags) {
                const element = document.createElement(tag);
                document.body.appendChild(element);
                out[tag] = getComputedStyle(element).display;
              }
              return [out.span, out.div, out.img, out.input, out.select, out.button,
                      out.head, out.style, out.template, out.table, out.tr, out.td,
                      out['x-thing']];
            })()
        """,
        expected=["inline", "block", "inline", "inline-block", "inline-block",
                  "inline-block", "none", "none", "none", "table", "table-row",
                  "table-cell", "inline"],
    ),
    Repro(
        name="an_undeclared_property_reports_its_initial_value",
        note="Excluded: only the properties the cascade starts from have an initial "
             "value, so a supported property nobody declared computes to nothing.",
        html=EMPTY_PAGE,
        script="""
            (() => {
              const element = document.createElement('div');
              document.body.appendChild(element);
              return getComputedStyle(element).marginTop;
            })()
        """,
        expected="",
        known_divergence="margin-top computes to '' with nothing declared; Chrome"
        " reports its initial '0px'",
    ),
)
