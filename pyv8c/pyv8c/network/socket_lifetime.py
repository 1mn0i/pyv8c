"""Coordinate sync-client socket lifetime with cancellation of native I/O."""
import os
import socket
import threading

if os.name == "nt":
    import ctypes

    _cancel_io = ctypes.WinDLL("kernel32", use_last_error=True).CancelIoEx
    _cancel_io.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _cancel_io.restype = ctypes.c_int


class OwnedSocket:
    """Forward I/O without a lock; serialize close and cancellation of its handle.

    Installed before the sync client's receiver thread starts. The original
    socket must not be independently closed after it is handed to this owner.
    """

    __slots__ = ("_socket", "_lifetime_lock")

    def __init__(self, raw_socket):
        self._socket = raw_socket
        self._lifetime_lock = threading.RLock()

    def __getattr__(self, name):
        return getattr(self._socket, name)

    def shutdown(self, how):
        with self._lifetime_lock:
            self._socket.shutdown(how)

    def close(self):
        with self._lifetime_lock:
            self._socket.close()

    def abort(self):
        with self._lifetime_lock:
            handle = self._socket.fileno()
            if handle < 0:
                return
            error = None
            if os.name == "nt":
                # Winsock's synchronous writes may survive shutdown/close.
                # Hold the lifetime lock across CancelIoEx: a receiver must
                # not close/recycle this handle between lookup and cancellation.
                if not _cancel_io(handle, None):
                    code = ctypes.get_last_error()
                    if code != 1168:  # ERROR_NOT_FOUND: operation already ended
                        error = ctypes.WinError(code)
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                # EOF and a concurrent native cancellation can already have
                # disconnected the socket. close still releases its handle.
                pass
            finally:
                self._socket.close()
            if error is not None:
                raise error
