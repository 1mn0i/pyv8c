"""Credential Management: the credential base and PublicKeyCredential.

The IDL baseline declares both interfaces but this runtime shipped no class at
all, so ``PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()``
answered ``pyv8.Undefined``.  Chrome 157 answers a Promise resolving to
``false`` on a machine without a platform authenticator
(``phase9_chrome_shape.json``), and the Kasada SDK reads that Promise.
"""
from __future__ import annotations

from pyv8c import pyv8

from ...foundation.arguments import dom_string
from ...features.runtime import FeatureImplementation
from ...idl import interface

__all__ = ["Credential", "PublicKeyCredential", "FEATURE"]


@interface('Credential', None)
class Credential:
    def __init__(self, id="", type="", **_):
        self._id = str(dom_string(id))
        self._type = str(dom_string(type))

    def get_id(self): return self._id
    def get_type(self): return self._type

    @staticmethod
    def fn_isConditionalMediationAvailable(_realm=None, *_extra):
        return pyv8.JSPromise(resolve=False)


@interface('PublicKeyCredential', 'Credential')
class PublicKeyCredential(Credential):
    def __init__(self, **kwargs):
        super().__init__(type="public-key", **kwargs)
        self._raw_id = pyv8.Null
        self._response = pyv8.Null
        self._authenticator_attachment = pyv8.Null

    def get_rawId(self): return self._raw_id
    def get_response(self): return self._response
    def get_authenticatorAttachment(self): return self._authenticator_attachment

    @staticmethod
    def fn_isUserVerifyingPlatformAuthenticatorAvailable(_realm=None, *_extra):
        """Promise<boolean>; false when no platform authenticator is present."""
        return pyv8.JSPromise(resolve=False)

    @staticmethod
    def fn_isConditionalMediationAvailable(_realm=None, *_extra):
        return pyv8.JSPromise(resolve=False)

    def fn_getClientExtensionResults(self, *_extra):
        return {}

    def fn_toJSON(self, *_extra):
        return {"id": self._id, "type": self._type,
                "rawId": self._raw_id, "response": self._response,
                "authenticatorAttachment": self._authenticator_attachment,
                "clientExtensionResults": {}}


FEATURE = FeatureImplementation({
    "Credential": Credential,
    "PublicKeyCredential": PublicKeyCredential,
})
