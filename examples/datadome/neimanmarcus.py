"""Generate fresh interstitial parameters in pyv8c, submit once, verify the homepage."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
from threading import Event
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import struct

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(WORKSPACE / 'pyv8'), str(WORKSPACE / 'pyv8c')]

import moderngl
from loguru import logger

import pyv8
from pyv8c import Browser
from pyv8c.platform.resources.request_headers import browser_headers
from pyv8c.web.graphics.webgl import WebGLRenderingContext, WebGLUniformLocation



class NativeWebGL:
    def __init__(self):
        self._local = threading.local()
        self.error = None
        self.renderer = None

    def _context(self):
        if not hasattr(self._local, 'context'):
            self._local.context = moderngl.create_standalone_context(require=330)
            self._local.resources = []
            self.renderer = self._local.context.info['GL_RENDERER']
        return self._local.context

    def _own(self, resource):
        self._local.resources.append(resource)
        return resource

    def release_current_thread(self):
        """Release GL resources on the realm thread that created them."""
        if not hasattr(self._local, 'context'):
            return
        for resource in reversed(self._local.resources):
            resource.release()
        self._local.context.release()
        del self._local.context, self._local.resources

    @staticmethod
    def _source(source, fragment):
        source = re.sub(r'^\s*#version\s+100\s*', '', source)
        source = re.sub(r'precision\s+\w+\s+\w+\s*;', '', source)
        source = re.sub(r'\b(?:lowp|mediump|highp)\b', '', source)
        source = re.sub(r'\battribute\b', 'in', source)
        source = re.sub(r'\bvarying\b', 'in' if fragment else 'out', source)
        if fragment:
            source = 'out vec4 nativeFragment;\n' + source.replace('gl_FragColor', 'nativeFragment')
        return '#version 330\n' + source

    def _link(self, gl, program, *_):
        WebGLRenderingContext.fn_linkProgram(gl, program)
        sources = {shader.shader_type: shader.source for shader in program.shaders}
        native = self._context().program(
            vertex_shader=self._source(sources[35633], False),
            fragment_shader=self._source(sources[35632], True),
        )
        program._native_program = self._own(native)
        program.linked = True

    def _uniform_location(self, gl, program, name, *_):
        native = getattr(program, '_native_program', None)
        if native is None or str(name) not in native or not isinstance(native[str(name)], moderngl.Uniform):
            return pyv8.Null
        location = WebGLUniformLocation()
        location._native_uniform = native[str(name)]
        return location

    def _uniform1f(self, gl, location, value, *_):
        if isinstance(location, WebGLUniformLocation):
            location._native_uniform.value = float(value)

    def _attrib_location(self, gl, program, name, *_):
        native = getattr(program, '_native_program', None)
        return native[str(name)].location if native is not None and str(name) in native else -1

    def _buffer_data(self, gl, target, data, usage, *_):
        WebGLRenderingContext.fn_bufferData(gl, target, data, usage)
        values = [data[index] for index in range(int(data._get('length')))]
        raw = struct.pack('<' + 'f' * len(values), *values)
        gl._bound[int(target)]._native_buffer = self._own(self._context().buffer(raw))

    def _pointer(self, gl, index, size, kind, normalized, stride, offset, *_):
        if kind != 5126 or normalized or stride or offset:
            raise RuntimeError('The native adapter requires tightly packed, unnormalized float attributes.')
        if not hasattr(gl, '_native_attributes'):
            gl._native_attributes = {}
        gl._native_attributes[int(index)] = (gl._bound[34962], int(size))

    def _draw(self, gl, mode, first, count, *_):
        program = gl._bound.get(35725)
        native = getattr(program, '_native_program', None)
        if native is None:
            raise RuntimeError('A linked native shader program is required before drawing.')
        ctx = self._context()
        if not hasattr(gl, '_native_framebuffer'):
            gl._native_framebuffer = self._own(ctx.simple_framebuffer(
                (max(1, gl._canvas._width), max(1, gl._canvas._height)), components=4))
        gl._native_framebuffer.use()
        contents = []
        for index, (buffer, size) in gl._native_attributes.items():
            if index < 0:
                continue
            name = next(name for name in native
                        if isinstance(native[name], moderngl.Attribute) and native[name].location == index)
            contents.append((buffer._native_buffer, f'{size}f', name))
        vertex_array = ctx.vertex_array(native, contents)
        try:
            vertex_array.render(mode=int(mode), vertices=int(count), first=int(first))
        finally:
            vertex_array.release()

    def _read_pixels(self, gl, x, y, width, height, format, kind, pixels, *_):
        if not hasattr(gl, '_native_framebuffer'):
            return WebGLRenderingContext.fn_readPixels(gl, x, y, width, height, format, kind, pixels)
        if format != 6408 or kind != 5121:
            raise RuntimeError('The native adapter currently reads RGBA unsigned-byte pixels.')
        data = gl._native_framebuffer.read(
            viewport=(int(x), int(y), int(width), int(height)), components=4, alignment=1)
        if int(pixels._get('length')) < len(data):
            raise RuntimeError('The readPixels destination is too small.')
        for index, value in enumerate(data):
            pixels[index] = value

    @property
    def interface_overrides(self):
        def hook(method):
            # The binder expects a plain callable with an explicit receiver.
            def invoke(receiver, *args):
                try:
                    return method(receiver, *args)
                except Exception as exc:
                    self.error = f'{type(exc).__name__}: {exc}'
                    raise
            return invoke
        methods = {
            'linkProgram': self._link, 'getUniformLocation': self._uniform_location,
            'uniform1f': self._uniform1f, 'getAttribLocation': self._attrib_location,
            'bufferData': self._buffer_data, 'vertexAttribPointer': self._pointer,
            'drawArrays': self._draw, 'readPixels': self._read_pixels,
        }
        return {'WebGLRenderingContext': {name: hook(method) for name, method in methods.items()}}


URL = 'https://www.neimanmarcus.com/'
USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36')
CHALLENGE_HOSTS = {'ct.captcha-delivery.com', 'geo.captcha-delivery.com',
                   'static.captcha-delivery.com'}


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def run(proxy=None, output=None, timeout=60):
    output = Path(output) if output else Path(__file__).with_name('output') / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    ready = Event()
    state = {'success': False, 'target': URL, 'sdk': {}, 'flow': []}
    captured = {}
    phase = 'generating'
    page = None
    started = time.monotonic()
    renderer = NativeWebGL()
    browser = None

    def note(method, url, status):
        state['flow'].append({'method': method, 'url': url, 'status': status,
                              'elapsed_ms': round((time.monotonic() - started) * 1000)})
        parts = urlsplit(url)
        logger.info('{} {}{} -> {}', method, parts.hostname, parts.path, status)

    def intercept(url, request):
        nonlocal phase
        parts = urlsplit(url)
        if phase != 'generating':
            return False
        if parts.scheme in ('blob', 'data'):
            return None
        if not (parts.hostname in CHALLENGE_HOSTS or
                (parts.hostname == 'www.neimanmarcus.com' and parts.path == '/')):
            return False
        method = request.get('method', 'GET').upper()
        if method == 'POST' and parts.hostname == 'geo.captcha-delivery.com' and parts.path == '/interstitial/':
            body = request.get('body') or ''
            if isinstance(body, bytes):
                body = body.decode('utf-8')
            params = parse_qs(body, keep_blank_values=True)
            required = ('cid', 'hash', 'referer', 'url', 's', 'e', 'env', 'userEnv',
                        'seed', 'b', 'dm', 'payload', 'plv3', 'ps')
            missing = [key for key in required if len(params.get(key, [])) != 1 or not params[key][0]]
            print(body)
            captured.update(url=url, body=body, headers=dict(request.get('headers') or {}), params=params)
            if missing:
                captured['error'] = 'The SDK did not generate: ' + ', '.join(missing)
            phase = 'captured'
            ready.set()
            # Pause at the outgoing boundary. The host submits these exact bytes
            # once below; this local refusal is never sent to the website.
            return {'status': 400, 'headers': {'content-type': 'application/json'},
                    'body': '{"error":"request captured for host submission"}'}
        if parts.path.startswith('/captcha/'):
            captured['error'] = 'The site switched from the interstitial to a captcha.'
            ready.set()
            return False
        response = page.net.request(method, url, headers=request.get('headers'),
                                    data=request.get('body'), timeout=min(timeout, 25))
        note(method, url, response.status)
        if parts.hostname == 'ct.captcha-delivery.com':
            state['sdk'][url] = {'bytes': len(response.content),
                                  'sha256': hashlib.sha256(response.content).hexdigest()}
        if parts.hostname == 'geo.captcha-delivery.com' and parts.path == '/interstitial/':
            (output / 'challenge.html').write_bytes(response.content)
        if parts.hostname == 'www.neimanmarcus.com':
            state['initial_status'] = response.status
            (output / 'initial.html').write_bytes(response.content)
            if response.status == 200:
                captured['already_accessible'] = response
                phase = 'captured'
                ready.set()
                return {'status': 200, 'headers': {'content-type': 'text/html'},
                        'body': '<!doctype html><title>Initial response captured</title>'}
            if response.status != 403 or not re.search(r'\bvar\s+dd\s*=', response.text):
                captured['error'] = f'Expected the dd interstitial bootstrap, received HTTP {response.status}.'
                ready.set()
        return {'status': response.status, 'headers': response.headers, 'body': response.body}

    try:
        browser = Browser(
            proxy='http://127.0.0.1:7890', 
            cache=False, resource_interceptor=intercept,
                        #   interface_overrides=renderer.interface_overrides,
                        #   environment={'user_agent': USER_AGENT},
                          http_options={'impersonate': 'chrome150', 'timeout': min(timeout, 25)},
                          stub_logging='off')
        context = browser.new_profile()
        page = context.new_page()
        page.goto(URL, timeout=min(timeout, 60), wait_until='domcontentloaded')
        while not ready.wait(.1):
            if renderer.error:
                raise RuntimeError('Native GPU execution failed: ' + renderer.error)
            if time.monotonic() - started >= timeout:
                raise TimeoutError('The local SDK did not generate the interstitial request before timeout.')
        if captured.get('error'):
            raise RuntimeError(captured['error'])
        if 'already_accessible' in captured:
            response = captured['already_accessible']
            state['generated_parameters'] = False
        else:
            params = captured['params']
            write_json(output / 'parameters.json', {key: values[0] for key, values in params.items()})
            (output / 'request.form').write_text(captured['body'], encoding='utf-8')
            state.update(generated_parameters=True, payload_chars=len(params['payload'][0]),
                         plv3_chars=len(params['plv3'][0]))
            logger.info('Generated payload={} chars, plv3={} chars',
                        state['payload_chars'], state['plv3_chars'])
            phase = 'submitting'
            response = page.net.request('POST', captured['url'], data=captured['body'],
                                        headers=captured['headers'], timeout=min(timeout, 25))
            note('POST', captured['url'], response.status)
            state['interstitial_status'] = response.status
            data = response.json()
            write_json(output / 'interstitial-response.json', data)
            state['view'] = data.get('view') if isinstance(data, dict) else None
            if response.status != 200 or not isinstance(data, dict) or data.get('view') != 'redirect':
                raise RuntimeError(f'Interstitial rejected: HTTP {response.status}, view={state["view"]!r}.')
            assignment = data.get('cookie', '')
            if not isinstance(assignment, str) or not assignment.startswith('datadome='):
                raise RuntimeError('The redirect response contains no datadome cookie.')
            cookie = assignment.split(';', 1)[0]
            context.cookies.set_from_document(URL, assignment)
            if cookie not in context.cookies.header_for(URL).split('; '):
                raise RuntimeError('The returned cookie was not stored for the target domain.')
            headers = browser_headers(page.environment, destination='document', mode='navigate',
                                      method='GET', url=URL, current_url=URL, http_version=2)
            response = page.net.request('GET', URL, headers=headers, referer=URL, timeout=min(timeout, 25))
            note('GET', URL, response.status)
        state.update(home_status=response.status, html_bytes=len(response.content),
                     renderer=renderer.renderer)
        (output / 'home.html').write_bytes(response.content)
        if response.status != 200 or 'data-reactroot' not in response.text:
            raise RuntimeError(f'Homepage verification failed: HTTP {response.status}.')
        state['success'] = True
        logger.info('Homepage verified: HTTP 200, {} bytes; artifacts: {}', len(response.content), output)
        return state
    except (Exception, pyv8.JSException) as exc:
        state['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        phase = 'closed'
        state['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        write_json(output / 'result.json', state)
        if browser is not None:
            try:
                if page is not None:
                    pending = [page]
                    seen = set()
                    while pending:
                        owner = pending.pop()
                        if id(owner) in seen or owner.closed:
                            continue
                        seen.add(id(owner))
                        pending.extend(tuple(owner._owned.frames))
                        owner.executor.call(renderer.release_current_thread)
            finally:
                browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', default=os.environ.get('NEIMAN_PROXY'))
    parser.add_argument('--output-dir', type=Path, help='A new directory for this run\'s private artifacts.')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    logger.remove()
    logger.add(sys.stderr, level='INFO', format='{time:HH:mm:ss} | {level} | {message}')
    try:
        run(args.proxy, args.output_dir, args.timeout)
    except (Exception, pyv8.JSException) as exc:
        logger.error('{}: {}', type(exc).__name__, str(exc))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
