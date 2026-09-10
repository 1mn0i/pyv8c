"""CDP target/session routing, discovery and connection generations."""

import json
import threading
import uuid
from urllib.parse import quote

from pyv8 import _pyv8
from .requests import Requests
from .session import Session, Target


class ProtocolError(Exception):
    def __init__(self, message, code=-32602):
        super().__init__(message)
        self.code = code


class Router:
    def __init__(self, options):
        self.options = options
        self.server_id = uuid.uuid4().hex
        self.targets = {}
        self.sessions = {}
        self.requests = Requests()
        self.lock = threading.RLock()
        self.transport = None
        self.root = None
        self.generation = None
        self.discover = False
        self.closed = False
        self.cleanup = set()
        self.diagnostics = []

    def register(self, host, parent=None, *, started=False):
        target = Target(host, self.options, parent)
        target.started = started
        with self.lock:
            if self.closed:
                raise RuntimeError("DevTools service is closed")
            self.targets[target.target_id] = target
            if self.root is None:
                self.root = target
            if self.generation is not None:
                if self.discover:
                    self.event("Target.targetCreated", {"targetInfo": target.descriptor()})
                for session in tuple(self.sessions.values()):
                    if session.auto_attach and target.parent is session.target:
                        self._attach(target, session, automatic=True, waiting=session.wait_children)
        return target

    def unregister(self, target):
        with self.lock:
            if target.closed:
                return
            for child in tuple(self.targets.values()):
                if child.parent is target:
                    self.unregister(child)
            for session in tuple(target.sessions):
                self._detach(session)
            self.targets.pop(target.target_id, None)
            for future in target.close():
                self.track_cleanup(future)
            self.event("Target.targetDestroyed", {"targetId": target.target_id})

    def connect(self, path, generation):
        with self.lock:
            self.generation = generation
            self.discover = False
            for registered in self.targets.values():
                registered.disconnected = False
            target = self.targets.get(path.rsplit("/", 1)[-1], self.root)
            self.sessions[None] = Session(self, target, None, generation)

    def disconnect(self, generation):
        with self.lock:
            if self.generation != generation:
                return
            self.generation = None
            for session in tuple(self.sessions.values()):
                future = session.close()
                if future is not None:
                    self.track_cleanup(future)
            self.sessions.clear()
            for target in self.targets.values():
                target.release()

    def event(self, method, params, session=None):
        if self.generation is None:
            return
        payload = {"method": method, "params": params}
        if session is not None and session.session_id is not None:
            payload["sessionId"] = session.session_id
        self.transport.send(payload, self.generation)

    def reply(self, request, generation, result=None, error=None):
        payload = {"id": request.get("id")}
        if "sessionId" in request:
            payload["sessionId"] = request["sessionId"]
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = {} if result is None else result
        self.transport.send(payload, generation)

    def receive(self, session, payload):
        with self.lock:
            if session.closed or session.generation != self.generation:
                return
            if "id" in payload:
                request = self.requests.resolve(session, payload)
                if request is None:
                    return
                if "error" not in payload:
                    if request.method == "Debugger.enable":
                        session.enabled = True
                    elif request.method == "Debugger.disable":
                        session.enabled = False
            if session.session_id is not None:
                payload["sessionId"] = session.session_id
            self.transport.send(payload, session.generation)

    def _attach(self, target, parent, automatic=False, waiting=False):
        if automatic:
            existing = next((entry for entry in target.sessions if entry.parent is parent and entry.automatic), None)
            if existing is not None:
                return existing
        session_id = uuid.uuid4().hex
        session = Session(self, target, session_id, self.generation, parent, automatic)
        self.sessions[session_id] = session
        if waiting and not target.started and not target.ready.is_set():
            target.waiting = True
        self.event("Target.attachedToTarget", {
            "sessionId": session_id, "targetInfo": target.descriptor(),
            "waitingForDebugger": target.waiting,
        }, parent)
        return session

    def _detach(self, session):
        for child in tuple(self.sessions.values()):
            if child.parent is session:
                self._detach(child)
        self.sessions.pop(session.session_id, None)
        future = session.close()
        if future is not None:
            self.track_cleanup(future)
        if session.session_id is not None:
            self.event("Target.detachedFromTarget", {
                "sessionId": session.session_id, "targetId": session.target.target_id,
            }, session.parent)

    def dispatch(self, raw, generation):
        request = {}
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                request = {}
                raise ProtocolError("Request must be an object", -32600)
            if type(request.get("id")) is not int or not isinstance(request.get("method"), str):
                raise ProtocolError("Request requires integer id and method", -32600)
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ProtocolError("params must be an object")
            with self.lock:
                if generation != self.generation or self.closed:
                    return
                session = self.sessions.get(request.get("sessionId"))
                if session is None or session.target.closed:
                    raise ProtocolError("Session or target is closed", -32000)
                method = request["method"]
                if method.startswith("Target."):
                    result = self._target_command(method, params, session)
                    self.reply(request, generation, result)
                    return
                if not method.startswith(("Runtime.", "Debugger.")):
                    raise ProtocolError("Method not found", -32601)
                forwarded = dict(request)
                forwarded.pop("sessionId", None)
                forwarded = self.requests.add(session, forwarded)
                future = session.target.submit(lambda: session.dispatch(forwarded))

                def failed(done):
                    if done.cancelled():
                        return
                    error = done.exception()
                    if error is not None:
                        self.receive(session, {"id": forwarded["id"], "error": {
                            "code": -32000, "message": str(error)}})
                future.add_done_callback(failed)
        except (ValueError, UnicodeError):
            self.reply({}, generation, error={"code": -32700, "message": "Invalid JSON"})
        except ProtocolError as error:
            self.reply(request, generation, error={"code": error.code, "message": str(error)})

    def _target_command(self, method, params, session):
        if method == "Target.getTargets":
            return {"targetInfos": [target.descriptor() for target in self.targets.values()]}
        if method == "Target.getTargetInfo":
            target = self.targets.get(params["targetId"]) if "targetId" in params else session.target
            if target is None:
                raise ProtocolError("Target not found", -32000)
            return {"targetInfo": target.descriptor()}
        if method == "Target.setDiscoverTargets":
            self.discover = bool(params.get("discover"))
            if self.discover:
                for target in self.targets.values():
                    self.event("Target.targetCreated", {"targetInfo": target.descriptor()}, session)
            return {}
        if method == "Target.setAutoAttach":
            if not params.get("flatten", False):
                raise ProtocolError("Only flattened sessions are supported")
            session.auto_attach = bool(params.get("autoAttach"))
            session.wait_children = bool(params.get("waitForDebuggerOnStart"))
            if session.auto_attach:
                for target in self.targets.values():
                    if target.parent is session.target:
                        self._attach(target, session, automatic=True)
            else:
                for child in tuple(self.sessions.values()):
                    if child.parent is session and child.automatic:
                        self._detach(child)
            return {}
        if method == "Target.attachToTarget":
            target = self.targets.get(params.get("targetId"))
            if target is None:
                raise ProtocolError("Target not found", -32000)
            if params.get("flatten", True) is not True:
                raise ProtocolError("Only flattened sessions are supported")
            return {"sessionId": self._attach(target, session).session_id}
        if method == "Target.detachFromTarget":
            child = self.sessions.get(params.get("sessionId"))
            if child is None or child.parent is not session:
                raise ProtocolError("Session not found", -32000)
            self._detach(child)
            return {}
        if method == "Target.activateTarget":
            return {}
        if method == "Target.closeTarget":
            target = self.targets.get(params.get("targetId"))
            if target is None:
                return {"success": False}
            target.wake_waiting_owner()
            self.track_cleanup(target.mailbox.submit(target, target.host.close, during_pause=False))
            return {"success": True}
        raise ProtocolError("Method not found", -32601)

    def websocket_url(self, target=None):
        host = self.options.host
        if ":" in host:
            host = f"[{host}]"
        path = f"/devtools/page/{target.target_id}" if target else f"/devtools/browser/{self.server_id}"
        return f"ws://{host}:{self.transport.port}{path}"

    def frontend_url(self, target):
        endpoint = self.websocket_url(target).removeprefix("ws://")
        return "devtools://devtools/bundled/inspector.html?experiments=true&ws=" + quote(endpoint, safe="/:[]")

    def valid_path(self, path):
        with self.lock:
            return path in ("/", f"/devtools/browser/{self.server_id}") or any(
                path == f"/devtools/page/{target_id}" for target_id in self.targets)

    def discovery(self, path):
        with self.lock:
            if path in ("/json", "/json/list"):
                return [{"id": target.target_id, "type": target.host.kind,
                         "title": target.host.url(), "url": target.host.url(),
                         "webSocketDebuggerUrl": self.websocket_url(target),
                         "devtoolsFrontendUrl": self.frontend_url(target)}
                        for target in self.targets.values()]
            if path == "/json/version":
                return {"Browser": "pyv8", "Protocol-Version": "1.3",
                        "V8-Version": str(_pyv8.v8_version()),
                        "webSocketDebuggerUrl": self.websocket_url()}
        return None

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.disconnect(self.generation)
            for target in tuple(self.targets.values()):
                for future in target.close():
                    self.track_cleanup(future)
            self.targets.clear()

    def track_cleanup(self, future):
        with self.lock:
            self.cleanup.add(future)

        def completed(done):
            with self.lock:
                self.cleanup.discard(done)
                if done.cancelled():
                    self.diagnostics.append("Inspector cleanup was cancelled")
                elif done.exception() is not None:
                    self.diagnostics.append(str(done.exception()))
        future.add_done_callback(completed)

    def finish_cleanup(self):
        with self.lock:
            pending = tuple(self.cleanup)
        for future in pending:
            try:
                future.result(timeout=5)
            except TimeoutError:
                if not future.done():
                    self.diagnostics.append("Inspector cleanup did not finish on its owner thread")
            except BaseException:
                pass
        if self.diagnostics:
            raise RuntimeError("DevTools cleanup failed: " + "; ".join(self.diagnostics))
