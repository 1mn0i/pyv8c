"""What the widget reports in ``/eb/`` -- the reported value, caught before it is encoded.

    python script/eb_witness.py --seconds 100 --click 640,430 960,540 1010,545 --after 20

The two-sided table is what makes this the target: Chrome sends **no** ``/eb/`` at all, and we
send five, right after the token request.  The report's body leaves the realm encoded, so the
readable value is the one the reporter hands to ``JSON.stringify`` -- an ``Error``, serialized
with ``Object.getOwnPropertyNames`` as the replacer (both bundles' report functions read exactly
that way, ``script/cf_frame/*.js``).

One bootstrap per realm: the door is ``pyv8.Context.exec_js``, which every page script goes
through, and the channel back is ``context.glob`` -- a realm busy in the challenge's own loops
cannot answer ``evaluate`` (60 s, then ``RealmBusyError``), so findings are pushed, not pulled.
Read-only: the original ``JSON.stringify`` still runs, unchanged.
"""
import argparse
import collections
import json
import os
import pathlib
import sys
import threading
import time

URL = "https://steamdb.info/login/"

#: ``LEFT`` is replaced by how many reports to capture.  The filter is name-independent: either
#: the value is an ``Error``, or the caller passed a names array as the replacer -- which is how
#: both bundles serialize an error.
BOOTSTRAP = r"""
(function () {
  var root = typeof globalThis === 'object' && globalThis ? globalThis : this;
  if (!root || root.__ebWitness) { return 'already'; }
  root.__ebWitness = { left: LEFT, seen: 0 };
  function send(payload) {
    try { if (typeof __eb_report === 'function') { __eb_report(JSON.stringify(payload)); } }
    catch (error) {}
  }
  var stringify = JSON.stringify;
  JSON.stringify = function (value, replacer, space) {
    try {
      var witness = root.__ebWitness;
      var isError = false;
      try { isError = value instanceof Error; } catch (error) {}
      if (witness.left > 0 && value && typeof value === 'object'
          && (isError || Array.isArray(replacer))) {
        witness.left -= 1;
        witness.seen += 1;
        var own = [];
        try {
          own = Object.getOwnPropertyNames(value).map(function (key) {
            var item = '?';
            try { item = String(value[key]); } catch (error) { item = '<throws>'; }
            return key + '=' + item.slice(0, 200);
          });
        } catch (error) {}
        send({
          kind: 'report',
          seen: witness.seen,
          tag: String(Object.prototype.toString.call(value)),
          isError: isError,
          name: String(value.name || ''),
          message: String(value.message || '').slice(0, 400),
          replacer: Array.isArray(replacer) ? String(replacer.join(',')).slice(0, 160) : '',
          own: own,
          stack: String(value.stack || '(no stack)').slice(0, 1400),
          where: String((new Error()).stack || '').slice(0, 800),
          url: String(root.location ? root.location.href : '?').slice(0, 110)
        });
      }
    } catch (error) {}
    return stringify.apply(JSON, arguments);
  };
  return 'hooked';
})()
"""

#: Run before a realm's every script until it takes: the first script of a fresh realm arrives
#: before ``Node`` exists at all, so a one-shot install misses every frame that matters.
DOM_HOOK = r"""
(function () {
  var root = typeof globalThis === 'object' && globalThis ? globalThis : this;
  var proto = (root && root.Node && root.Node.prototype) || null;
  if (!proto) { return 'late'; }
  if (proto.__ebHooked) { return 'ok'; }
  function kind(value) {
    try {
      if (value === undefined) { return 'undefined'; }
      if (value === null) { return 'null'; }
      if (typeof value !== 'object') { return typeof value + ':' + String(value).slice(0, 40); }
      var tag = Object.prototype.toString.call(value);
      var ctor = '';
      try { ctor = String(value.constructor && value.constructor.name); } catch (error) {}
      var extra = '';
      try { extra = ' nodeType=' + String(value.nodeType) + ' nodeName=' + String(value.nodeName); }
      catch (error) {}
      return tag + '/' + ctor + extra;
    } catch (error) { return '<unreadable>'; }
  }
  //: A refusal here is what the widget reports in /eb/: wrap the mutators and describe the
  //: argument our binding would not take (the original still runs, so behaviour is unchanged).
  ['replaceChild', 'insertBefore', 'appendChild', 'removeChild'].forEach(function (method) {
    var original = proto[method];
    if (typeof original !== 'function') { return; }
    proto[method] = function (first, second) {
      try { return original.apply(this, arguments); }
      catch (error) {
        try {
          if (typeof __eb_report === 'function') {
            __eb_report(JSON.stringify({
              kind: 'refusal',
              method: method,
              parent: String((this && this.nodeName) || '?'),
              first: kind(first),
              second: kind(second),
              error: String(error && error.name) + ': ' + String(error && error.message).slice(0, 220),
              where: String((new Error()).stack || '').slice(0, 700)
            }));
          }
        } catch (inner) {}
        throw error;
      }
    };
  });
  proto.__ebHooked = true;
  return 'ok';
})()
"""

REPORTS = []

#: The last reads the engine answered ``undefined``, from the engine's own trace callback,
#: kept short on purpose: a report names a failing call, and its upstream is one of the reads
#: immediately before it ("answered undefined, then called" is the challenge's usual chain).
RECENT = collections.deque(maxlen=60)


TRACING = {"on": False}


def trace_callback(event, receiver, name, arguments, value, error, is_construct, ctx_type):
    """Keep only the reads that answered ``undefined`` -- the trace is a ring, not a log."""
    import pyv8

    if str(event).startswith("get") and value is pyv8.Undefined:
        RECENT.append(f"{ctx_type} get {name}")
    return None


def install(left):
    """Plant the callback, then run the bootstrap in each realm before that realm's script."""
    import pyv8

    original = pyv8.Context.exec_js
    done = set()
    dom_ready = set()

    def told(text):
        try:
            payload = json.loads(str(text))
        except BaseException:  # noqa: BLE001
            payload = {"kind": "unreadable", "text": str(text)[:400]}
        REPORTS.append(payload)
        print("=== " + ascii_only(json.dumps(payload))[:1900], flush=True)
        if payload.get("kind") == "report":
            print("    recent undefined reads: "
                  + ascii_only(" | ".join(list(RECENT)[-24:])), flush=True)

    def wrapped(self, source, *args, **kwargs):
        key = id(self)
        if key not in done:
            try:
                self.glob["__eb_report"] = told
            except BaseException as error:  # noqa: BLE001
                print(f"! no channel in this realm: {type(error).__name__}: {str(error)[:90]}")
            try:
                original(self, BOOTSTRAP.replace("LEFT", str(left)), filename="eb-bootstrap.js")
                done.add(key)
            except BaseException as error:  # noqa: BLE001 - retried on the next script
                print(f"! bootstrap deferred: {type(error).__name__}: {str(error)[:90]}")
        if key in done and key not in dom_ready:
            #: ``Node`` does not exist when a realm's first script arrives, so this is retried
            #: until a realm answers 'ok' (then never again).
            try:
                if original(self, DOM_HOOK, filename="eb-dom-hook.js") == "ok":
                    dom_ready.add(key)
                    print(f"    dom hook installed in realm {len(dom_ready)}", flush=True)
            except BaseException:  # noqa: BLE001 - retried on the next script
                pass
        return original(self, source, *args, **kwargs)

    pyv8.Context.exec_js = wrapped


def install_dump(directory, most):
    """Keep each realm's scripts, so a stack column from *this* build can be read afterwards.

    The bundles carry a new build id (and new columns) every round, so the only usable dump is
    the one taken while the /eb/ report was produced.
    """
    from pyv8c.platform.scripting import ScriptController

    original = ScriptController.evaluate
    state = {"count": 0}
    directory.mkdir(parents=True, exist_ok=True)

    def wrapped(self, source, *args, **kwargs):
        filename = kwargs.get("filename")
        if state["count"] < most:
            text = source if isinstance(source, str) else str(source)
            if len(text) < 2_000_000:
                state["count"] += 1
                tail = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(filename or "inline"))[-40:]
                path = directory / f"{state['count']:02d}-{tail}.js"
                path.write_text(text, encoding="utf-8")
                print(f"    dumped {path.name} {len(text)}B", flush=True)
        return original(self, source, filename=filename)

    ScriptController.evaluate = wrapped


def ascii_only(text):
    """This console is GBK, and a report with a stray character in it would end the run."""
    return "".join(c if c.isascii() and c.isprintable() else "." for c in text)


def turn(page, seconds, step=0.2):
    """Turn the queue for *seconds* on a worker: a busy realm can hold one call for a minute."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        time.sleep(step)


def pump(page, seconds, step=0.2, grace=1.0):
    if seconds <= 0:
        return
    worker = threading.Thread(target=turn, args=(page, seconds, step), daemon=True)
    worker.start()
    worker.join(seconds + grace)


def gesture(page, points, seed_base=1, wait=60.0):
    """The same posted gesture ``5s.py`` sends: the widget answers a hand, not a poll."""
    done = threading.Event()
    outcome = {}

    def run():
        try:
            for index, (x, y) in enumerate(points):
                page.mouse.move(x, y, curve="human", steps=8, duration=8 / 60, seed=seed_base + index)
                page.run_until_idle(timeout=0)
            page.mouse.click(delay=0.07)
            page.run_until_idle(timeout=0)
            outcome["ok"] = True
        except BaseException as error:  # noqa: BLE001
            outcome["error"] = f"{type(error).__name__}: {str(error)[:80]}"
        finally:
            done.set()

    if not page.realm.executor.post(run):
        print("gesture refused: the realm is closed")
        return False
    if not done.wait(wait):
        print(f"gesture still queued after {wait:g}s")
        return False
    if "error" in outcome:
        print(f"gesture failed: {outcome['error']}")
        return False
    return True


def browse(seconds, points, after, left, out, dump=None):
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    if dump:
        install_dump(dump, 20)
    install(left)
    started = time.time()
    browser = Browser(stub_logging="off", threaded_loaders=True, trace=TRACING["on"],
                      traceable=TRACING["on"],
                      trace_callback=trace_callback if TRACING["on"] else None)
    try:
        page = browser.new_context().new_page()
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001
            print(f"goto: {type(error).__name__}: {str(error)[:110]}")
        if points:
            pump(page, max(0.0, after - (time.time() - started)))
            print(f"gesture at {time.time() - started:.0f}s: "
                  + " ".join(f"{x},{y}" for x, y in points), flush=True)
            gesture(page, points)
            pump(page, max(0.0, seconds - after))
        else:
            pump(page, seconds)
        print(f"=== {len(REPORTS)} report(s) in {time.time() - started:.0f}s")
        sys.stdout.flush()
    finally:
        if out:
            out.write_text(json.dumps(REPORTS, ensure_ascii=True, indent=1), encoding="utf-8")
            print(f"-> {out}")
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        #: A realm wedged in its own loop is left to the OS rather than waited on.
        os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=100.0)
    parser.add_argument("--after", type=float, default=20.0)
    parser.add_argument("--click", nargs="+", default=[], help="x,y points, in order")
    parser.add_argument("--reports", type=int, default=12)
    parser.add_argument("--dump", type=pathlib.Path, default=None,
                        help="also keep this build's scripts here, for reading a stack column")
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path(os.environ.get("TEMP", ".")) / "eb_reports.json")
    parser.add_argument("--trace", action="store_true",
                        help="keep the last undefined-answered reads, to pair with a report")
    arguments = parser.parse_args(argv)
    TRACING["on"] = arguments.trace
    points = [tuple(int(part) for part in item.split(",")) for item in arguments.click]
    browse(arguments.seconds, points, arguments.after, arguments.reports, arguments.out,
           arguments.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
