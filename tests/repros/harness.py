"""Run one case here, and in the Chrome already installed on this machine.

The reference is named by path, not fetched: nothing is downloaded, and which browser
answered is part of every divergence report.  Point ``PYV8C_CHROME`` at another
``chrome.exe`` to compare against a different one.

Both sides run the same page and evaluate the same script once.  The reference
fulfils the document's URL itself, so the page has the origin ``corpus.URL`` says it
has -- cookie and URL behaviour need a real origin, not ``about:blank``.  What comes
back is a value or the name of a thrown error: an error *message* is written by the
engine for a human, so only the class is compared.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
from pathlib import Path
from typing import Any

from corpus import Repro

REFERENCE_ENV = "PYV8C_CHROME"

CHROME_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
)

#: pyv8 spells an error ``V8TypeError``, Chrome writes ``TypeError: boom``; the class
#: is the part the two can be held to.
ERROR_NAME = re.compile(r"(?:V8)?([A-Z][A-Za-z]*Error)")


class ReferenceUnavailable(RuntimeError):
    """The reference browser cannot be run, with the reason to report."""


@dataclasses.dataclass(frozen=True)
class Observation:
    """What a browser did with a case: a value, or the error it raised.

    ``detail`` carries the engine's own wording of an error for the report only -- it
    is written for a human, so it is never compared.
    """

    value: Any = None
    error: str = ""
    detail: str = dataclasses.field(default="", compare=False)

    def __str__(self):
        if self.error:
            first_line = self.detail.splitlines()[0] if self.detail else ""
            return f"{self.error}: {first_line}" if first_line else self.error
        return json.dumps(self.value, sort_keys=True, default=repr)


def error_name(text, error=None):
    """The class of a reported error: named in the prose, or carried by the error.

    Playwright names the class in the message, pyv8's DOMException keeps it in
    ``name`` only, so the prose is read first and the error itself after that.
    """
    names = ERROR_NAME.findall(text or "")
    if names:
        return names[-1]
    for name in (getattr(error, "name", None), getattr(error, "_name", None)):
        if isinstance(name, str) and name:
            return name
    return ""


def normalize(value):
    """A value the two engines can be held to the same record by.

    The reference side hands back what it can serialize, and there a JavaScript
    ``null`` and an ``undefined`` are both simply absent.  pyv8's two singletons mean
    the same thing at that boundary, so they read as ``None`` here as well: a case
    that needs to tell them apart asks for ``typeof``, which is a string.
    """
    import pyv8

    if value is pyv8.Null or value is pyv8.Undefined:
        return None
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def chrome_executable():
    """``(path, "")`` for the Chrome to compare against, or ``(None, reason)``."""
    override = os.environ.get(REFERENCE_ENV)
    if override:
        path = Path(override)
        if path.exists():
            return path, ""
        return None, f"{REFERENCE_ENV}={override} does not exist"

    for candidate in CHROME_PATHS:
        path = Path(os.path.expandvars(candidate))
        if path.exists():
            return path, ""
    return None, (
        f"no Chrome found in {', '.join(CHROME_PATHS)}; set {REFERENCE_ENV} to the"
        f" chrome.exe to compare against"
    )


def observe_here(case: Repro) -> Observation:
    """What this environment observes for a case."""
    from pyv8c import Browser

    browser = Browser(stub_logging="off")
    try:
        page = browser.new_context().new_page()
        page.set_content(case.html, url=case.url)
        page.run_until_idle(timeout=1)
        try:
            return Observation(value=normalize(page.evaluate(case.script)))
        except BaseException as error:  # a script that throws is an observation too
            return Observation(
                error=error_name(str(error), error) or type(error).__name__, detail=str(error)
            )
    finally:
        browser.close()


class ChromeReference:
    """A running local Chrome, reused for every case in a run."""

    def __init__(self, browser, executable, version):
        self._browser = browser
        self.executable = executable
        self.version = version

    def observe(self, case: Repro) -> Observation:
        page = self._browser.new_page()
        try:
            page.route(
                "**/*",
                lambda route: route.fulfill(
                    status=200, content_type="text/html", body=case.html
                ),
            )
            page.goto(case.url)
            try:
                return Observation(value=normalize(page.evaluate(case.script)))
            except BaseException as error:
                return Observation(
                    error=error_name(str(error), error) or type(error).__name__, detail=str(error)
                )
        finally:
            page.close()


@contextlib.contextmanager
def chrome_reference():
    """The locally installed Chrome, or :class:`ReferenceUnavailable` saying why not."""
    executable, reason = chrome_executable()
    if executable is None:
        raise ReferenceUnavailable(reason)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise ReferenceUnavailable(
            f"playwright is not installed ({error}); pip install playwright"
        ) from error

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        except Exception as error:
            raise ReferenceUnavailable(f"cannot start {executable}: {error}") from error
        try:
            yield ChromeReference(browser, executable, browser.version)
        finally:
            browser.close()


def divergence(here: Observation, there: Observation, browser: str = "") -> str:
    """How two observations differ, or ``""`` when they agree."""
    if here == there:
        return ""
    where = f"Chrome {browser}" if browser else "the reference browser"
    return f"pyv8c observed {here}; {where} observed {there}"
