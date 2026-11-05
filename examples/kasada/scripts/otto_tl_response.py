"""Dump the raw /tl response: status, headers, length and the first bytes."""

import argparse
import time
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger

from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = "https://www.otto.de/"


def intercept(url, request):
    parts = urlsplit(str(url))
    kind = request.get("resource_type", "")
    path = parts.path
    is_otto = (parts.hostname or "").lower() == "www.otto.de"
    challenge = is_otto and any(path.endswith("/" + suffix)
                                for suffix in ("p.js", "rc.js", "mfc", "fp", "ips.js", "tl"))
    document = is_otto and path == "/" and kind == "document"
    allow = challenge or document or parts.scheme in ("about", "data", "blob", "javascript")
    return None if allow else False


class Observed(CurlCffiTransport):
    def _call(self, request):
        response = super()._call(request)
        parts = urlsplit(request.url)
        if (parts.hostname or "").lower() == "www.otto.de" and parts.path.endswith("/tl"):
            headers = dict(getattr(response, "headers", {}) or {})
            body = getattr(response, "body", b"") or b""
            if isinstance(body, str):
                body = body.encode("utf8", "ignore")
            print("TL_STATUS", response.status, flush=True)
            for key, value in headers.items():
                print("TL_HEADER", key, "=", str(value)[:80], flush=True)
            print("TL_LEN", len(body), flush=True)
            print("TL_HEX", body[:64].hex(), flush=True)
            Path(__import__("tempfile").gettempdir(), "otto_tl_body.bin").write_bytes(body)
        return response


register_transport("otto_tl_response", lambda *, options=None: Observed(options=options),
                   option_keys=Observed.accepted_option_keys, proxy=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    args = parser.parse_args()

    logger.remove()
    browser = Browser(proxy=args.proxy, cache=False, threaded_loaders=True,
                      resource_interceptor=intercept, http_engine="otto_tl_response",
                      http_options={"impersonate": "chrome150", "timeout": 25},
                      ws_engine=None, stub_logging="off")
    try:
        page = browser.new_profile().new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=45)
        except BaseException as error:
            print("navigation:", type(error).__name__, str(error)[:100], flush=True)
        time.sleep(args.timeout)
    finally:
        browser.close()


main()
