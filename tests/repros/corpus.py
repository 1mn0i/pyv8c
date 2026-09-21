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
)
