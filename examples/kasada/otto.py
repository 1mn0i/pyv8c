"""Execute OTTO's live Kasada flow with pyv8c and report the /tl response.

The remote scripts generate the current fingerprint and payload. No HAR, captured
payload, browser automation or fixed challenge identifiers are used at runtime.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from pyv8c import pyv8
from pyv8c.pyv8.trace import format_trace
from loguru import logger
from pyv8c import Browser, register_transport
from pyv8c.network.curl_cffi import CurlCffiTransport

URL = 'https://www.otto.de/'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', default='http://127.0.0.1:7890')
    parser.add_argument('--timeout', type=float, default=75)
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--trace-ips-errors', action='store_true',
                        help='summarize failed binding operations in ips.js')
    parser.add_argument('--allow-site-scripts', action='store_true',
                        help='also load OTTO scripts while diagnosing dependencies')
    parser.add_argument('--allow-site-resources', action='store_true',
                        help='also load OTTO resources while diagnosing dependencies')
    args = parser.parse_args()
    if not args.verbose:
        logger.remove()
    completed = threading.Event()
    results = []
    counts = {'allowed': 0, 'blocked': 0}
    errors = {}
    error_examples = {}

    def trace(event, receiver, name, arguments, value, error, is_construct, context):
        if error is not pyv8.Undefined and error is not None:
            key = (str(context), str(event), type(receiver).__name__, str(name), type(error).__name__)
            errors[key] = errors.get(key, 0) + 1
            if key not in error_examples:
                error_examples[key] = str(format_trace(event, receiver, name, arguments, value, error,
                                                       is_construct, context))[:500]

    def intercept(url, request):
        parts = urlsplit(str(url))
        kind = request.get('resource_type', '')
        path = parts.path
        is_otto = (parts.hostname or '').lower() == 'www.otto.de'
        challenge = is_otto and any(path.endswith('/' + suffix)
                                    for suffix in ('p.js', 'rc.js', 'mfc', 'fp', 'ips.js', 'tl'))
        document = is_otto and path == '/' and kind == 'document'
        hostname = (parts.hostname or '').lower()
        other_script = (args.allow_site_scripts and kind == 'script'
                        and (hostname == 'otto.de' or hostname.endswith('.otto.de')))
        site_resource = (args.allow_site_resources
                         and (hostname == 'otto.de' or hostname.endswith('.otto.de'))
                         and kind not in ('image', 'media'))
        allow = challenge or document or other_script or site_resource or parts.scheme in ('about', 'data', 'blob', 'javascript')
        counts['allowed' if allow else 'blocked'] += 1
        if challenge:
            print('request:', request.get('method', 'GET'), path.rsplit('/', 1)[-1], flush=True)
        return None if allow else False

    class ObservedTransport(CurlCffiTransport):
        def _call(self, request):
            response = super()._call(request)
            parts = urlsplit(request.url)
            if ((parts.hostname or '').lower() == 'www.otto.de'
                    and parts.path.rsplit('/', 1)[-1] in ('p.js', 'rc.js', 'mfc', 'fp', 'ips.js', 'tl')):
                headers = {str(k).lower(): v for k, v in (response.headers or {}).items()}
                print('response:', parts.path.rsplit('/', 1)[-1], response.status,
                      'bytes:', len(response.content), 'x-kpsdk-r:', headers.get('x-kpsdk-r'), flush=True)
            if (parts.hostname or '').lower() == 'www.otto.de' and parts.path.endswith('/tl') and request.method.upper() == 'POST':
                headers = {str(k).lower(): v for k, v in (request.headers or {}).items()}
                body_bytes = request.body or b''
                if os.environ.get('OTTO_DUMP_TL'):
                    Path(os.environ['OTTO_DUMP_TL']).write_bytes(body_bytes)
                print('tl request:', 'body_bytes:', len(body_bytes), 'head:', body_bytes[:16].hex(),
                      'headers:', {k: len(str(v)) for k, v in headers.items() if k.startswith('x-kpsdk-')},
                      'cookie_names:', [item.strip().split('=', 1)[0]
                                       for item in str(self.cookies.header_for(request.url) or '').split(';') if item.strip()],
                      'referer:', urlsplit(str(headers.get('referer', ''))).path, flush=True)
                try:
                    body = response.json()
                except (ValueError, TypeError):
                    body = None
                results.append((response.status, body))
                print('tl response:', response.status, json.dumps(body, ensure_ascii=False), flush=True)
                if body is None:
                    print('tl response body:', repr(response.text[:200]), flush=True)
                if response.status == 200 and isinstance(body, dict) and body.get('reload') is True:
                    completed.set()
            return response

    register_transport('otto_observed', lambda *, options=None: ObservedTransport(options=options),
                       option_keys=ObservedTransport.accepted_option_keys, proxy=True)
    override = os.environ.get('PYV8C_ENV_JSON')
    environment = (json.loads(Path(override).read_text(encoding='utf-8'))
                   if override else None)
    browser = Browser(proxy=args.proxy, cache=False, threaded_loaders=True, environment=environment,
                      resource_interceptor=intercept, http_engine='otto_observed',
                      http_options={'impersonate': 'chrome150', 'timeout': 25},
                      ws_engine=None, stub_logging='off', trace=args.trace_ips_errors,
                      trace_scripts='*ips.js*' if args.trace_ips_errors else None,
                      trace_callback=trace if args.trace_ips_errors else None)
    try:
        page = browser.new_profile().new_page()
        started = time.monotonic()
        try:
            page.goto(URL, wait_until='domcontentloaded', timeout=min(args.timeout, 45))
        except BaseException as error:
            print('navigation:', type(error).__name__, str(error)[:200], flush=True)
        remaining = max(0.0, args.timeout - (time.monotonic() - started))
        completed.wait(remaining)
        print('requests:', counts, flush=True)
        if errors:
            print('ips.js failed operations:', sorted(errors.items(), key=lambda item: -item[1])[:30], flush=True)
            for line in error_examples.values():
                print('ips.js error sample:', line, flush=True)
        if any(status == 200 and isinstance(body, dict) and body.get('reload') is True
               for status, body in results):
            print('PASS: /tl returned {"reload":true}', flush=True)
            return 0
        print('FAIL: no successful /tl response observed', flush=True)
        return 1
    finally:
        browser.close()


if __name__ == '__main__':
    raise SystemExit(main())
