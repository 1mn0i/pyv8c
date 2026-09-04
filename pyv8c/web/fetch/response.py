"""Fetch response model."""

import json

from pyv8.flag import FlagExposed
from pyv8.tools import ConstructorConfig

from ...engine.registry import interface, js_attributes, js_methods
from .headers import Headers


@interface("Response", None, constructible=True, length=1)
@ConstructorConfig(exposed=FlagExposed.kYes, constructor=1, has_constructor=1)
class Response:
    __v8_attribute__ = js_attributes(
        ("status", "get_status", None, 0, 1), ("statusText", "get_statusText", None, 0, 1),
        ("ok", "get_ok", None, 0, 1), ("url", "get_url", None, 0, 1),
        ("redirected", "get_redirected", None, 0, 1), ("type", "get_type", None, 0, 1),
        ("headers", "get_headers", None, 0, 1), ("bodyUsed", "get_bodyUsed", None, 0, 1),
    )
    __v8_method__ = js_methods(
        ("text", "fn_text", 0, 1), ("json", "fn_json", 0, 1),
        ("arrayBuffer", "fn_arrayBuffer", 0, 1),
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
