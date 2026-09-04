"""Script execution, inline handlers and JS helpers for a Page."""

import re
from collections import deque
from urllib.parse import urljoin

import pyv8

from ..foundation.errors import InvalidStateError, as_js_error
from ..foundation.tasks import TaskSource


class ScriptController:
    """Own script-queue state and execution; runs on the owning Page thread."""

    # JavaScript helpers compiled once per realm; see ScriptController._js_scripts.
    _JS_HELPER_SOURCES = {
        "make_promise": (
            "(() => { let resolve, reject;"
            " const promise = new Promise((yes, no) => { resolve = yes; reject = no; });"
            " return { promise, resolve, reject }; })()"
        ),
        "error_factory": "(name, message) => new (globalThis[name] || Error)(message)",
        "dom_error_factory": "(message, name) => new DOMException(message, name)",
        "promise_resolve": "(value) => Promise.resolve(value)",
    }

    def __init__(self, page):
        self.page = page
        self.dom_blocking_scripts = 0
        self.queue = deque()
        self.defer = deque()
        self.module_cache = {}
        self._js_script_cache = None

    def reset(self):
        self.dom_blocking_scripts = 0
        self.queue.clear()
        self.defer.clear()
        self.module_cache.clear()

    def clear_js_helpers(self):
        """Drop cached scripts when the V8 context goes away."""
        self._js_script_cache = None

    # -- realm JS helpers ----------------------------------------------

    @property
    def _js_scripts(self):
        """Compile the realm's JavaScript helpers exactly once.

        ``exec_js`` builds a fresh ``Script`` for every source string, and that
        construction costs several times more than evaluating an already
        compiled one. Fetch, XHR and every rejected Promise go through these
        helpers, so they are compiled once per realm and re-evaluated after.
        """
        scripts = self._js_script_cache
        if scripts is None:
            scripts = {
                key: pyv8.Script(self.page.context, source)
                for key, source in self._JS_HELPER_SOURCES.items()
            }
            self._js_script_cache = scripts
        return scripts

    def make_promise(self):
        record = self.page.context.exec_js(self._js_scripts["make_promise"])
        return record["promise"], record["resolve"], record["reject"]

    def js_error_value(self, exc):
        """Materialize a rejected Promise reason as a native JS error object."""
        error = as_js_error(exc)
        name = getattr(error, "_name", None)
        if not name:
            name = {
                "V8TypeError": "TypeError", "V8RangeError": "RangeError",
                "V8ReferenceError": "ReferenceError", "V8SyntaxError": "SyntaxError",
                "V8URIError": "URIError", "V8EvalError": "EvalError",
            }.get(type(error).__name__, "Error")
        message = str(error)
        scripts = self._js_scripts
        # DOMException's name is stored on the Python instance, while native
        # Error subclasses use the constructor name.  Keep DOMException
        # construction separate so its name/code remain observable.
        if hasattr(error, "_name"):
            factory = self.page.context.exec_js(scripts["dom_error_factory"])
            return factory.call(None, message, name)
        factory = self.page.context.exec_js(scripts["error_factory"])
        return factory.call(None, name, message)

    def promise_resolve(self, value):
        factory = self.page.context.exec_js(self._js_scripts["promise_resolve"])
        return factory.call(None, value)

    # -- script execution ----------------------------------------------

    def evaluate(self, script, filename=None):
        if self.page.closed or self.page.context_owner.closed:
            raise InvalidStateError("Page is closed")
        target = getattr(self.page, "_devtools_target", None)
        if target is not None:
            target.wait_for_startup()
        return self.page.context.exec_js(str(script), filename=filename)

    def execute_script(self, node, source, filename=None):
        try:
            if str(getattr(node, "get_type", lambda: "")() or "").lower() == "module":
                self.execute_module(source, filename or self.page.url, set())
            else:
                self.evaluate(source, filename=filename or self.page.url)
            return True
        except BaseException as exc:
            self.page.lifecycle.dispatch_script_error(node, exc)
            return False

    def execute_module(self, source, filename, trail):
        """Execute static module imports in dependency-first order.

        V8's public extension currently exposes classic script execution only.
        This adapter implements the useful static-import subset while keeping
        all module work on the Page/V8 thread.
        """
        key = str(filename)
        if key in self.module_cache:
            return
        if key in trail:
            return  # cyclic static imports are already evaluating
        trail = set(trail)
        trail.add(key)
        imports = re.findall(r"(^|;)\s*import\s+([\s\S]*?)\s+from\s+(['\"])([^'\"]+)\3\s*;?", source)
        side_effects = re.findall(r"(?:^|;)\s*import\s*(['\"])([^'\"]+)\1\s*;?", source)
        for _prefix, clause, _quote, specifier in imports:
            dep_url = urljoin(key, specifier)
            dep = self.page.resources.load_resource(dep_url, {"method": "GET", "mode": "cors"})
            self.execute_module(dep.body_text(), dep.url or dep_url, trail)
        for _quote, specifier in side_effects:
            dep_url = urljoin(key, specifier)
            dep = self.page.resources.load_resource(dep_url, {"method": "GET", "mode": "cors"})
            self.execute_module(dep.body_text(), dep.url or dep_url, trail)
        transformed = re.sub(r"(?:^|;)\s*import\s+([\s\S]*?)\s+from\s+(['\"])([^'\"]+)\2\s*;?", "", source)
        transformed = re.sub(r"(?:^|;)\s*import\s*(['\"])([^'\"]+)\1\s*;?", "", transformed)
        transformed = re.sub(r"\bexport\s+default\s+", "", transformed)
        transformed = re.sub(r"\bexport\s+(?=(?:async\s+)?(?:function|class|const|let|var)\b)", "", transformed)
        transformed = re.sub(r"\bexport\s*\{[^}]*\}\s*;?", "", transformed)
        # Imported names are global lexical bindings after dependency
        # evaluation.  Alias forms need a local declaration in this script.
        for _prefix, clause, _quote, _specifier in imports:
            for item in clause.strip().strip("{}").split(","):
                item = item.strip()
                if not item or item == "*":
                    continue
                if item.startswith("*"):
                    continue
                parts = re.split(r"\s+as\s+", item)
                if len(parts) == 2:
                    transformed = "const %s = %s;\n%s" % (parts[1].strip(), parts[0].strip(), transformed)
        self.evaluate(transformed, filename=key)
        self.module_cache[key] = True

    # -- parser / DOM script coordination ------------------------------

    def node_adopted(self, node):
        if getattr(node, "localName", "") == "script":
            self.dynamic_script_updated(node)

    def dynamic_script_updated(self, node):
        page = self.page
        if page.closed or getattr(node, "_resource_started", False) and getattr(node, "_dynamic_started", False):
            return
        # Setting src/text on a detached script does not fetch or execute it.
        # The adoption callback will retry once it is connected to this
        # document.
        parent = getattr(node, "parentNode", None)
        connected = False
        while parent is not None:
            if parent is page.document:
                connected = True
                break
            parent = getattr(parent, "parentNode", None)
        if not connected:
            return
        node._dynamic_started = True
        node._parser_script = False
        node._script_defer = False
        node._resource_started = False
        if node.get_src():
            # Dynamically inserted classic scripts are async by default. An
            # explicit async=false script is kept in insertion order.
            node._resource_async = bool(node.get_async())
            if not node._resource_async:
                self.dom_blocking_scripts += 1
                self.queue.append(node)
            page.resources.track_document_resource(
                node, node.get_src(), {"method": "GET", "mode": "same-origin"}, "script"
            )
        elif getattr(node, "_raw_text", ""):
            page.tasks.post(lambda n=node: self.execute_script(n, n._raw_text, page.url))

    def install_inline_handler(self, node, name, source):
        try:
            fn = self.page.context.exec_js("(function(event){" + str(source) + "})")
            node._handlers[str(name).lower()] = fn
        except BaseException as exc:
            self.page.lifecycle.dispatch_script_error(node, exc)

    def drain_script_queue(self):
        page = self.page
        while self.queue and getattr(self.queue[0], "_resource_done", False):
            node = self.queue.popleft()
            response, error = node._resource_response, node._resource_error
            if error:
                page.lifecycle.dispatch_script_error(node, error)
            elif response is None:
                self.execute_script(node, getattr(node, "_inline_source", ""), page.url)
            else:
                self.execute_script(node, response.body_text(), response.url or page.url)
                page.lifecycle.dispatch_page_event("load", node, {"bubbles": False})
            self.dom_blocking_scripts = max(0, self.dom_blocking_scripts - 1)
        if not self.dom_blocking_scripts and page.navigation.parser is not None and not page.navigation.parser.done:
            page.tasks.post(
                lambda generation=page.navigation.generation: page.navigation.resume_parser(generation),
                TaskSource.PARSER,
            )
        if page.navigation.parser_done:
            self.run_deferred_scripts()

    def run_deferred_scripts(self):
        page = self.page
        if page.closed or not page.navigation.parser_done:
            return
        while self.defer:
            node = self.defer[0]
            if not getattr(node, "_resource_done", False):
                return
            self.defer.popleft()
            response, error = node._resource_response, node._resource_error
            if error:
                page.lifecycle.dispatch_script_error(node, error)
            else:
                source = getattr(node, "_inline_source", "")
                if response is not None:
                    source = response.body_text()
                self.execute_script(node, source, (response.url if response is not None else page.url) or page.url)
                if response is not None:
                    page.lifecycle.dispatch_page_event("load", node, {"bubbles": False})
        page.lifecycle.maybe_fire_dom_content_loaded()
