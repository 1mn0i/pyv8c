"""Put every document response on disk, so the challenge's HTML can be read offline.

    python script/dump_documents.py --seconds 25 [--dir script/cf_html]

``dump_scripts.py`` captures what the engine *runs*; this captures what it *receives*, which is
where a resource the page never asked for is discovered.  The JSD chain is the live example:
Chrome's first hop is ``GET …/challenge-platform/scripts/jsd/main.js -> 302`` and no script of
ours mentions ``jsd`` at all, so the request has to come from markup -- and markup was the one
thing never written down.

Read-only: the transport witness observes the call and returns the original response untouched.
"""
import argparse
import os
import pathlib
import re
import sys
import threading
import time

URL = "https://steamdb.info/"
DEFAULT_DIR = pathlib.Path(__file__).resolve().parent / "cf_html"


def safe(name, limit=60):
    return (re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "body"))[-limit:] or "body")


def install(directory, most, biggest):
    """Write the body of every document response the transport delivers."""
    from pyv8c.network.curl_cffi import CurlCffiTransport

    original = CurlCffiTransport._call
    state = {"count": 0, "bytes": 0}

    def wrapped(self, request):
        response = original(self, request)
        #: By content type, not by request kind: the request's own fields are not the same shape
        #: on every path, and asking for a field that does not exist captured nothing at all once.
        headers = {str(name).lower(): str(value)
                   for name, value in (dict(getattr(response, "headers", {}) or {})).items()}
        if "text/html" in headers.get("content-type", ""):
            body = getattr(response, "body", None) or b""
            text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
            if state["count"] < most and len(text) <= biggest:
                state["count"] += 1
                state["bytes"] += len(text)
                host = safe(str(getattr(request, "url", "")).split("//")[-1], 40)
                path = directory / f"{state['count']:02d}-{host}.html"
                path.write_text(f"<!-- {getattr(request, 'url', '')} -->\n\n{text}",
                                encoding="utf-8")
                print(f"    {path.name:56} {len(text):9d}B", flush=True)
        return response

    CurlCffiTransport._call = wrapped
    if CurlCffiTransport._call is not wrapped:
        raise RuntimeError("the document witness was not installed")


def browse(seconds, directory, most, biggest):
    from loguru import logger

    from pyv8c import Browser

    logger.remove()
    directory.mkdir(parents=True, exist_ok=True)
    install(directory, most, biggest)
    browser = Browser(stub_logging="off", threaded_loaders=True)
    try:
        page = browser.new_context().new_page()
        try:
            page.goto(URL)
        except BaseException as error:  # noqa: BLE001
            print(f"goto: {type(error).__name__}: {str(error)[:100]}")
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                page.run_until_idle(timeout=0)
            except BaseException:  # noqa: BLE001
                pass
            time.sleep(0.2)
        print(f"=== {directory}: {len(list(directory.iterdir()))} document(s)")
        sys.stdout.flush()
    finally:
        closer = threading.Thread(target=browser.close, daemon=True)
        closer.start()
        closer.join(5.0)
        os._exit(0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=25.0)
    parser.add_argument("--dir", type=pathlib.Path, default=DEFAULT_DIR)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--biggest", type=int, default=1_000_000)
    arguments = parser.parse_args(argv)
    browse(arguments.seconds, arguments.dir, arguments.max, arguments.biggest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
