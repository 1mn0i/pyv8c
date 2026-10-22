"""Byte streams used by the CompressionStream and DecompressionStream APIs.

The compressor is incremental; readers receive Uint8Array chunks as the writer
produces them. A write that emits a chunk waits for the readable side to take it,
matching the transform stream's backpressure in Chromium.
"""

from collections import deque
import zlib

import pyv8

from ...engine.features import FeatureImplementation
from ...foundation.arguments import MISSING, dom_string, missing_error
from ...idl import interface
from ..page_errors import PageTypeError, js_error_value


_FORMATS = {'gzip': 31, 'deflate': 15, 'deflate-raw': -15}


def _promise(services):
    return services.realm_services.make_promise()


def _bytes(value):
    if value is MISSING:
        raise PageTypeError('A chunk must be a BufferSource.')
    try:
        if isinstance(value, pyv8.JSTypeArray):
            return bytes(value)
        if isinstance(value, pyv8.JSObject) and value._buffer_kind() is not None:
            return bytes(value)
        raise PageTypeError('A chunk must be a BufferSource.')
    except (TypeError, ValueError, AttributeError, IndexError):
        raise PageTypeError('A chunk must be a BufferSource.') from None


class _BytePipe:
    def __init__(self, fmt, decompress, services):
        self.services = services
        self.decompress = decompress
        self.codec = (zlib.decompressobj(_FORMATS[fmt]) if decompress
                      else zlib.compressobj(wbits=_FORMATS[fmt]))
        self.gzip_header = fmt == 'gzip' and not decompress
        self.queue = deque()
        self.readers = deque()
        self.finished = False
        self.failed = None

    def _fail(self, error):
        if isinstance(error, zlib.error):
            detail = str(error).split(': ', 1)[-1]
            error = PageTypeError(f'The compressed data was not valid: {detail}.')
        self.failed = error
        while self.queue:
            _chunk, (_resolve, reject) = self.queue.popleft()
            reject(js_error_value(error))
        while self.readers:
            _resolve, reject = self.readers.popleft()
            reject(js_error_value(error))
        return error

    def _emit(self, chunk, ack):
        if not chunk:
            ack[0](None)
            return
        if self.gzip_header and len(chunk) >= 10:
            # Chromium writes OS=10 (unknown) into the gzip header.
            chunk = chunk[:9] + b'\x0a' + chunk[10:]
            self.gzip_header = False
        item = (pyv8.JSTypeArray(list(chunk), pyv8.ArrayType.Uint8Array), ack)
        if self.readers:
            resolve, _reject = self.readers.popleft()
            resolve({'value': item[0], 'done': False})
            ack[0](None)
        else:
            self.queue.append(item)

    def read(self):
        promise, resolve, reject = _promise(self.services)
        if self.queue:
            value, ack = self.queue.popleft()
            resolve({'value': value, 'done': False})
            ack[0](None)
        elif self.failed is not None:
            reject(js_error_value(self.failed))
        elif self.finished:
            resolve({'value': pyv8.Undefined, 'done': True})
        else:
            self.readers.append((resolve, reject))
        return promise

    def write(self, value):
        promise, resolve, reject = _promise(self.services)
        if self.failed is not None or self.finished:
            reject(js_error_value(self.failed or PageTypeError('The stream is closed.')))
            return promise
        try:
            output = self.codec.decompress(_bytes(value)) if hasattr(self.codec, 'decompress') else self.codec.compress(_bytes(value))
            self._emit(output, (resolve, reject))
        except (PageTypeError, zlib.error) as error:
            reject(js_error_value(self._fail(error)))
        return promise

    def close(self):
        promise, resolve, reject = _promise(self.services)
        if self.failed is not None or self.finished:
            reject(js_error_value(self.failed or PageTypeError('The stream is closed.')))
            return promise
        try:
            output = self.codec.flush()
            if self.decompress and not self.codec.eof:
                raise PageTypeError('Compressed input was truncated.')
            self.finished = True
            self._emit(output, (resolve, reject))
            while self.readers:
                reader_resolve, _reject = self.readers.popleft()
                reader_resolve({'value': pyv8.Undefined, 'done': True})
        except (PageTypeError, zlib.error) as error:
            reject(js_error_value(self._fail(error)))
        return promise


@interface('ReadableStream', None)
class ReadableStream:
    def __init__(self, source=None, *_extra, services=None, _realm=None, **_):
        self._pipe = source if isinstance(source, _BytePipe) else None
        self._services = services or _realm
        self._reader = None

    def get_locked(self): return self._reader is not None

    def fn_getReader(self, *_extra):
        if self._reader is not None:
            raise PageTypeError('ReadableStream is locked')
        self._reader = ReadableStreamDefaultReader(self)
        return self._reader


@interface('WritableStream', None)
class WritableStream:
    def __init__(self, sink=None, *_extra, services=None, _realm=None, **_):
        self._pipe = sink if isinstance(sink, _BytePipe) else None
        self._services = services or _realm
        self._writer = None

    def get_locked(self): return self._writer is not None

    def fn_getWriter(self, *_extra):
        if self._writer is not None:
            raise PageTypeError('WritableStream is locked')
        self._writer = WritableStreamDefaultWriter(self)
        return self._writer


@interface('ReadableStreamDefaultReader', None)
class ReadableStreamDefaultReader:
    def __init__(self, stream=MISSING, *_extra, **_):
        if stream is MISSING:
            raise missing_error('ReadableStreamDefaultReader', 'constructor', 1, 0, constructor=True)
        if not isinstance(stream, ReadableStream):
            raise PageTypeError('The first argument is not a ReadableStream.')
        if stream._reader is not None:
            raise PageTypeError('ReadableStream is locked')
        self._stream = stream
        stream._reader = self

    def fn_read(self, *_extra):
        if self._stream is None:
            raise PageTypeError('Reader has been released')
        return self._stream._pipe.read() if self._stream._pipe is not None else _promise(self._stream._services)[0]

    def fn_releaseLock(self, *_extra):
        if self._stream is not None:
            self._stream._reader = None
            self._stream = None


@interface('WritableStreamDefaultWriter', None)
class WritableStreamDefaultWriter:
    def __init__(self, stream=MISSING, *_extra, **_):
        if stream is MISSING:
            raise missing_error('WritableStreamDefaultWriter', 'constructor', 1, 0, constructor=True)
        if not isinstance(stream, WritableStream):
            raise PageTypeError('The first argument is not a WritableStream.')
        if stream._writer is not None:
            raise PageTypeError('WritableStream is locked')
        self._stream = stream
        stream._writer = self

    def fn_write(self, chunk=MISSING, *_extra):
        if self._stream is None:
            raise PageTypeError('Writer has been released')
        return self._stream._pipe.write(chunk) if self._stream._pipe is not None else _resolved(self._stream._services)

    def fn_close(self, *_extra):
        if self._stream is None:
            raise PageTypeError('Writer has been released')
        return self._stream._pipe.close() if self._stream._pipe is not None else _resolved(self._stream._services)

    def fn_releaseLock(self, *_extra):
        if self._stream is not None:
            self._stream._writer = None
            self._stream = None


def _resolved(services):
    promise, resolve, _reject = _promise(services)
    resolve(None)
    return promise


class _CompressionBase:
    def __init__(self, format=MISSING, *_extra, services=None, _realm=None, **_):
        name = type(self).__name__
        if format is MISSING:
            raise missing_error(name, 'constructor', 1, 0, constructor=True)
        fmt = dom_string(format)
        if fmt not in _FORMATS:
            raise PageTypeError(f"Failed to construct '{name}': Unsupported compression format: '{fmt}'")
        self._services = services or _realm
        pipe = _BytePipe(fmt, name == 'DecompressionStream', self._services)
        self._readable = ReadableStream(pipe, services=self._services)
        self._writable = WritableStream(pipe, services=self._services)

    def get_readable(self): return self._readable
    def get_writable(self): return self._writable


@interface('CompressionStream', None)
class CompressionStream(_CompressionBase):
    pass


@interface('DecompressionStream', None)
class DecompressionStream(_CompressionBase):
    pass


FEATURE = FeatureImplementation({
    'CompressionStream': CompressionStream,
    'DecompressionStream': DecompressionStream,
    'ReadableStream': ReadableStream,
    'WritableStream': WritableStream,
    'ReadableStreamDefaultReader': ReadableStreamDefaultReader,
    'WritableStreamDefaultWriter': WritableStreamDefaultWriter,
})
