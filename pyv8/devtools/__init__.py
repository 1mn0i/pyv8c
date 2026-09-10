"""Shared DevTools service for embedded Contexts and browser hosts."""

import threading
from typing import Callable, TypeVar

from pyv8._pyv8 import Context
from .options import DevToolsOptions
from .router import Router
from .scheduler import CallingThread, Host
from .transport import Transport

__all__ = ["DevToolsOptions", "DevToolsHandle", "start_devtools"]

_Result = TypeVar("_Result")


class DevToolsHandle:
    def __init__(self, router, root, standalone=False, on_close=None):
        self._router = router
        self._root = root
        self._standalone = standalone
        self._on_close = on_close
        self._closed = False
        self._close_lock = threading.Lock()

    @property
    def host(self) -> str:
        return self._router.transport.host

    @property
    def port(self) -> int:
        return self._router.transport.port

    @property
    def url(self) -> str:
        return self._router.frontend_url(self._root)

    def wait_for_connection(self, timeout: float | None = None) -> bool:
        return self._router.transport.wait_for_connection(timeout)

    def wait_for_ready(self, timeout: float | None = None) -> bool:
        return self._root.wait_for_ready(timeout, pump=self._standalone)

    def run(self, callback: Callable[[Context], _Result]) -> _Result:
        if not self._standalone:
            raise RuntimeError("Browser hosts already have an executor; use page.evaluate")
        if self._closed:
            raise RuntimeError("DevTools handle is closed")
        if not self._root.host.executor.is_owner_thread():
            raise RuntimeError("run must be called on the Context owner thread")
        if not self.wait_for_ready(self._router.options.startup_timeout):
            raise TimeoutError("DevTools did not become ready")
        self._root.before_script()
        try:
            return callback(self._root.host.context)
        finally:
            self._root.after_script()
            self._root.mailbox.drain(self._root)

    def close(self) -> None:
        if self._standalone and not self._root.host.executor.is_owner_thread():
            raise RuntimeError("Standalone DevTools must be closed on the Context owner thread")
        with self._close_lock:
            self._close()

    def _close(self):
        if self._closed:
            return
        self._router.close()
        for target in [self._root]:
            if target.host.executor.is_owner_thread():
                target.mailbox.drain(target)
        try:
            self._router.finish_cleanup()
        finally:
            try:
                self._router.transport.close()
            finally:
                self._closed = True
                if self._on_close is not None:
                    self._on_close()

    def __enter__(self) -> "DevToolsHandle":
        return self

    def __exit__(self, *_error):
        self.close()


def create_service(host, options=None, *, standalone=False, on_close=None):
    options = DevToolsOptions() if options is None else options
    if not isinstance(options, DevToolsOptions):
        raise TypeError("options must be DevToolsOptions")
    router = Router(options)
    root = router.register(host)
    router.transport = Transport(router, options)
    try:
        router.transport.start()
    except Exception:
        router.close()
        router.transport.close()
        raise
    return DevToolsHandle(router, root, standalone, on_close)


def start_devtools(context: Context, *, options: DevToolsOptions | None = None) -> DevToolsHandle:
    return create_service(Host(context, CallingThread(), lambda: "about:blank", context.close),
                          options, standalone=True)
