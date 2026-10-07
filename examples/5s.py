"""Judge the steamdb.info challenge: run the page in pyv8c and say whether it came through.

    python 5s.py                   # run once, judge, exit 0/1
    python 5s.py --trace           # verbose stubs + engine trace -> 5s.log
    python 5s.py --seconds 30      # how long the page is given
    python 5s.py --timeout 180     # seconds before the child is killed
    python 5s.py --json            # the raw readings instead of the report

判据（见 README 开头）：页面出现**它自己的内容**（标题、导航、表格）而不是 Cloudflare 的拦截页或
封禁页；`cf_clearance` 是否下发单独列出（它是判"过挑战"的硬条件，没有就是没通）。脚本**自成一体**：
读数探针就在本文件里，不 import `script/` 里的任何东西。

子进程 + 硬超时的理由：页面里的挑战是一个不会自己结束的长任务，``run_until_idle`` 打断不了它，
只能由父进程到点杀掉。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG = HERE / "5s.log"
URL = "https://steamdb.info/"


#: 还在挑战里的标志：页面上出现这些名字，就说明这不是站点本身。
INTERSTITIAL = ("#cf-challenge-running", ".cf-browser-verification", "#challenge-form",
                "#challenge-stage", "#cf-please-wait")
#: 标题若命中这些，说明拿到的是拦截/封禁页。
BLOCKED_TITLES = ("just a moment", "attention required", "access denied",
                  "checking your browser", "verify you are human")


def pump(page, seconds):
    """Let the queue turn for *seconds*; a busy realm is left to finish and retried."""
    deadline = time.time() + seconds
    while True:
        try:
            page.run_until_idle(timeout=0)
        except Exception:  # noqa: BLE001
            pass
        if time.time() >= deadline:
            return
        time.sleep(0.2)


def judge(reading, requests):
    """The two verdicts, from the page's own readings plus what the run asked for."""
    title = str(reading.get("title", ""))
    marks = str(reading.get("cfMarks", ""))
    counts = reading.get("counts") or {}
    elements = int(reading.get("elements") or 0)
    rows = int(counts.get("tr") or 0)
    tables = int(counts.get("table") or 0)

    why = []
    if any(name in title.lower() for name in BLOCKED_TITLES):
        why.append(f"title is the interstitial {title[:40]!r}")
    if any(name in marks for name in INTERSTITIAL):
        why.append(f"challenge nodes still on the page: {marks}")
    if not elements:
        why.append("no element count: the realm may not have started")
    if not (rows or tables) and elements < 2000:
        why.append(f"too little content: elements={elements} table={tables} tr={rows}")
    content = not why

    cloudflare = [target for target in requests if "challenge-platform" in target
                  or "challenges.cloudflare.com" in target]
    posts = [target for target in cloudflare if "/fo/" in target or "/pat/" in target]
    clearance = "cf_clearance" in str(reading.get("cookie", ""))
    return {
        "content": content,
        "contentWhy": why,
        "clearance": clearance,
        "requests": len(requests),
        "cloudflareRequests": len(cloudflare),
        "challengePosts": len(posts),
    }


def plain(value, limit=120):
    """Page data can be any script or any console: keep the report readable anywhere."""
    text = str(value)[:limit]
    return text.encode("ascii", "backslashreplace").decode("ascii")


def report(reading, verdict):
    print("=== readings")
    for key in ("title", "url", "ready", "viewport", "elements", "counts", "cfMarks", "cookie"):
        if key in reading:
            print(f"    {key:12} {plain(reading[key])}")
    if "text" in reading:
        print(f"    {'text':12} {plain(reading['text'])}")
    print("=== verdict")
    print(f"    {'page content':14} {'OK' if verdict['content'] else 'NO: ' + '; '.join(verdict['contentWhy'])}")
    print(f"    {'cf_clearance':14} {'set' if verdict['clearance'] else 'absent (challenge not passed)'}")
    print(f"    {'requests':14} {verdict['requests']} total, {verdict['cloudflareRequests']} cloudflare, "
          f"{verdict['challengePosts']} challenge posts")
    print(f"    {'VERDICT':14} {'PASS' if verdict['content'] and verdict['clearance'] else 'FAIL'}")
    return 0 if verdict["content"] and verdict["clearance"] else 1


def gesture(page, seconds):
    """Move the mouse across the page and click the widget, the way a visitor would.

    A managed challenge may be interactive, and an engine that never sends a pointer event is
    a client no real visitor resembles.  The window the page is given is split between the
    gestures and the time the page gets to react to them.
    """

    def widget_box():
        """The widget frame's centre: it lives inside a shadow root, so walk into them."""
        return page.evaluate("""(() => {
            const wanted = (frame) => {
                const src = String((frame && frame.getAttribute('src')) || '');
                return src.indexOf('challenges.cloudflare.com') >= 0
                    || src.indexOf('challenge-platform') >= 0;
            };
            const find = (root) => {
                const direct = Array.from(root.querySelectorAll('iframe')).find(wanted);
                if (direct) { return direct; }
                for (const element of root.querySelectorAll('*')) {
                    if (element.shadowRoot) {
                        const inner = find(element.shadowRoot);
                        if (inner) { return inner; }
                    }
                }
                return null;
            };
            const frame = find(document);
            if (!frame) { return null; }
            const box = frame.getBoundingClientRect();
            return [box.left + box.width / 2, box.top + box.height / 2,
                    window.innerWidth, window.innerHeight, box.width, box.height,
                    Boolean(frame.getRootNode() !== document)];
        })()""")

    #: The widget frame arrives a few seconds in (measured: ~4.3s), so aim once it is there
    #: rather than at a window the click would miss.
    found = None
    deadline = time.time() + min(12.0, max(4.0, seconds))
    inventory = []
    while found is None and time.time() < deadline:
        pump(page, 0.4)
        try:
            found = widget_box()
            inventory = page.evaluate(
                "JSON.stringify(Array.from(document.querySelectorAll('iframe'))"
                ".map(f => String(f.getAttribute('src') || '(no src)').slice(0, 60)))")
        except BaseException as error:  # noqa: BLE001 - a JS error is one of these
            print(f"-- widget lookup failed: {type(error).__name__}")
            break
    if found is None:
        print(f"-- top-level iframes seen: {inventory}")
    if found:
        target = (float(found[0]), float(found[1]))
        width, height = float(found[2]), float(found[3])
        print(f"-- widget at ({target[0]:.0f}, {target[1]:.0f}) of {width:.0f}x{height:.0f}"
              f", box {float(found[4]):.0f}x{float(found[5]):.0f}"
              f", in shadow root: {bool(found[6])}")
    else:
        target = (640.0, 360.0)
        width, height = 1280.0, 720.0
        print("-- no widget frame found; gesturing at the window instead")

    spots = [(60.0, 80.0), (width * 0.35, height * 0.25), (width * 0.6, height * 0.45), target,
             (target[0] + 7.0, target[1] + 5.0)]
    share = max(0.1, seconds / (len(spots) + 2))
    for x, y in spots:
        page.mouse.move(x, y, steps=8)
        pump(page, share)
    page.mouse.click(target[0], target[1])
    pump(page, share)
    page.mouse.click(target[0] + 3.0, target[1] + 2.0)
    pump(page, share)
    print(f"-- gestures done, mouse at {page.mouse.position}")


def browse(stub_logging, trace, log, seconds, gesture_mouse=False):
    from loguru import logger
    from pyv8c import Browser

    logger.add(log, level="DEBUG" if trace else "INFO", mode="w")
    requests = []

    def intercept(url, request):
        requests.append(str(url))
        return None

    started = time.time()
    browser = Browser(stub_logging=stub_logging, trace=trace, traceable=trace,
                      threaded_loaders=True, resource_interceptor=intercept)
    try:
        page = browser.new_context().new_page()
        page.goto(URL)
        #: The same total window either way, so a run with gestures is comparable with one
        #: without: half the time going through the motions, half letting the page answer.
        if gesture_mouse:
            gesture(page, seconds / 2)
            pump(page, seconds / 2)
        else:
            pump(page, seconds)
        try:
            reading = page.evaluate(STATE)
        except BaseException as error:  # noqa: BLE001 - a JS error is one of these
            reading = {"error": f"{type(error).__name__}: {str(error)[:120]}",
                       "elapsed": round(time.time() - started, 1)}
        print("-- elapsed %.1fs" % (time.time() - started))
        if "--json" in sys.argv or reading.get("error"):
            print(json.dumps({"reading": reading, "requests": requests},
                             ensure_ascii=False, indent=1)[:2000])
        if reading.get("error"):
            return 1
        verdict = judge(reading, requests)
        if "--json" in sys.argv:
            print(json.dumps(verdict, ensure_ascii=False, indent=1))
        return report(reading, verdict)
    finally:
        browser.close()


if __name__ == "__main__":
    sys.exit(browse(stub_logging="on", trace=True, log=LOG, seconds=30.0, gesture_mouse=False))
