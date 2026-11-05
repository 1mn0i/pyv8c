"""Correlate the JSVMP instruction stream with the host calls it makes.

The tracer records ``(pc, opcode)`` for every dispatched instruction and a wall-clock
mark every 32 instructions.  Python records the wall clock of every host call, so the
two streams can be aligned by time and the opcode that precedes a host call identified.
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger

from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = "https://www.otto.de/"

TRACER = (
    "function p(e){var _v=t[e.r[0]++]>>5;try{var _b=self.__ops||(self.__ops=[]);"
    "_b.push(((e.r[0]&4095)<<8)|(_v&255));"
    "if((_b.length&3)===0){var _m=self.__marks||(self.__marks=[]);"
    "_m.push([_b.length,Date.now()])}}catch(x){}return _v}"
)
PATTERN = r'function p\(e\)\{return t\[e\.r\[0\]\+\+\]>>5\}'

HOST_CALLS = []
INTERESTING = ("query", "getParameter", "getExtension", "getRandomValues", "measureText",
               "fillText", "getImageData", "toDataURL", "getContext", "enumerateDevices",
               "getBattery", "estimate", "requestAdapter", "getHighEntropyValues")


def trace_callback(event, receiver, name, arguments, value, error, is_construct, context):
    try:
        label = str(name)
        if label not in INTERESTING:
            return
        if len(HOST_CALLS) >= 20000:
            return
        HOST_CALLS.append([round(time.time() * 1000, 2),
                           type(receiver).__name__ + "." + label])
    except Exception:
        pass


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
        if parts.path.endswith("/ips.js"):
            text, count = re.subn(PATTERN, TRACER, response.text, count=1)
            print("tracer patched:", count, flush=True)
            response.body = text.encode("utf8")
            return response
        if (parts.hostname or "").lower() == "www.otto.de" and parts.path.endswith("/tl"):
            try:
                body = response.json()
            except (ValueError, TypeError):
                body = None
            print("tl response:", response.status, body, flush=True)
        return response


register_transport("otto_vm_calls", lambda *, options=None: Observed(options=options),
                   option_keys=Observed.accepted_option_keys, proxy=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--proxy", default="http://127.0.0.1:7890")
    args = parser.parse_args()

    logger.remove()
    browser = Browser(proxy=args.proxy, cache=False, threaded_loaders=True,
                      resource_interceptor=intercept, http_engine="otto_vm_calls",
                      http_options={"impersonate": "chrome150", "timeout": 25},
                      ws_engine=None, stub_logging="off", trace=True,
                      trace_scripts=None, trace_callback=trace_callback)
    try:
        page = browser.new_profile().new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=45)
        except BaseException as error:
            print("navigation:", type(error).__name__, str(error)[:100], flush=True)
        payload = "none"
        for _round in range(args.timeout):
            time.sleep(1.0)
            current = page.evaluate("""(() => {
                let best = 'none';
                let size = 0;
                for (const frame of Array.from(document.querySelectorAll('iframe'))) {
                    const view = frame.contentWindow;
                    if (view && view.__ops && view.__ops.length > size) {
                        size = view.__ops.length;
                        try {
                            best = JSON.stringify({ops: view.__ops.slice(-20000),
                                                   marks: (view.__marks || []).slice(-4000)});
                        } catch (error) {}
                    }
                }
                return best;
            })()""")
            if current and current != "none" and len(current) > len(payload or ""):
                payload = current
            if payload and len(payload) > 100000:
                break
        target = Path(__import__("tempfile").gettempdir(), "otto_vm_calls.json")
        if payload and payload != "none":
            target.write_text(payload, encoding="utf8")
            print("vm stream captured:", len(payload), flush=True)
        Path(__import__("tempfile").gettempdir(), "otto_host_calls.json").write_text(
            json.dumps(HOST_CALLS), encoding="utf8")
        print("host calls captured:", len(HOST_CALLS), flush=True)
    finally:
        browser.close()


main()
