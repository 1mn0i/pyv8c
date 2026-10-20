"""Worker-owned requests; only plain data crosses the two owner queues."""
from functools import partial

from ...foundation.arguments import is_nullish
from ...foundation.host_errors import LoadFailure
from ...foundation.tasks import TaskSource
from ...foundation.urls import origin_tuple
from ...network.models import NetworkResponse
from ...web.fetch.response import Response
from ...web.fileapi.blob import Blob
from .fetcher import LoadContext
from .handles import LoadHandle


class WorkerResourceCapability:
    def __init__(self, worker):
        self.worker = worker
        self._context = None
        self._callbacks = {}
        # Only the Worker owner reads or releases these V8-bound closures.
        worker._resource_scope.add(self._callbacks.clear)

    @property
    def origin(self):
        if self._context is None:
            config = self.worker.config
            self._context = LoadContext(
                origin=origin_tuple(self.worker.script_url, inherited=config.creator_origin),
                owner_realm=config.owner_realm, force_pool=True)
        return self._context.origin

    def load_resource_async(self, url, request, callback):
        return self.worker.resource_ownership.issue(self._start, url, request, callback)

    def _start(self, url, request, callback):
        handle = LoadHandle()
        self._callbacks[handle] = callback
        handle.add_done_callback(self._terminal)
        try:
            options = dict(request or {})
            options.setdefault('initiator_url', self.worker.script_url)
            headers = options.get('headers')
            options['headers'] = dict(getattr(headers, '_data', headers) or {})
            body = options.get('body')
            # Buffer/Blob and string coercion are evaluated on the Worker,
            # before any callback can inspect the request on another owner.
            options['body'] = (None if is_nullish(body) else body
                               if isinstance(body, (str, bytes)) else Blob([body])._data)
            timeout = options.get('timeout')
            options['timeout'] = None if is_nullish(timeout) else float(timeout)
            self.origin  # Freeze the effective origin once, including opaque identity.
        except BaseException as error:
            self._post_result(handle, None, error)
            return handle
        parent = self.worker.page
        try:
            ticket = parent._tasks.post_task(
                partial(self._send, handle, str(url), options), TaskSource.NETWORKING,
                label='Worker fetch request')
        except BaseException as error:
            self._post_result(handle, None, LoadFailure(str(error)))
            return handle
        if ticket is None:
            handle.cancel()
        return handle

    def _terminal(self, handle):
        if handle.cancelled:
            self.worker.tasks.post_task(partial(self._discard, handle), TaskSource.NETWORKING,
                                        label='Worker fetch cleanup')

    def _discard(self, handle):
        self._callbacks.pop(handle, None)

    def _send(self, handle, url, options):
        parent = self.worker.page
        if (handle.done or self.worker.tasks.closed or
                parent._page_realm is not self._context.owner_realm):
            handle.cancel()
            return
        try:
            inner = parent._resource_fetcher.load_resource_async(
                url, options, partial(self._received, handle), context=self._context)
            handle.bind_transport(inner)
            inner.add_done_callback(lambda done: handle.cancel() if done.cancelled else None)
        except BaseException as error:
            self._post_result(handle, None, LoadFailure(str(error)))

    def _received(self, handle, response, error):
        if handle.done:
            return
        if error is not None:
            # A V8 exception or host response must never escape its owner.
            self._post_result(handle, None, LoadFailure(str(error)))
            return
        try:
            body = response.body
            if isinstance(body, (bytearray, memoryview)):
                body = bytes(body)
            if body is not None and not isinstance(body, (str, bytes)):
                raise LoadFailure('worker response body is not plain data')
            headers = dict(response.headers._data)
            if any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in headers.items()):
                raise LoadFailure('worker response headers are not plain data')
            payload = NetworkResponse(
                url=str(response.url), status=int(response.status),
                status_text=str(response.status_text), headers=headers,
                body=body, redirected=bool(response.redirected), type=str(response.type))
        except BaseException as error:
            self._post_result(handle, None, LoadFailure(str(error)))
        else:
            self._post_result(handle, payload, None)

    def _post_result(self, handle, payload, error):
        if handle.done:
            return
        try:
            ticket = self.worker.tasks.post_task(
                partial(self._deliver, handle, payload, error), TaskSource.NETWORKING,
                label='Worker fetch response')
        except BaseException:
            handle.cancel()
            raise
        if ticket is None:
            handle.cancel()

    def _deliver(self, handle, payload, error):
        callback = self._callbacks.pop(handle, None)
        if callback is None or not handle.finish():
            return
        response = None
        if error is None:
            try:
                response = Response(payload.body, {
                    'url': payload.url, 'status': payload.status, 'statusText': payload.status_text,
                    'headers': payload.headers, 'redirected': payload.redirected, 'type': payload.type,
                }, services=self.worker.services)
            except BaseException as exc:
                error = exc
        callback(response, error)
