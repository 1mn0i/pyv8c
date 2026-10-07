"""Fetch response model."""
import json

import pyv8
from pyv8 import AttributeSpec, IdlSpec, MethodSpec

from ...engine.features import interface
from ...foundation.arguments import byte_string, dictionary, integer, is_nullish, usv_string
from ...foundation.errors import V8RangeError, V8SyntaxError, V8TypeError
from .blob import Blob
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
            MethodSpec(name="clone", callback="fn_clone", side_effect=1),
            #: Chrome's static ``Response.json`` is not declared here: the member table is keyed
            #: by name, so the duplicate would be rejected ("declares duplicate methods: json").
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, body=None, options=None, *_extra, services=None, _realm=None, **_):
        if body is pyv8.Undefined:
            body = None
        if isinstance(body, Blob):
            body = body._data
        elif not is_nullish(body) and not isinstance(body, bytes):
            body = usv_string(body)
        if options is pyv8.Undefined:
            options = None
        if not is_nullish(options):
            options = dictionary(options)
        if services is pyv8.Undefined:
            services = None
        options = options if isinstance(options, dict) else {}
        self._services = services or _realm
        self.body = body
        self.headers = Headers(options.get("headers"))
        self.status = integer(options.get("status", 200), bits=16)
        self.status_text = byte_string(options.get("statusText", options.get("reason", "")))
        if _realm is not None and not 200 <= self.status <= 599:
            raise V8RangeError("Failed to construct 'Response': The status provided (" + str(self.status) + ") is outside the range [200, 599].")
        if _realm is not None and self.status in (204, 205, 304) and not is_nullish(body):
            raise V8TypeError("Failed to construct 'Response': Response with null body status cannot have body")
        if any(character in self.status_text for character in "\r\n"):
            raise V8TypeError("Failed to construct 'Response': Invalid statusText")
        self.url = str(options.get("url", ""))
        self.redirected = bool(options.get("redirected", False))
        # A response a script builds is "default"; "basic" is what a same-origin
        # fetch returns, and "cors"/"opaque" belong to the network layer.
        self.type = str(options.get("type", "default"))
        self.body_used = False

    def get_status(self): return self.status
    def get_statusText(self): return self.status_text
    def get_ok(self): return 200 <= self.status < 300
    def get_url(self): return self.url
    def get_redirected(self): return self.redirected
    def get_type(self): return self.type
    def get_headers(self): return self.headers
    def get_bodyUsed(self): return self.body_used

    def fn_clone(self, *_extra):
        """A response with the same body, headers, status and type, usable on
        its own; neither side counts as consumed until something reads it.
        """
        if self.body_used:
            raise V8TypeError("Body has already been consumed.")
        return Response(self.body, {"status": self.status, "statusText": self.status_text,
                                    "headers": self.headers, "url": self.url,
                                    "redirected": self.redirected, "type": self.type},
                        services=self._services)

    def body_text(self):
        if is_nullish(self.body):
            return ""
        return self.body.decode("utf-8", "replace") if isinstance(self.body, bytes) else usv_string(self.body)

    def _body_value(self):
        if self.body_used:
            raise V8TypeError("Body has already been consumed.")
        self.body_used = True
        return self.body_text()

    def _result(self, value):
        return self._services.realm_services.promise_resolve(value) if self._services else value

    def fn_text(self, *_extra):
        if not self._services:
            return self._body_value()
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            resolve(self._body_value())
        except (V8TypeError, pyv8.JSException, UnicodeError) as exc:
            reject(self._services.realm_services.js_error_value(exc))
        return promise
    def fn_json(self, *_extra):
        if not self._services:
            return json.loads(self._body_value())
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            text = self._body_value()
            try:
                value = json.loads(text)
            except ValueError as exc:
                raise V8SyntaxError(str(exc)) from exc
            resolve(value)
        except (V8TypeError, V8SyntaxError, pyv8.JSException, UnicodeError) as exc:
            reject(self._services.realm_services.js_error_value(exc))
        return promise
    def fn_arrayBuffer(self, *_extra):
        if not self._services:
            return self._body_value().encode()
        promise, resolve, reject = self._services.realm_services.make_promise()
        try:
            text = self._body_value()
            resolve(self.body if isinstance(self.body, bytes) else text.encode())
        except (V8TypeError, pyv8.JSException, UnicodeError) as exc:
            reject(self._services.realm_services.js_error_value(exc))
        return promise


__all__ = ["Response"]
