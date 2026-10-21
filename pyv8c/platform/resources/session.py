"""Realm-free request sessions: only immutable config and ordinary payloads cross threads."""
from dataclasses import dataclass
from types import SimpleNamespace

from ...foundation.host_errors import LoadFailure
from ...foundation.urls import absolute_url, origin_tuple, serialize_origin, unloadable_scheme
from ...network.models import NetworkRequest, NetworkResponse
from ...network.redirects import Deliver, RedirectFailure, next_hop
from .local import data_url_response, extension_response


def check_origin(origin, target_origin, mode):
    if mode == 'same-origin' and origin != target_origin:
        raise LoadFailure('same-origin request crossed an origin')


def check_cors(headers, origin, credentials):
    allowed = headers.get('access-control-allow-origin', '')
    if allowed not in ('*', serialize_origin(origin)):
        raise LoadFailure('CORS check failed')
    if credentials == 'include' and (allowed == '*' or
            headers.get('access-control-allow-credentials', '') != 'true'):
        raise LoadFailure('credentialed CORS request was not allowed')


def plain_response(value, url):
    """Copy host/transport responses; never publish JS values or bound responses."""
    if isinstance(value, dict):
        body = value.get('body', '')
        headers = value.get('headers', {})
        status, status_text = value.get('status', 200), value.get('statusText', 'OK')
        redirected, kind = value.get('redirected', False), value.get('type', 'basic')
        url = value.get('url') or url
    elif isinstance(value, (str, bytes)) or value is None:
        body, headers, status, status_text, redirected, kind = str(value) if value is None else value, {}, 200, 'OK', False, 'basic'
    else:
        body, headers = value.body, value.headers
        headers = getattr(headers, '_data', headers)
        status, status_text = value.status, value.status_text
        redirected, kind = value.redirected, value.type
        url = value.url or url
    if isinstance(body, (bytearray, memoryview)):
        body = bytes(body)
    if body is not None and not isinstance(body, (str, bytes)):
        raise LoadFailure('resource response body is not plain data')
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                           for k, v in headers.items()):
        raise LoadFailure('resource response headers are not plain data')
    return NetworkResponse(url=str(url), body=body, status=int(status), status_text=str(status_text),
                           headers={k.lower(): v for k, v in headers.items()},
                           redirected=bool(redirected), type=str(kind))


def select_source(*, profile, environment, initiator, target, options, origin,
                  cache_key, loader, backend):
    """Shared source precedence for page and Worker requests."""
    cached = profile.resource_cache.get(cache_key)
    if cached is not None:
        return 'payload', plain_response(cached, target)
    if target.startswith('about:blank'):
        return 'payload', NetworkResponse(url=target, body='')
    if target.startswith('blob:'):
        target_origin = tuple(profile._blob_origins.get(target, origin_tuple(target)))
        if options.get('mode') != 'navigate' and origin != target_origin:
            raise LoadFailure('Blob URL belongs to another origin')
        body, content_type = profile.resolve_blob_url(target)
        return 'payload', NetworkResponse(url=target, body=body, headers={'content-type': content_type})
    if target[:5].lower() == 'data:':
        return 'payload', plain_response(data_url_response(target, None), target)
    view = SimpleNamespace(environment=environment, url=initiator, services=None)
    extension = extension_response(view, target, options)
    if extension is not None:
        return 'payload', plain_response(extension, target)
    if loader:
        return 'loader', loader
    blocked = unloadable_scheme(target)
    if blocked is not None:
        raise LoadFailure(blocked)
    if backend is not None and backend.http_available:
        return 'engine', backend
    raise LoadFailure('Network access requires an explicit resource_loader or real_network=True')


@dataclass(frozen=True, slots=True)
class ResourceRequestSession:
    """Snapshot on the creator owner; contains no Page, DOM or Realm callback."""
    profile: object
    environment: object
    origin: tuple
    base_url: str
    loader: object
    interceptor: object
    backend: object
    cache_enabled: bool

    def load(self, url, options, cancelled):
        from .fetcher import ResourceFetchController
        options = dict(options or {})
        initiator = str(options.get('initiator_url') or self.base_url)
        target = absolute_url(url, initiator)
        origin = self.origin
        view = SimpleNamespace(environment=self.environment, url=initiator)
        mode = ResourceFetchController._request_mode(options)
        prepare = ResourceFetchController._request_with_environment_headers
        prepared = prepare(options, view, target, initiator, origin)
        cache = self.profile.resource_cache
        key = cache.key(target, prepared, enabled=self.cache_enabled, origin=serialize_origin(origin))

        def active():
            if cancelled() or self.profile.closed:
                raise LoadFailure('resource owner closed or request cancelled')

        active()
        if self.interceptor is not None:
            decision = self.interceptor(target, dict(prepared))
            active()
            if decision is not None and decision is not True:
                return plain_response({'body': ''} if decision is False else decision, target)
        kind, source = select_source(profile=self.profile, environment=self.environment,
                                     initiator=initiator, target=target, options=options, origin=origin,
                                     cache_key=key, loader=self.loader, backend=self.backend)
        if kind == 'payload':
            return source
        method, body = str(options.get('method', 'GET')).upper(), options.get('body')
        headers = ResourceFetchController.header_dict(options.get('headers'))
        follows = kind == 'engine' and bool(getattr(source.http, 'FOLLOWS_ITSELF', False))
        hops = 0
        while True:
            active()
            target_origin = tuple(self.profile._blob_origins.get(target, origin_tuple(target)))
            check_origin(origin, target_origin, mode)
            request = prepare({**options, 'method': method, 'body': body, 'headers': headers},
                              view, target, initiator, origin)
            if kind == 'loader':
                value = source(target, request)
                response = plain_response({**value, 'url': target} if isinstance(value, dict) else value, target)
            else:
                response = plain_response(source.request_sync(NetworkRequest(
                    url=target, method=method, body=body, headers=request['headers'],
                    timeout=request.get('timeout'), credentials=request.get('credentials', 'same-origin'),
                    mode=mode, redirect=str(options.get('redirect', 'follow')) if follows else 'manual',
                    origin=serialize_origin(origin))), target)
            active()
            response_origin = origin_tuple(response.url)
            if mode == 'cors' and response_origin != origin:
                check_cors(response.headers, origin, options.get('credentials'))
            check_origin(origin, response_origin, mode)
            if follows and response.redirected:
                if options.get('redirect', 'follow') != 'follow':
                    raise LoadFailure('transport followed a redirect that must be inspected')
                decision = Deliver()
            else:
                try:
                    decision = next_hop(status=response.status, method=method, url=target, headers=headers,
                                        body=body, location=response.headers.get('location', ''), hops=hops,
                                        mode=mode, redirect=str(options.get('redirect', 'follow')))
                except RedirectFailure as error:
                    raise LoadFailure(str(error)) from error
            if not isinstance(decision, Deliver):
                target, method, body, headers = decision.url, decision.method, decision.body, decision.headers
                hops += 1
                continue
            if decision.opaque:
                return NetworkResponse(url=target, status=0, status_text='', body='', type='opaqueredirect')
            response.redirected = bool(hops or response.redirected)
            if options.get('mode') == 'no-cors' and origin != response_origin:
                response.status, response.body, response.headers, response.type = 0, '', {}, 'opaque'
            active()
            cache.put(key, {'body': response.body, 'status': response.status,
                            'statusText': response.status_text, 'url': response.url,
                            'headers': response.headers, 'redirected': response.redirected, 'type': response.type})
            return response
