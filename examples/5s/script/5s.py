"""SteamDB under pyv8c, judged only by the cookie we are after.

The judging run -- the one that reports the exchange table, the jar and the verdict.  Its
one-line counterpart is ``examples/5s/5s.py``; this is the tool that says *where* a run was
lost, and both write the same ``5s.log`` beside the case.

    python script/5s.py                # quiet run: exchange table, jar, verdict
    python script/5s.py --seconds 45   # let the challenge live longer
    python script/5s.py --trace        # engine trace and verbose stubs -> 5s.log
    python script/5s.py --trace-after 100 --seconds 150   # trace only the late phase
    python script/5s.py --timeout 150  # hard kill for the child when a task wedges
    python script/5s.py --no-watch     # without the transport witness

**Verdict**: the context's cookie jar holds ``cf_clearance`` for ``steamdb.info`` and the page
is SteamDB itself rather than the interstitial.

The run reports three witnesses and nothing else, because these are the three a challenge can
be argued from:

* **exchange table** -- every request the transport made, its status, its size, and the
  ``Set-Cookie`` names it carried.  ``cf_clearance`` *is* a ``Set-Cookie``, so a run that never
  looks at one cannot say where it was lost;
* **cookie jar** -- what we kept, with domains;
* **page state** -- where we ended, and what the document says.

The page's challenge runs long tasks of its own, so ``run_until_idle`` cannot time it out: the
work ends when the script returns.  The real run therefore happens in a child process with a
hard kill -- a wedged realm must not wedge the analysis.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

#: The page under challenge.  ``/login/`` since 2026-10-10: it is the door the case is after, and
#: the root ends up in the same place anyway.
URL = "https://steamdb.info/login/"
#: The log stays beside the case, not beside the tools: the simple ``../5s.py`` writes the same
#: file, and the README's judge commands read it from there.
LOG = Path(__file__).resolve().parent.parent / "5s.log"

#: Every exchange of this run: ``(method, url, status, bytes, cookie names, body snippet)``.
EXCHANGES = []

#: Every ``Set-Cookie`` the jar was handed: ``(url, header, names kept)``.
JAR_EVENTS = []

#: What the page says about itself, including the challenge's own bookkeeping.
SNAPSHOT = r"""
(function () {
  var opt = window._cf_chl_opt || {};
  function frame_text(frame) {
    try {
      var doc = frame.contentDocument;
      if (!doc) { return frame.getAttribute('sandbox') ? 'cross-origin' : 'no document'; }
      return String((doc.body && doc.body.innerText) || doc.documentElement.textContent || '')
              .replace(/\s+/g, ' ').trim().slice(0, 120);
    } catch (error) { return 'unreadable: ' + error.name; }
  }
  return {
    url: location.href.slice(0, 120),
    title: String(document.title || '').slice(0, 80),
    body: String((document.body && document.body.innerText) || '')
            .replace(/\s+/g, ' ').trim().slice(0, 120),
    frames: [].map.call(document.querySelectorAll('iframe'), function (f) {
      return { sandbox: String(f.getAttribute('sandbox') || '-').slice(0, 40),
               src: String(f.src || '').slice(0, 40),
               text: frame_text(f) }; }),
    scripts: document.querySelectorAll('script').length,
    chl_opt: Object.keys(opt).slice(0, 14),
    turnstile: window.turnstile ? Object.keys(window.turnstile).slice(0, 8) : null,
    widget: (function () {
      var node = document.querySelector('[class*=cf-], #cf-chl-widget, [id*=turnstile]');
      if (!node) { return null; }
      var attrs = {};
      [].forEach.call(node.attributes || [], function (a) { attrs[a.name] = String(a.value).slice(0, 60); });
      return { tag: node.tagName, id: node.id, cls: String(node.className).slice(0, 60),
               text: String(node.innerText || node.textContent || '')
                       .replace(/\s+/g, ' ').trim().slice(0, 300),
               attrs: attrs };
    })(),
    error: (function () {
      var node = document.querySelector('.cf-error');
      return node ? String(node.innerText || node.textContent || '')
                      .replace(/\s+/g, ' ').trim().slice(0, 400) : null;
    })()
  };
})()
"""


def _text(value):
    """A request body as text, whatever shape the caller built it in."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def watch_calls():
    """Print the traceback of the engine calls the widget catches and reports.

    The trace names the exception type only (``, error: <AttributeError>``), and an
    ``AttributeError`` is a Python defect rather than a DOM error: something in our own stack
    is missing.  Wrapping the two callbacks the widget uses for its "environment check" turns
    that into file and line.  Read-only: the original still runs, and still raises.
    """
    import traceback

    from pyv8c.web.bom.navigator import GPU
    from pyv8c.web.bom.webrtc import RTCPeerConnection

    def watched(owner, name, label):
        original = getattr(owner, name)

        def wrapper(self, *args, **kwargs):
            try:
                return original(self, *args, **kwargs)
            except BaseException:
                print(f"! {label} raised:")
                for entry in traceback.format_exc().splitlines()[-6:]:
                    print(f"      {entry.strip()[:150]}")
                raise

        setattr(owner, name, wrapper)

    watched(GPU, "fn_requestAdapter", "navigator.gpu.requestAdapter()")
    watched(RTCPeerConnection, "fn_createOffer", "RTCPeerConnection.createOffer()")


def watch_transport():
    """Record every exchange the network transport makes.

    This is the only place a ``Set-Cookie`` becomes visible: the jar stores what it is handed,
    so "the server never sent it" and "we dropped it" have to be told apart *here*, before the
    jar either way.  Read-only: the wrapped call is the original one.
    """
    from pyv8c.network.curl_cffi import CurlCffiTransport
    from pyv8c.network.cookies import split_set_cookie

    original = CurlCffiTransport._call

    def wrapped(self, request):
        started = time.perf_counter()
        #: What the jar held going *in*: a granted cookie that goes missing is either not sent
        #: here or deleted by this response, and the two need telling apart.
        before = _jar_names(self.cookies)
        response = original(self, request)
        header = str(response.headers.get("set-cookie") or "")
        names = ", ".join(sorted({part.split("=")[0].strip() for part in split_set_cookie(header)
                                  if "=" in part})) if header else ""
        body = str(getattr(response, "body", "") or "")
        host = str(getattr(request, "url", ""))
        sent = _text(getattr(request, "body", None))
        #: The sent body is what the widget *says*: ``eb/…/chl_api_message`` is how it reports
        #: an error to Cloudflare, and Chrome never makes that call -- so its words are the
        #: finding, not the status code.
        snippet = ""
        if "challenges.cloudflare.com" in host or len(body) <= 400:
            snippet = " ".join(body.split())[:150]
        EXCHANGES.append({
            "method": str(request.method).upper(), "url": host,
            "status": getattr(response, "status", "?"), "bytes": len(body),
            #: Which HTTP version this hop used.  The edge decides the *flavour* of the challenge
            #: at this request, TLS is ruled out (three Chrome profiles got the same one), and
            #: the same request through plain curl_cffi came back over HTTP/3.
            "http": str(getattr(response, "http_version", "?")),
            "cookies": names, "ms": int((time.perf_counter() - started) * 1000),
            "body": snippet, "jar": len(getattr(self.cookies, "_cookies", {}) or {}),
            "sent": len(sent), "sent_body": " ".join(sent.split())[:220],
            #: Which cookies the jar holds after this exchange, not just how many: a granted
            #: ``cf_clearance`` that a later response replaces has to be visible as a change.
            "jar_names": _jar_names(self.cookies),
            #: What we sent, not just where: ``pat``/``ci`` answering 400 while Chrome gets
            #: 401/200 says the edge rejected *our request*, and a header is the thing it can
            #: reject.  Values keep enough width to be compared with Chrome's -- a 44-character
            #: cut turned "which URL is the referrer" into a question the dump could not answer.
            "headers": "; ".join(f"{name}={str(value)[:160]}"
                                 for name, value in sorted(
                                     (dict(getattr(request, "headers", {}) or {})).items())),
            #: What came back, all of it: the widget reads ``cf-chl-out``/``cf-chl-out-s`` off a
            #: challenge response and continues only when they carry something, so whether the
            #: answer arrives with those headers is the difference that decides the last step.
            "resp_headers": "; ".join(f"{name}={str(value)[:80]}"
                                      for name, value in sorted(
                                          (dict(getattr(response, "headers", {}) or {})).items())),
            #: The header itself: a granted cookie the jar drops can only be replayed from the
            #: exact bytes the server sent.
            "raw_cookie": header[:220],
            "jar_before": before,
        })
        return response

    CurlCffiTransport._call = wrapped
    if CurlCffiTransport._call is not wrapped:
        #: A witness that quietly fails to install is worse than none: the table came out empty
        #: once because an edit left this assignment after a ``return``.
        raise RuntimeError("the transport witness was not installed")


def _jar_names(jar):
    """The cookie names the jar holds, sorted, for a row of the exchange table."""
    return ", ".join(sorted({name for (_domain, _path, name) in
                             dict(getattr(jar, "_cookies", {}) or {})}))


def watch_executor():
    """Print the traceback of a call the realm's owner thread never picked up.

    ``RealmBusyError`` says a realm did not answer in time, which is all the trace can tell: the
    caller in our own stack and the call that was still holding the thread are what say whether
    the two are waiting for each other.
    """
    import faulthandler
    import traceback

    from pyv8c.foundation.host_errors import RealmBusyError
    from pyv8c.foundation.executor import RealmExecutor

    original = RealmExecutor.call

    def wrapped(self, callback, *args, **kwargs):
        try:
            return original(self, callback, *args, **kwargs)
        except RealmBusyError as error:
            print(f"! realm busy: {error}")
            #: Both ends: the outer frames say who asked, the inner ones where the thread was
            #: held.  Printing only the tail once hid the caller entirely.
            lines = traceback.format_exc().splitlines()
            for entry in lines[:14] + (["      ..."] if len(lines) > 22 else []) + lines[-8:]:
                print(f"      {entry.strip()[:150]}")
            #: And every thread's stack: the realm that never answered is a *different* thread,
            #: so its own stack -- not this one -- says what it is holding.
            print("! thread dump:")
            faulthandler.dump_traceback()
            raise

    RealmExecutor.call = wrapped
    if RealmExecutor.call is not wrapped:
        raise RuntimeError("the executor witness was not installed")


def watch_jar():
    """Record every ``Set-Cookie`` the jar is handed, with what it kept of it.

    The exchange table shows *that* a granted cookie went missing; this shows *why*: the header
    the jar received, whole, next to the names it ended up holding.
    """
    from pyv8c.network.cookies import CookieJar

    original = CookieJar.add_from_response

    def wrapped(self, url, header):
        result = original(self, url, header)
        JAR_EVENTS.append((str(url), str(header), _jar_names(self)))
        return result

    CookieJar.add_from_response = wrapped
    if CookieJar.add_from_response is not wrapped:
        raise RuntimeError("the jar witness was not installed")


def jar_events():
    """Print what the jar was handed, and what it kept."""
    print(f"set-cookie headers ({len(JAR_EVENTS)}):")
    for url, header, names in JAR_EVENTS:
        print(f"    {url[:60]}")
        print(f"      given: {header[:400]}")
        print(f"      kept : [{names}]")


def exchanges_since(start, context):
    """Print the exchanges of this run, oldest first: what was asked and what came back."""
    print(f"exchanges ({len(EXCHANGES) - start}):")
    previous_jar = None
    for entry in EXCHANGES[start:]:
        jar_note = ""
        if entry["jar_names"] != previous_jar:
            jar_note = f" jar=[{entry['jar_before']} -> {entry['jar_names']}]"
            previous_jar = entry["jar_names"]
        print(f"    {entry['method']:4} {entry['url']} -> {entry['status']} "
              f"{entry['bytes']}B {entry['ms']}ms http={entry['http']}"
              + (f" set-cookie: {entry['cookies']}" if entry["cookies"] else "")
              + jar_note)
        #: The header line is what the two tables are compared on: where a request is refused
        #: (400 against Chrome's 401/200), the difference is in here, not in the URL.
        if entry["headers"]:
            print(f"         hdr : {entry['headers']}")
        if entry["resp_headers"]:
            print(f"         back: {entry['resp_headers']}")
        if entry["raw_cookie"]:
            print(f"         raw : {entry['raw_cookie']}")
        if entry["sent"]:
            print(f"         sent {entry['sent']}B: {entry['sent_body']}")
        if entry["body"]:
            print(f"         got : {entry['body'][:150]}")


def cookies_of(context):
    """Every cookie in the jar, as ``(name, domain, path, value)``."""
    jar = getattr(context, "cookies", None)
    stored = []
    for (domain, path, name), cookie in dict(getattr(jar, "_cookies", {}) or {}).items():
        stored.append((name, domain, path, str(cookie.value)[:24]))
    return stored


def make_filtered_trace(names):
    """A trace callback that keeps the interesting events and drops the rest.

    The trace costs ~100x because a challenge reads one global name 670k times; logging every
    access is what makes a traced run never reach the step worth reading.  The callback is ours,
    so the filtering belongs here: an event that raised is always kept, and so is any access
    whose member name was asked for.  Everything else is dropped before it reaches the log.
    """
    from pyv8c.engine.trace import log_trace

    wanted = {name.strip() for name in (names or "").split(",") if name.strip()}

    def callback(event, receiver, name, arguments, value, error, is_construct, ctx_type):
        if error is not None or str(name) in wanted:
            return log_trace(event, receiver, name, arguments, value, error, is_construct,
                             ctx_type)
        return None

    return callback, wanted


def watch_log_trace(names):
    """Filter ``log_trace`` itself, so the drop happens no matter which path reaches it.

    ``trace_callback`` is honoured on every realm this case can see (measured: the callback is
    handed TOP and FRAME events, and a ``None`` return really suppresses the line).  A filtered
    run of the real site still wrote 14 MB in five seconds, and those lines carry no error, so
    they are not this callback's own output -- the provenance is still open.  Until it is
    settled, the same filter also goes on the function every path ends in: the module's own
    ``log_trace`` and the copy ``platform.realms`` imported at startup.
    """
    from pyv8c.engine import trace as trace_module
    from pyv8c.platform import realms

    original = trace_module.log_trace
    wanted = {name.strip() for name in (names or "").split(",") if name.strip()}

    def filtered(event, receiver, name, arguments, value, error, is_construct, ctx_type):
        if error is None and str(name) not in wanted:
            return None
        return original(event, receiver, name, arguments, value, error, is_construct, ctx_type)

    trace_module.log_trace = filtered
    realms.log_trace = filtered


def watch_trace_install(limit=12):
    """Say which trace callback each realm is created with -- and shout when it differs.

    A filter only works where it was handed over, and the handover happens once per realm
    (``platform/realms.py`` assembles the native Context arguments).  A filtered run that still
    wrote a hundred-megabyte log means some realm got a *different* callback, and printing only
    the first few realms hid it: this prints the first one, then every realm that disagrees.
    """
    from pyv8c.platform import realms

    original = realms._context_kwargs
    first = {"callback": None, "count": 0, "printed": 0}

    def wrapped(session, context_type, trace, **kwargs):
        options = original(session, context_type, trace, **kwargs)
        callback = options.get("trace_callback")
        first["count"] += 1
        if first["callback"] is None:
            first["callback"] = callback
            print(f"realm {first['count']}: {context_type} trace={trace} "
                  f"callback={getattr(callback, '__name__', callback)}")
        elif callback is not first["callback"] and first["printed"] < limit:
            first["printed"] += 1
            print(f"realm {first['count']}: {context_type} trace={trace} got a DIFFERENT "
                  f"callback: {getattr(callback, '__name__', callback)}"
                  f" from {getattr(callback, '__module__', '') or '?'}")
        return options

    realms._context_kwargs = wrapped


#: The wait is bounded under the run's own ``--timeout``: a wait longer than it (240s once did)
#: means the harness kills the process before the verdict, and the round reads as nothing.
def gesture(page, points, seed_base=1, wait=60.0):
    """Approach each point along a bowed path, then click -- the widget answers a hand, not a poll.

    ``/login/``'s managed challenge does not clear on its own: Chrome, headed and untouched, sat
    on ``turnstile/f/…`` for 75 s with an empty jar.  ``examples/akm/akm.py`` already has the
    shape -- one position per frame with ``curve="human"``, then a click with a human delay,
    because what the sensor reads is the cursor's path.

    The gesture is **posted onto the page's own queue**, because that is what a browser does with
    input: the realm here is held by the challenge's own loops for minutes, and ``mouse.move``
    marshals onto its owner thread under a 60 s deadline, so sending it from this thread was
    refused outright (measured 2026-10-10: ``realm executor did not complete '_move_to' within
    60.0s``).  On the queue each call runs inline -- it is already the owner thread -- and the
    caller waits with a bound of its own, then says whether it happened.
    """
    done = threading.Event()
    outcome = {}

    def run():
        try:
            for index, (x, y) in enumerate(points):
                page.mouse.move(x, y, curve="human", steps=8, duration=8 / 60,
                                seed=seed_base + index)
                page.run_until_idle(timeout=0)
            page.mouse.click(delay=0.07)
            page.run_until_idle(timeout=0)
            outcome["ok"] = True
        except BaseException as error:  # noqa: BLE001 - a refused gesture is a reading
            outcome["error"] = f"{type(error).__name__}: {str(error)[:80]}"
        finally:
            done.set()

    if not page.realm.executor.post(run):
        print("gesture refused: the realm is closed")
        return False
    if not done.wait(wait):
        print(f"gesture still queued after {wait:g}s (the page never gave the thread back)")
        return False
    if "error" in outcome:
        print(f"gesture failed: {outcome['error']}")
        return False
    return True


def evaluate_bounded(page, expression, seconds=15.0):
    """The page's reading, or why it could not be read in time.

    A realm busy in the challenge's own loop answers nothing for a minute -- and the snapshot is
    only one of the three witnesses, so the verdict must not wait for it.  The old shape spent
    60 s per call and left the run to the hard kill, which is why ``--seconds 60`` used to take
    over two minutes and print no verdict at all.
    """
    box = {}

    def read():
        try:
            box["value"] = page.evaluate(expression)
        except BaseException as error:  # noqa: BLE001
            box["error"] = f"{type(error).__name__}: {str(error)[:80]}"

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(seconds)
    if "value" in box:
        return box["value"]
    if "error" in box:
        return {"error": box["error"]}
    return {"error": f"no answer in {seconds:g}s (the realm is busy)"}


def report(context, page, started):
    """Print the witnesses and the verdict."""
    exchanges_since(started, context)
    jar_events()
    stored = cookies_of(context)
    print(f"cookies ({len(stored)}): {', '.join(sorted({entry[0] for entry in stored})) or '(none)'}")
    for name, domain, path, value in stored:
        print(f"    {name}@{domain}{path} = {value}")

    #: ``ensure_ascii`` and a guard, both learned the hard way: the page's own text is not
    #: ASCII, this console is GBK, and a print that raises takes the verdict down with it --
    #: one round printed ``cookies`` and then nothing at all.
    try:
        state = evaluate_bounded(page, SNAPSHOT)
        print(f"page: {json.dumps(state, ensure_ascii=True)}")
    except BaseException as error:  # noqa: BLE001 - a report must never eat the verdict
        print(f"page: unreadable ({type(error).__name__})")

    clearance = [entry for entry in stored if entry[0] == "cf_clearance"]
    on_steamdb = str(state.get("url", "")).startswith("https://steamdb.info/") or \
        "Steam Database" in str(state.get("title", ""))
    challenge = "challenge" in str(state.get("url", "")) or \
        "Just a moment" in str(state.get("title", ""))
    if clearance and on_steamdb and not challenge:
        print("VERDICT: PASS -- cf_clearance held, and the page is SteamDB")
        return 0
    missing = []
    if not clearance:
        missing.append("no cf_clearance")
    if not on_steamdb:
        missing.append("not on SteamDB")
    if challenge:
        missing.append("still on the challenge document")
    print(f"VERDICT: FAIL -- {', '.join(missing)}")
    return 1


def browse(stub_logging, trace, log, seconds, watch, trace_after=None, trace_only=None,
           log_level=None, impersonate=None, click_points=(), click_after=20.0):
    from loguru import logger
    from pyv8c import Browser

    #: The default sink writes every line to stderr as well, which buries the verdict: the
    #: run's stdout has to answer "did we get cf_clearance", nothing else.
    logger.remove()
    #: ``resource failed: <url> (<reason>)`` is DEBUG (``platform/resource_loading.py``), and it
    #: is the only line that says whether a resource was dropped *before* it ever reached the
    #: transport -- so the level has to be askable on its own, without tracing.
    logger.add(log, level=log_level or ("DEBUG" if (trace or trace_after or trace_only) else "INFO"),
               mode="w")
    logger.add(sys.stdout, level="WARNING")
    if watch:
        watch_transport()
        watch_jar()
        watch_executor()
        watch_calls()
        watch_trace_install()
        if trace_only:
            watch_log_trace(trace_only)

    started = len(EXCHANGES)
    began = time.time()
    callback, wanted = (make_filtered_trace(trace_only) if trace_only
                        else (None, set()))
    if wanted:
        print(f"trace filter: {', '.join(sorted(wanted))} (plus every event that raised)")
    #: ``impersonate`` moves the *transport* fingerprint, which is the one signal the two sides
    #: differ on before any JavaScript runs: the edge hands Chrome the JSD flavour and us the
    #: Turnstile one, and our first request is missing exactly the ``priority`` header curl_cffi's
    #: Chrome profiles add.  One variable, one run.
    transport = ({"http_engine": "curl_cffi", "http_options": {"impersonate": impersonate}}
                 if impersonate else {})
    if impersonate:
        print(f"transport: curl_cffi impersonating {impersonate}")
    browser = Browser(stub_logging=stub_logging, trace=trace or bool(trace_only),
                      traceable=trace or trace_after is not None or bool(trace_only),
                      trace_callback=callback, threaded_loaders=True, **transport)
    context = browser.new_context()
    #: Say what was installed rather than assume it: a filter that is silently dropped shows up
    #: as a hundred-megabyte log, so these two lines are worth their ink.
    if trace_only or trace:
        print(f"trace callback: {getattr(callback, '__name__', callback)}; "
              f"context inherited it: {context.trace_callback is browser.trace_callback}")
    page = context.new_page()
    code = 1  # a run that dies on the way is a failed run, not a crash
    try:
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001 - the challenge keeps working anyway
            print(f"goto: {type(error).__name__}: {str(error)[:100]}")
        pump(page, 5)
        #: Tracing every access costs ~100x -- measured: 670k reads of one global name turned a
        #: bounded loop into minutes -- so a run that traces from the start never reaches the
        #: step worth reading.  ``traceable`` flips the switch once the page has done its heavy
        #: lifting; what the widget reports to Cloudflare comes late.
        if trace_after is not None and not trace:
            pump(page, max(0.0, trace_after - 5))
            try:
                #: ``Page.trace`` is a property (the setter marshals onto the page's thread).
                page.trace = True
                print(f"trace on after {time.time() - began:.0f}s")
            except BaseException as error:  # noqa: BLE001 - a trace that will not start
                print(f"trace refused: {type(error).__name__}: {str(error)[:100]}")
            pump(page, max(0.0, seconds - trace_after))
        else:
            #: The run is judged once the page has had its full time, not when it looks quiet.
            if click_points:
                pump(page, max(0.0, click_after - (time.time() - began)))
                print(f"gesture at {time.time() - began:.0f}s: "
                      + " ".join(f"{x},{y}" for x, y in click_points))
                gesture(page, click_points)
                pump(page, max(0.0, seconds - click_after))
            else:
                pump(page, max(0.0, seconds - 5))
        code = report(context, page, started)
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(10.0)
        print(f"ran {time.time() - began:.1f}s")
        sys.stdout.flush()
        #: A wedged realm must not outlive the run: the OS reclaims whatever is left.
        os._exit(code)


def _turn(page, deadline, step):
    """Turn the realm's queue until *deadline*; errors on the way are the run's own measurement."""
    while time.time() < deadline:
        try:
            page.run_until_idle(timeout=0)
        except BaseException:  # noqa: BLE001
            pass
        time.sleep(step)


def pump(page, seconds=0.0, step=0.2, grace=1.0):
    """Let the queue turn for *seconds*, without letting a busy realm eat the window.

    A running challenge keeps its own timers going, so the page need not reach idle.  The other
    half is that ``run_until_idle`` marshals onto the owner thread under the executor's 60s
    deadline: when the challenge's own scripts hold that thread, one call blocks for a minute,
    and a run whose window is 120s was still inside its first call at 300s -- the verdict never
    printed.  So the turning happens on a worker of its own and this thread only waits out the
    clock; whatever the worker is still doing is a daemon's business.
    """
    if seconds <= 0:
        return
    turner = threading.Thread(target=_turn, args=(page, time.time() + seconds, step), daemon=True)
    turner.start()
    turner.join(seconds + grace)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--trace", action="store_true", help="verbose stubs and engine trace")
    #: 90, not 30: the challenge only submits ``cf_clearance`` late in its run -- with a 25-30s
    #: window every run looked like "the server never sent a cookie" (measured 2026-10-06).
    parser.add_argument("--seconds", type=float, default=90.0, help="seconds the page may live")
    parser.add_argument("--timeout", type=float, default=200.0, help="hard kill for the child")
    parser.add_argument("--log", type=Path, default=LOG, help="where the child logs")
    parser.add_argument("--no-watch", action="store_true",
                        help="without the transport witness (exchanges and Set-Cookie)")
    #: Tracing the whole run costs ~100x, which is the difference between reaching ``pat``/``ci``
    #: and never getting there.  This switches it on late, where the reported errors are.
    parser.add_argument("--trace-after", type=float, default=None, metavar="SECONDS",
                        help="switch the engine trace on this many seconds in (instead of --trace)")
    #: The trace costs ~100x, so tracing a whole run starves the challenge.  This keeps tracing
    #: on throughout while dropping everything but the names asked for and whatever raised.
    parser.add_argument("--trace-only", default=None, metavar="NAMES",
                        help="trace all run, but log only these members (plus every raise)")
    #: DEBUG without tracing: the engine's own lines are what say a resource was dropped before
    #: the transport ever saw it (``resource failed: …``), and at INFO they are invisible.
    parser.add_argument("--log-level", default=None, metavar="LEVEL",
                        help="loguru level for the run's log (DEBUG reaches 'resource failed')")
    #: The transport fingerprint is a variable of its own: our runs had none, and the edge hands
    #: the two sides different flavours of this challenge.
    parser.add_argument("--impersonate", default=None, metavar="PROFILE",
                        help="curl_cffi profile for the transport (e.g. chrome136, as in akm.py)")
    #: The managed challenge on ``/login/`` waits for a hand -- see ``gesture()``.  Repeatable:
    #: ``--click 360,220 --click 700,380``.
    parser.add_argument("--click", action="append", default=None, metavar="X,Y",
                        help="approach this point and click, as in akm.py (repeatable)")
    parser.add_argument("--click-after", type=float, default=20.0, metavar="SECONDS",
                        help="when the gesture happens (default 20)")
    arguments = parser.parse_args(argv)

    points = []
    for pair in arguments.click or ():
        try:
            x, y = (int(part) for part in str(pair).split(","))
        except ValueError:
            raise SystemExit(f"--click wants X,Y (got {pair!r})")
        points.append((x, y))

    if arguments.child:
        browse("verbose" if arguments.trace else "off", arguments.trace, arguments.log,
               arguments.seconds, not arguments.no_watch, arguments.trace_after,
               arguments.trace_only, arguments.log_level, arguments.impersonate,
               tuple(points), arguments.click_after)
        return 0

    command = [sys.executable, "-u", __file__, "--child", "--log", str(arguments.log),
               "--seconds", str(arguments.seconds)]
    if arguments.trace:
        command.append("--trace")
    if arguments.log_level:
        command += ["--log-level", str(arguments.log_level)]
    if arguments.impersonate:
        command += ["--impersonate", str(arguments.impersonate)]
    for x, y in points:
        command += ["--click", f"{x},{y}"]
    if points:
        command += ["--click-after", str(arguments.click_after)]
    if arguments.trace_after is not None:
        command += ["--trace-after", str(arguments.trace_after)]
    if arguments.trace_only:
        command += ["--trace-only", str(arguments.trace_only)]
    if arguments.no_watch:
        command.append("--no-watch")
    process = subprocess.Popen(command)
    try:
        code = process.wait(timeout=arguments.timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        print(f"killed after {arguments.timeout:.0f}s: the page never ran out of work",
              file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
