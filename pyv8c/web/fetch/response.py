"""Fetch response model."""
from pyv8 import IdlSpec, AttributeSpec, MethodSpec

import json




from ...engine.registry import interface
from .headers import Headers


@interface('Response', None)
class Response:
    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="status", getter="get_status", side_effect=1),
            AttributeSpec(name="statusText", getter="get_statusText", side_effect=1),
            AttributeSpec(name="ok", getter="get_ok", side_effect=1),
            AttributeSpec(name="url", getter="get_url", side_effect=1),
            AttributeSpec(name="redirected", getter="get_redirected", side_effect=1),
            AttributeSpec(name="type", getter="get_type", side_effect=1),
            AttributeSpec(name="headers", getter="get_headers", side_effect=1),
            AttributeSpec(name="bodyUsed", getter="get_bodyUsed", side_effect=1),
        ),
        methods=(
            MethodSpec(name="text", callback="fn_text", side_effect=1),
            MethodSpec(name="json", callback="fn_json", side_effect=1),
            MethodSpec(name="arrayBuffer", callback="fn_arrayBuffer", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, body="", options=None, services=None, _realm=None, **_):
        options = options if isinstance(options, dict) else {}
        self._services = services or _realm
        self.body = body
        self.status = int(options.get("status", 200))
        self.status_text = str(options.get("statusText", options.get("reason", "")))
        self.url = str(options.get("url", ""))
        self.headers = Headers(options.get("headers"))
        self.redirected = bool(options.get("redirected", False))
        self.type = str(options.get("type", "basic"))
        self.body_used = False

    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_ok(self): return 200 <= self.status < 300
    def get_url(self): return self.url
    def get_redirected(self): return self.redirected
    def get_type(self): return self.type
    def get_headers(self): return self.headers
    def get_bodyUsed(self): return self.body_used

    def body_text(self):
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else str(self.body)

    def _body_value(self):
        self.body_used = True
        return self.body_text()

    def _result(self, value):
        return self._services.realm_services.promise_resolve(value) if self._services else value

    def fn_text(self): return self._result(self._body_value())
    def fn_json(self): return self._result(json.loads(self._body_value()))
    def fn_arrayBuffer(self):
        return self._result(self.body if isinstance(self.body, bytes) else self._body_value().encode())


__all__ = ["Response"]
