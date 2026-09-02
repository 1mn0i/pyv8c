"""Small Chrome DevTools Protocol bridge for pyv8c realms."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from urllib.parse import quote

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

import pyv8

class DevToolsTarget:
    """A debuggable page, iframe, or worker realm."""

    def __init__(self, server, realm, kind="page", parent=None, *, worker_runtime=None):
        self.server = server
        self.realm = realm
        self.kind = kind
        self.parent = parent
        self.worker_runtime = worker_runtime
        self.target_id = uuid.uuid4().hex
        self.sessions = set()
        self.debugger = None
        self.closed = False
        if worker_runtime is None:
            self._create_debugger()

    @property
    def url(self):
        if self.kind == "worker":
            return str(getattr(self.realm, "script_url", "about:blank"))
        return str(getattr(self.realm, "url", "about:blank"))

    @property
    def title(self):
        return self.url

    def descriptor(self):
        return {
            "targetId": self.target_id,
            "type": "worker" if self.kind == "worker" else "page",
            "title": self.title,
            "url": self.url,
            "attached": bool(self.sessions),
            "canAccessOpener": False,
            "browserContextId": "pyv8c",
        }

    def _create_debugger(self):
        target = self

        class Inspector(pyv8.Debugger):
            def __init__(self, context):
                super().__init__(context)

            def handle(self, message):
                target.server.emit_from_target(target, message)

        context = getattr(self.realm, "context", None) or getattr(self.realm, "_context", None)
        if context is not None:
            self.debugger = Inspector(context)

    def send(self, message):
        if self.closed:
            return
        if self.worker_runtime is not None:
            self.worker_runtime.enqueue_debugger(message, self)
        elif self.debugger is not None:
            try:
                self.debugger.send(message.encode("utf-8"))
            except BaseException as exc:
                self.server._send({"method": "Runtime.exceptionThrown", "params": {"exceptionDetails": {"text": str(exc)}}})

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.sessions.clear()
        debugger, self.debugger = self.debugger, None
        if debugger is not None:
            if self.worker_runtime is not None:
                self.worker_runtime.enqueue_debugger_close(debugger)
            else:
                del debugger


class DevToolsServer:
    """Single-client WebSocket server and Target/session router."""

    def __init__(self, context, page, host, port):
        self.context = context
        self.root_page = page
        self.host = host
        self.requested_port = int(port)
        self.port = None
        self.targets = {}
        self._target_by_realm = {}
        self._sessions = {}
        self._lock = threading.RLock()
        self._loop = None
        self._server = None
        self._websocket = None
        self._outgoing = None
        self._started = threading.Event()
        self._shutdown = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, name="pyv8c-devtools", daemon=True)

    def start(self):
        self._thread.start()
        if not self._started.wait(5):
            self.close()
            raise RuntimeError("timed out starting devtools server")
        if self._error:
            raise RuntimeError("failed to start devtools server") from self._error

    def _run(self):
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = exc
            self._started.set()
        finally:
            self._loop = None

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        async with serve(self._handle, self.host, self.requested_port) as server:
            self._server = server
            self.port = server.sockets[0].getsockname()[1]
            self._started.set()
            await self._loop.run_in_executor(None, self._shutdown.wait)

    async def _handle(self, websocket):
        with self._lock:
            if self._websocket is not None:
                await websocket.close(code=1013, reason="debugger already connected")
                return
            self._websocket = websocket
            self._outgoing = asyncio.Queue()
        sender = asyncio.create_task(self._sender(websocket, self._outgoing))
        try:
            async for message in websocket:
                self._dispatch(message)
        finally:
            sender.cancel()
            with self._lock:
                if self._websocket is websocket:
                    self._websocket = None
                    self._outgoing = None
            for session_id, target in list(self._sessions.items()):
                target.sessions.discard(session_id)
            self._sessions.clear()

    async def _sender(self, websocket, outgoing):
        try:
            while True:
                message = await outgoing.get()
                await websocket.send(message)
        except (asyncio.CancelledError, ConnectionClosed):
            return

    def _send(self, payload):
        with self._lock:
            loop, outgoing = self._loop, self._outgoing
        if loop is not None and outgoing is not None:
            message = json.dumps(payload, separators=(",", ":"))
            if threading.current_thread() is self._thread:
                outgoing.put_nowait(message)
            else:
                asyncio.run_coroutine_threadsafe(outgoing.put(message), loop)

    def emit_from_target(self, target, message):
        try:
            payload = json.loads(message)
        except (TypeError, ValueError):
            return
        matched = False
        for session_id, bound in list(self._sessions.items()):
            if bound is target:
                matched = True
                payload["sessionId"] = session_id
                self._send(payload)
        if not matched:
            self._send(payload)

    def register_page(self, page, kind="page", parent=None):
        with self._lock:
            existing = self._target_by_realm.get(page)
            if existing and not existing.closed:
                return existing
            target = DevToolsTarget(self, page, kind, parent)
            self.targets[target.target_id] = target
            self._target_by_realm[page] = target
        self._send({"method": "Target.targetCreated", "params": {"targetInfo": target.descriptor()}})
        return target

    def register_worker(self, runtime):
        with self._lock:
            target = DevToolsTarget(self, runtime, "worker", self._target_by_realm.get(runtime.page), worker_runtime=runtime)
            self.targets[target.target_id] = target
            self._target_by_realm[runtime] = target
            runtime.debug_target = target
            runtime.enqueue_debugger_init(target)
        self._send({"method": "Target.targetCreated", "params": {"targetInfo": target.descriptor()}})
        return target

    def unregister(self, realm):
        with self._lock:
            target = self._target_by_realm.pop(realm, None)
            if target is None:
                return
            self.targets.pop(target.target_id, None)
            session_ids = [sid for sid, value in self._sessions.items() if value is target]
            for sid in session_ids:
                self._sessions.pop(sid, None)
        target.close()
        self._send({"method": "Target.targetDestroyed", "params": {"targetId": target.target_id}})

    def _reply(self, request, result=None, error=None, session_id=None):
        payload = {"id": request.get("id")}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result or {}
        if session_id:
            payload["sessionId"] = session_id
        self._send(payload)

    def _dispatch(self, raw):
        try:
            request = json.loads(raw)
        except (TypeError, ValueError):
            return
        method = request.get("method", "")
        params = request.get("params") or {}
        session_id = request.get("sessionId")
        if method == "Target.getTargets":
            self._reply(request, {"targetInfos": [t.descriptor() for t in self.targets.values() if not t.closed]})
            return
        if method == "Target.setDiscoverTargets":
            if params.get("discover", False):
                for target in self.targets.values():
                    if not target.closed:
                        self._send({"method": "Target.targetCreated", "params": {"targetInfo": target.descriptor()}})
            self._reply(request, {})
            return
        if method == "Target.setAutoAttach":
            self._reply(request, {})
            return
        if method == "Target.attachToTarget":
            target = self.targets.get(params.get("targetId"))
            if target is None:
                self._reply(request, error={"code": -32000, "message": "No such target"})
                return
            sid = uuid.uuid4().hex
            self._sessions[sid] = target
            target.sessions.add(sid)
            self._reply(request, {"sessionId": sid})
            self._send({"method": "Target.attachedToTarget", "params": {"sessionId": sid, "targetInfo": target.descriptor(), "waitingForDebugger": False}})
            return
        if method == "Target.detachFromTarget":
            sid = params.get("sessionId") or session_id
            target = self._sessions.pop(sid, None)
            if target:
                target.sessions.discard(sid)
                self._send({"method": "Target.detachedFromTarget", "params": {"sessionId": sid, "targetId": target.target_id}})
            self._reply(request, {})
            return
        if method == "Target.activateTarget":
            self._reply(request, {})
            return
        if method == "Target.closeTarget":
            target = self.targets.get(params.get("targetId"))
            if target and target.realm is not self.root_page:
                close = getattr(target.realm, "close", None) or getattr(target.realm, "fn_terminate", None)
                if close:
                    close()
            self._reply(request, {"success": bool(target)})
            return
        target = self._sessions.get(session_id) if session_id else self._target_by_realm.get(self.root_page)
        if target is None:
            self._reply(request, error={"code": -32000, "message": "Target not found"}, session_id=session_id)
            return
        if method.startswith("Runtime.") or method.startswith("Debugger."):
            inspector_request = dict(request)
            inspector_request.pop("sessionId", None)
            target.send(json.dumps(inspector_request, separators=(",", ":")))
            return
        self._reply(request, error={"code": -32601, "message": "Method not found"}, session_id=session_id)

    def close(self):
        if self._shutdown.is_set():
            return
        self._shutdown.set()
        for target in list(self.targets.values()):
            target.close()
        self.targets.clear()
        self._target_by_realm.clear()
        self._sessions.clear()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
        if getattr(self.context, "_devtools", None) is self:
            self.context._devtools = None

    def target_info_changed(self, realm):
        target = self._target_by_realm.get(realm)
        if target is not None and not target.closed:
            self._send({"method": "Target.targetInfoChanged", "params": {"targetInfo": target.descriptor()}})


class DevToolsHandle:
    def __init__(self, server):
        self._server = server
        self.host = server.host
        self.port = server.port
        self.url = "devtools://devtools/bundled/inspector.html?experiments=true&v8only=true&ws={}:{}".format(quote(self.host, safe="."), self.port)

    def close(self):
        self._server.close()

    def wait(self, timeout=None):
        self._server._thread.join(timeout)


def start_devtools(context, page, *, host="127.0.0.1", port=0):
    server = DevToolsServer(context, page, host, port)
    server.start()
    page._devtools_target = server.register_page(page)
    return DevToolsHandle(server)


__all__ = ["DevToolsHandle", "DevToolsTarget", "DevToolsServer", "start_devtools"]
