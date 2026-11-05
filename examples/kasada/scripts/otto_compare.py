"""Compare OTTO's Kasada /tl exchange between pyv8c and the installed Chrome.

Both sides load the live page through the same proxy and the same request filter:
the document plus the challenge endpoints only.  The report records the /tl body
size, the x-kpsdk header lengths and the response, which is what separates a
payload-shape difference from a transport or session one.

This is a diagnostic baseline: the Chrome run uses a browser, the pyv8c run is the
runtime under test.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger

from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = "https://www.otto.de/"
CHALLENGE = ("p.js", "rc.js", "mfc", "fp", "ips.js", "tl")
HERE = Path(__file__).resolve().parent


def _is_challenge(url: str) -> bool:
    parts = urlsplit(url)
    return ((parts.hostname or "").lower() == "www.otto.de"
            and any(parts.path.endswith("/" + suffix) for suffix in CHALLENGE))


def _is_document(url: str, request: dict) -> bool:
    parts = urlsplit(url)
    return ((parts.hostname or "").lower() == "www.otto.de"
            and parts.path == "/" and request.get("resource_type") == "document")


def run_pyv8c(proxy: str, timeout: float) -> dict:
    captured: dict = {}

    def intercept(url, request):
        parts = urlsplit(str(url))
        allow = (_is_challenge(str(url)) or _is_document(str(url), request)
                 or parts.scheme in ("about", "data", "blob", "javascript"))
        return None if allow else False

    class Observed(CurlCffiTransport):
        def _call(self, request):
            response = super()._call(request)
            if _is_challenge(request.url) and urlsplit(request.url).path.endswith("/tl"):
                body = request.body or b""
                headers = {str(k).lower(): str(v) for k, v in (request.headers or {}).items()}
                captured.update({
                    "status": response.status,
                    "body_bytes": len(body),
                    "body_head": body[:16].hex(),
                    "sdk_headers": {name: len(value) for name, value in headers.items()
                                    if name.startswith("x-kpsdk")},
                    "response": response.text[:120],
                })
            return response

    register_transport("otto_compare", lambda *, options=None: Observed(options=options),
                       option_keys=Observed.accepted_option_keys, proxy=True)
    browser = Browser(proxy=proxy, cache=False, threaded_loaders=True,
                      resource_interceptor=intercept, http_engine="otto_compare",
                      http_options={"impersonate": "chrome150", "timeout": 25},
                      ws_engine=None, stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        try:
            page.goto(URL, wait_until="commit", timeout=60)
        except BaseException:  # noqa: BLE001 - the challenge can still finish
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not captured:
            time.sleep(0.5)
        return captured
    finally:
        browser.close()


def run_chrome(proxy: str, timeout: float, chrome: str) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright is not installed"}
    captured: dict = {}

    def on_response(response):
        if not response.url.endswith("/tl"):
            return
        request = response.request
        body = request.post_data_buffer or b""
        headers = {name.lower(): value for name, value in request.headers.items()}
        captured.update({
            "status": response.status,
            "body_bytes": len(body),
            "body_head": body[:16].hex(),
            "sdk_headers": {name: len(value) for name, value in headers.items()
                            if name.startswith("x-kpsdk")},
            "response": response.text()[:120],
        })

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome, headless=False, proxy={"server": proxy},
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled"])
        try:
            page = browser.new_context().new_page()
            page.on("response", on_response)
            try:
                page.goto(URL, timeout=60000)
            except BaseException:  # noqa: BLE001
                pass
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and not captured:
                page.wait_for_timeout(500)
            return captured
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--side", choices=("both", "pyv8c", "chrome"), default="both")
    parser.add_argument("--chrome", default="C:/Program Files/Google/Chrome/Application/chrome.exe")
    parser.add_argument("--out", default=str(HERE.parent / "otto_tl_compare.json"))
    args = parser.parse_args()
    logger.remove()

    report = {}
    if args.side in ("both", "pyv8c"):
        report["pyv8c"] = run_pyv8c(args.proxy, args.timeout)
    if args.side in ("both", "chrome"):
        report["chrome"] = run_chrome(args.proxy, args.timeout, args.chrome)

    for side, value in report.items():
        print(side, json.dumps(value, ensure_ascii=False), flush=True)
    left, right = report.get("pyv8c"), report.get("chrome")
    if left and right and "body_bytes" in left and "body_bytes" in right:
        print("delta", json.dumps({
            "body_bytes": right["body_bytes"] - left["body_bytes"],
            "sdk_headers": {name: right["sdk_headers"].get(name, 0) - left["sdk_headers"].get(name, 0)
                            for name in set(right["sdk_headers"]) | set(left["sdk_headers"])},
        }, ensure_ascii=False), flush=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
