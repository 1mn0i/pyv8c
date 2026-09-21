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
        name="a_select_reports_its_own_value_and_index",
        html=page(body="<select><option value=a>A</option></select>"),
        script="""
            (() => {
              const select = document.querySelector('select');
              select.value = 'a';
              return [typeof select.selectedIndex, select.value];
            })()
        """,
        expected=["undefined", "a"],
        known_divergence="HTMLSelectElement.selectedIndex is not implemented, so it is"
        " undefined here; Chrome reports the index as a number",
    ),
    Repro(
        name="a_select_exposes_its_options_collection",
        script="typeof document.querySelector('select').options",
        html=page(body="<select><option value=a>A</option></select>"),
        expected="undefined",
        known_divergence="HTMLOptionsCollection is not implemented, so .options is undefined"
        " here; Chrome exposes the collection",
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
        name="a_button_keeps_its_name",
        script="typeof document.createElement('button').name",
        expected="undefined",
        known_divergence="HTMLButtonElement.name is not implemented, so a button's name is"
        " undefined here; Chrome reflects the name attribute as a string",
    ),
    Repro(
        name="a_form_exposes_its_elements_collection",
        script="typeof document.forms[0].elements",
        html=page(body="<form><input name=a></form>"),
        expected="undefined",
        known_divergence="HTMLFormControlsCollection is not implemented, so .elements is"
        " undefined here; Chrome exposes the collection",
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
        expected="undefined",
        known_divergence="DOMStringMap is not implemented, so .dataset is undefined here;"
        " Chrome exposes one",
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
