"""``URL`` and the live ``URLSearchParams`` view it hands out.

The two are coupled: ``URL.searchParams`` returns a ``URLSearchParams`` bound to
its owner through ``_url``, which writes every mutation back with
``_set_search_from_params``.
"""

from urllib.parse import urljoin, urlsplit, urlunsplit

import v8n
from v8n import AttributeSpec, IdlSpec, MethodSpec
from v8n.flag import FlagCrossOriginCheck, FlagLocation, FlagReceiverCheck

from ...engine.features import interface
from ...foundation.arguments import (
    MISSING,
    dictionary,
    dom_string,
    interface_value,
    is_nullish,
    missing_error,
    sequence,
    usv_string,
)
from ...foundation.errors import InvalidStateError, V8TypeError, V8URIError
from ...foundation.urls import SPECIAL_SCHEMES
from ..fetch.blob import Blob

__all__ = ["URL", "URLSearchParams"]


@interface('URLSearchParams', None)
class URLSearchParams:
    __v8_idl_spec__ = IdlSpec(
        methods=(
            MethodSpec(name="append", callback="fn_append", length=2),
            MethodSpec(name="delete", callback="fn_delete", length=1),
            MethodSpec(name="get", callback="fn_get", length=1, side_effect=1),
            MethodSpec(name="getAll", callback="fn_getAll", length=1, side_effect=1),
            MethodSpec(name="has", callback="fn_has", length=1, side_effect=1),
            MethodSpec(name="set", callback="fn_set", length=2),
            MethodSpec(name="sort", callback="fn_sort"),
            MethodSpec(name="toString", callback="fn_toString", side_effect=1),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )

    def __init__(self, init='', *_extra, _url=None, **_):
        if init is v8n.Undefined:
            init = ''
        from urllib.parse import parse_qsl
        self._url = _url
        iterable = isinstance(init, (list, tuple))
        iterator = MISSING
        if isinstance(init, v8n.Value):
            iterator = init.iterator()
            iterable = not is_nullish(iterator)
        if iterable:
            def convert_pair(entry):
                pair = sequence(entry, usv_string)
                if len(pair) != 2:
                    raise V8TypeError("Failed to construct 'URLSearchParams': Sequence initializer must only contain pair elements.")
                return tuple(pair)
            self._items = sequence(init, convert_pair, iterator_method=iterator)
        elif isinstance(init, (dict, v8n.Value)):
            self._items = [(usv_string(key), usv_string(value)) for key, value in dictionary(init).items()]
        else:
            self._items = list(parse_qsl(usv_string(init).lstrip("?"), keep_blank_values=True))

    def _refresh(self):
        if self._url is not None:
            from urllib.parse import parse_qsl
            self._items = list(parse_qsl(urlsplit(self._url.href).query, keep_blank_values=True))

    def _commit(self):
        if self._url is not None:
            from urllib.parse import urlencode
            self._url._set_search_from_params(urlencode(self._items))

    def fn_append(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'append', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); self._items.append((str(name), str(value))); self._commit()

    def fn_delete(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'delete', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); self._items = [(k, v) for k, v in self._items if k != name]; self._commit()

    def fn_get(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'get', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name)
        for key, value in self._items:
            if key == name: return value
        return v8n.Null

    def fn_getAll(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'getAll', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); return [value for key, value in self._items if key == name]

    def fn_has(self, name=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'has', 1, 0, constructor=False)
        name = dom_string(name)
        self._refresh(); name = str(name); return any(key == name for key, _ in self._items)

    def fn_set(self, name=MISSING, value=MISSING, *_extra):
        if name is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 0, constructor=False)
        if value is MISSING:
            raise missing_error('URLSearchParams', 'set', 2, 1, constructor=False)
        name = dom_string(name)
        value = dom_string(value)
        self._refresh(); name, value = str(name), str(value)
        replaced = False; result = []
        for key, old_value in self._items:
            if key == name:
                if not replaced: result.append((name, value)); replaced = True
            else: result.append((key, old_value))
        if not replaced: result.append((name, value))
        self._items = result; self._commit()

    def fn_sort(self, *_extra):
        self._refresh(); self._items.sort(key=lambda item: item[0]); self._commit()

    def fn_toString(self, *_extra):
        from urllib.parse import urlencode
        self._refresh()
        return urlencode(self._items)


@interface('URL', None)
class URL:
    def __init__(self, url=MISSING, base=v8n.Undefined, *_extra, _realm=None, **_):
        if url is MISSING:
            raise missing_error('URL', 'constructor', 1, 0, constructor=True)
        url = usv_string(url)
        base = None if base is v8n.Undefined else usv_string(base)
        self._realm = _realm
        try:
            if base is not None:
                base = self._resolve(base)
            self.href = self._resolve(url, base)
        except V8TypeError as exc:
            raise V8TypeError("Failed to construct 'URL': Invalid URL") from exc
        self._search_params = None

    @staticmethod
    def _resolve(url, base=None):
        raw = str(url)
        if not raw or any(ch.isspace() for ch in raw):
            raise V8TypeError("Invalid URL")
        value = raw if base is None else urljoin(str(base), raw)
        try:
            parts = urlsplit(value)
            if not parts.scheme or (parts.scheme in SPECIAL_SCHEMES and not parts.netloc):
                raise V8URIError("Invalid URL")
            # Reading the port is what rejects a URL whose port cannot be one.
            _ = parts.port
        except (ValueError, V8URIError) as exc:
            raise V8TypeError("Invalid URL") from exc
        return urlunsplit(parts)

    def _parts(self): return urlsplit(self.href)
    def _replace(self, *, scheme=None, netloc=None, path=None, query=None, fragment=None):
        parts = self._parts()
        self.href = urlunsplit((scheme if scheme is not None else parts.scheme,
                                netloc if netloc is not None else parts.netloc,
                                path if path is not None else parts.path,
                                query if query is not None else parts.query,
                                fragment if fragment is not None else parts.fragment))

    def _set_search_from_params(self, query): self._replace(query=str(query))
    def get_href(self): return self.href
    def set_href(self, value):
        value = dom_string(value)
        self.href = self._resolve(value, self.href)
    def get_origin(self):
        parts = self._parts()
        if parts.scheme not in SPECIAL_SCHEMES or not parts.netloc:
            return "null"
        host = parts.hostname or ""
        if parts.port is not None:
            host += ":" + str(parts.port)
        return f"{parts.scheme}://{host}"
    def get_protocol(self):
        scheme = self._parts().scheme
        return scheme + ":" if scheme else ""
    def set_protocol(self, value):
        value = dom_string(value)
        self._replace(scheme=str(value).rstrip(':'))
    def get_username(self): return self._parts().username or ""
    def set_username(self, value):
        value = dom_string(value)
        self._set_credentials(username=str(value), password=self.get_password())
    def get_password(self): return self._parts().password or ""
    def set_password(self, value):
        value = dom_string(value)
        self._set_credentials(username=self.get_username(), password=str(value))
    def _set_credentials(self, *, username, password):
        parts = self._parts(); host = parts.hostname or ""
        if parts.port is not None: host += ":" + str(parts.port)
        credentials = (username + (":" + password if password else "") + "@") if username or password else ""
        self._replace(netloc=credentials + host)
    def get_host(self): return self._parts().netloc.rsplit("@", 1)[-1]
    def set_host(self, value):
        value = dom_string(value)
        self._replace(netloc=str(value))
    def get_hostname(self): return self._parts().hostname or ""
    def set_hostname(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        port = ":" + str(parts.port) if parts.port is not None else ""
        self._replace(netloc=credentials + str(value) + port)
    def get_port(self): return str(self._parts().port or "")
    def set_port(self, value):
        value = dom_string(value)
        parts = self._parts(); credentials = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
        self._replace(netloc=credentials + (parts.hostname or "") + ((":" + str(value)) if str(value) else ""))
    def get_pathname(self):
        path = self._parts().path
        return path or ("/" if self._parts().netloc else "")
    def set_pathname(self, value):
        value = dom_string(value)
        self._replace(path=str(value) or '/')
    def get_search(self):
        query = self._parts().query
        return ("?" + query) if query else ""
    def set_search(self, value):
        value = dom_string(value)
        self._replace(query=str(value).lstrip('?'))
    def get_hash(self):
        fragment = self._parts().fragment
        return ("#" + fragment) if fragment else ""
    def set_hash(self, value):
        value = dom_string(value)
        self._replace(fragment=str(value).lstrip('#'))
    def get_searchParams(self):
        if self._search_params is None:
            self._search_params = URLSearchParams(self.get_search(), _url=self)
        return self._search_params
    def fn_toString(self, *_extra):
        return self.href
    def fn_toJSON(self, *_extra):
        return self.href

    @staticmethod
    def fn_canParse(url=MISSING, base=v8n.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'canParse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not v8n.Undefined:
            base = dom_string(base)
        try: URL(url, base)
        except (V8URIError, TypeError, ValueError): return False
        return True

    @staticmethod
    def fn_parse(url=MISSING, base=v8n.Undefined, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'parse', 1, 0, constructor=False)
        url = dom_string(url)
        if base is not v8n.Undefined:
            base = dom_string(base)
        try: return URL(url, base)
        except (V8URIError, TypeError, ValueError): return v8n.Null

    @staticmethod
    def fn_createObjectURL(_realm, blob=MISSING, *_extra):
        if blob is MISSING:
            raise missing_error('URL', 'createObjectURL', 1, 0, constructor=False)
        blob = interface_value(blob, 'Blob', 'URL', 'createObjectURL', 1, nullable=False)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        if not isinstance(blob, Blob): raise V8TypeError("The object is not a Blob")
        return services.context_services.create_blob_url(blob, realm=services.realm_services)

    @staticmethod
    def fn_revokeObjectURL(_realm, url=MISSING, *_extra):
        if url is MISSING:
            raise missing_error('URL', 'revokeObjectURL', 1, 0, constructor=False)
        url = dom_string(url)
        services = _realm
        if services is None or services.realm_services.closed: raise InvalidStateError("The execution context is no longer active")
        services.context_services.revoke_blob_url(str(url))

    __v8_idl_spec__ = IdlSpec(
        attributes=(
            AttributeSpec(name="href", getter="get_href", setter="set_href"),
            AttributeSpec(name="origin", getter="get_origin", side_effect=1),
            AttributeSpec(name="protocol", getter="get_protocol", setter="set_protocol"),
            AttributeSpec(name="username", getter="get_username", setter="set_username"),
            AttributeSpec(name="password", getter="get_password", setter="set_password"),
            AttributeSpec(name="host", getter="get_host", setter="set_host"),
            AttributeSpec(name="hostname", getter="get_hostname", setter="set_hostname"),
            AttributeSpec(name="port", getter="get_port", setter="set_port"),
            AttributeSpec(name="pathname", getter="get_pathname", setter="set_pathname"),
            AttributeSpec(name="search", getter="get_search", setter="set_search"),
            AttributeSpec(name="searchParams", getter="get_searchParams", side_effect=1),
            AttributeSpec(name="hash", getter="get_hash", setter="set_hash"),
        ),
        methods=(
            MethodSpec(name="toString", callback="fn_toString", side_effect=1),
            MethodSpec(name="toJSON", callback="fn_toJSON", side_effect=1),
            MethodSpec(name="canParse", callback="fn_canParse", length=1, side_effect=1, location=FlagLocation.kInterface, receiver_check=FlagReceiverCheck.kDoNotCheck, cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
            MethodSpec(name="parse", callback="fn_parse", length=1, side_effect=1, location=FlagLocation.kInterface, receiver_check=FlagReceiverCheck.kDoNotCheck, cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
            MethodSpec(name="createObjectURL", callback="fn_createObjectURL", length=1, location=FlagLocation.kInterface, receiver_check=FlagReceiverCheck.kDoNotCheck, cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
            MethodSpec(name="revokeObjectURL", callback="fn_revokeObjectURL", length=1, location=FlagLocation.kInterface, receiver_check=FlagReceiverCheck.kDoNotCheck, cross_origin_check=FlagCrossOriginCheck.kDoNotCheck),
        ),
        constructor_behavior=1,
        length=1,
        has_constructor=1,
    )
