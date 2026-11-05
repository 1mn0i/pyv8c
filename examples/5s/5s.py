"""Run the live SteamDB challenge in pyv8c; accept an HTTP 200 Set-Cookie only.

No Chrome, captured Cookie, HAR replay, or saved challenge parameters are used.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'pyv8'), str(ROOT / 'pyv8c')]

from loguru import logger
from pyv8c import Browser, register_transport
from pyv8c.foundation.host_errors import HostStateError, RealmBusyError
from pyv8c.network.curl_cffi import CurlCffiTransport
from pyv8c.web.dom.hit_testing import border_box
from pyv8c.web.dom.nodes import _subtree_nodes


URL = 'https://steamdb.info/login/'
# URL = 'https://pastebin.com/login'
# URL = 'https://www.e-food.gr'
# URL = "https://fractal-testnet.unisat.io/explorer"

# URL = "https://cn.airbusan.com/content/individual"
# URL = "https://simple.ripley.com.pe/"

HOST_NAME = urlsplit(URL).hostname
# PROXY = 'http://127.0.0.1:7890'
PROXY = None


def run(*, proxy=PROXY, timeout=90):
    started = time.monotonic()
    deadline = started + timeout
    received = threading.Event()
    answer = {}
    clicked = set()

    class ObservedTransport(CurlCffiTransport):
        def _call(self, request):
            response = super()._call(request)
            value = response.cookies.get('cf_clearance')
            if (response.status == 200 and value
                    and urlsplit(response.url).hostname == HOST_NAME):
                answer.update(ok=True, status=response.status, url=response.url,
                              set_cookie=response.headers['set-cookie'], cf_clearance=value)
                received.set()
            return response

    register_transport('5s-observed', ObservedTransport, proxy=True)
    browser = Browser(cache=False, threaded_loaders=True, proxy=proxy,
                      http_engine='5s-observed',
                      http_options={'impersonate': 'chrome150', 'timeout': 20, 'retry': 5})
    try:
        profile = browser.new_profile()
        page = profile.new_page()
        page.goto(URL, wait_until='domcontentloaded', timeout=min(30, timeout))

        def click_ready_control():
            frames, pending = [], [page]
            while pending:
                frame = pending.pop()
                if frame.closed:
                    continue
                frames.append(frame)
                pending.extend(tuple(frame._owned.frames))
            for frame in frames:
                target = urlsplit(frame.url)
                if target.hostname != 'challenges.cloudflare.com' or '/turnstile/f/' not in target.path:
                    continue
                # The widget uses a closed shadow tree. Inspect host DOM objects
                # on their owner thread, without altering the SDK or shadow mode.
                for node in _subtree_nodes(frame.document):
                    if (node in clicked or getattr(node, 'localName', '') != 'input'
                            or node.attributes.get('type') != 'checkbox'):
                        continue
                    box = border_box(node)
                    if box.width <= 0 or box.height <= 0:
                        continue
                    try:
                        frame.mouse.click_element(node, delay=.08)
                    except HostStateError:
                        continue  # A covered/hidden control is not ready yet.
                    clicked.add(node)
                    print('Clicked authentication checkbox.', file=sys.stderr, flush=True)

        while not received.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'No HTTP 200 cf_clearance Set-Cookie within {timeout:g}s; clicks={len(clicked)}')
            try:
                page.executor.call(click_ready_control, timeout=min(3, remaining))
            except RealmBusyError:
                # The SDK may be processing a response; the transport still
                # observes replies without enqueuing JS evaluation probes.
                pass
            received.wait(min(.25, max(0, deadline-time.monotonic())))

        answer.update(clicks=len(clicked), elapsed=round(time.monotonic()-started, 2),
                      cookie_jar_held='cf_clearance=' in profile.cookies.header_for(URL))
        return answer
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', default=PROXY)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    # logger.remove()
    # logger.add(sys.stderr, level='WARNING')
    try:
        result = run(proxy=args.proxy, timeout=args.timeout)
    except Exception as error:
        print(json.dumps({'ok': False, 'error': f'{type(error).__name__}: {error}'}, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

