"""Explicitly owned, off-screen EGL/GLES context backed by supplied ANGLE libraries.

This is a low-level building block, not a complete WebGL implementation. Calls and
teardown must run on the thread that created the context.
"""
from __future__ import annotations

import ctypes as c
import os
from pathlib import Path
import sys
from threading import RLock, get_ident


_DISPLAY_LOCK = RLock()
_DISPLAY_USERS = {}


class AngleError(RuntimeError):
    """ANGLE could not create or operate the requested graphics context."""


def library_names():
    if sys.platform == 'win32':
        return 'libEGL.dll', 'libGLESv2.dll'
    if sys.platform == 'darwin':
        return 'libEGL.dylib', 'libGLESv2.dylib'
    return 'libEGL.so', 'libGLESv2.so'


def bind_function(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class AngleContext:
    """One EGL pbuffer and GLES2 context; caller supplies the ANGLE library directory."""

    def __init__(self, library_dir, width=16, height=16):
        width, height = int(width), int(height)
        if width < 1 or height < 1:
            raise ValueError('ANGLE pbuffer dimensions must be positive')
        directory = Path(library_dir).expanduser().resolve()
        egl_name, gl_name = library_names()
        for name in (egl_name, gl_name):
            if not (directory / name).is_file():
                raise AngleError(f'ANGLE library not found: {directory / name}')
        self._thread = get_ident()
        self._dll_path = None
        self._display_key = None
        self.display = self.surface = self.context = None
        try:
            if os.name == 'nt':
                self._dll_path = os.add_dll_directory(str(directory))
            self.egl = c.CDLL(str(directory / egl_name))
            self.gl = c.CDLL(str(directory / gl_name))
            self._bind_egl()
            self._create(width, height)
        except Exception:
            self.close()
            raise

    def _bind_egl(self):
        p, i = c.c_void_p, c.c_int32
        egl = self.egl
        self._get_display = bind_function(egl, 'eglGetDisplay', p, p)
        self._query_string = bind_function(egl, 'eglQueryString', c.c_char_p, p, i)
        self._get_proc_address = bind_function(egl, 'eglGetProcAddress', p, c.c_char_p)
        self._initialize = bind_function(egl, 'eglInitialize', i, p, c.POINTER(i), c.POINTER(i))
        self._choose_config = bind_function(egl, 'eglChooseConfig', i, p, c.POINTER(i), c.POINTER(p), i, c.POINTER(i))
        self._bind_api = bind_function(egl, 'eglBindAPI', i, i)
        self._create_surface = bind_function(egl, 'eglCreatePbufferSurface', p, p, p, c.POINTER(i))
        self._create_context = bind_function(egl, 'eglCreateContext', p, p, p, p, c.POINTER(i))
        self._make_current = bind_function(egl, 'eglMakeCurrent', i, p, p, p, p)
        self._current_context = bind_function(egl, 'eglGetCurrentContext', p)
        self._destroy_surface = bind_function(egl, 'eglDestroySurface', i, p, p)
        self._destroy_context = bind_function(egl, 'eglDestroyContext', i, p, p)
        self._terminate = bind_function(egl, 'eglTerminate', i, p)
        self._get_error = bind_function(egl, 'eglGetError', i)

    def _check(self, value, operation):
        if not value:
            raise AngleError(f'{operation} failed (EGL error 0x{self._get_error():04x})')
        return value

    def _select_display(self):
        """Prefer ANGLE's surfaceless platform on Linux, then use EGL default."""
        i, p = c.c_int32, c.c_void_p
        display = None
        if sys.platform.startswith('linux'):
            extensions = (self._query_string(None, 0x3055) or b'').split()
            if b'EGL_MESA_platform_surfaceless' in extensions:
                address = self._get_proc_address(b'eglGetPlatformDisplayEXT')
                if address:
                    get_platform_display = c.CFUNCTYPE(p, c.c_uint, p, c.POINTER(i))(address)
                    display = get_platform_display(0x31DD, None, None)  # EGL_PLATFORM_SURFACELESS_MESA
        return self._check(display or self._get_display(None), 'eglGetDisplay')

    def _create(self, width, height):
        i, p = c.c_int32, c.c_void_p
        self.display = self._select_display()
        # EGLDisplay objects are process-shared. Terminating one while another
        # pbuffer still uses it invalidates that context on some implementations.
        self._display_key = (str(Path(self.egl._name).resolve()), int(self.display))
        with _DISPLAY_LOCK:
            if self._display_key not in _DISPLAY_USERS:
                self._check(self._initialize(self.display, None, None), 'eglInitialize')
            _DISPLAY_USERS[self._display_key] = _DISPLAY_USERS.get(self._display_key, 0) + 1
        self._check(self._bind_api(0x30A0), 'eglBindAPI')  # EGL_OPENGL_ES_API
        attrs = (i * 15)(0x3033, 1, 0x3040, 4, 0x3024, 8, 0x3023, 8,
                         0x3022, 8, 0x3021, 8, 0x3038, 0x3038, 0x3038)
        config, count = p(), i()
        self._check(self._choose_config(self.display, attrs, c.byref(config), 1, c.byref(count)),
                    'eglChooseConfig')
        if not count.value:
            raise AngleError('eglChooseConfig found no RGBA8 GLES2 pbuffer configuration')
        self._config = config
        self.surface = self._check(self._create_surface(
            self.display, config, (i * 5)(0x3057, width, 0x3056, height, 0x3038)), 'eglCreatePbufferSurface')
        self.context = self._check(self._create_context(
            self.display, config, None, (i * 3)(0x3098, 2, 0x3038)), 'eglCreateContext')
        self._check(self._make_current(self.display, self.surface, self.surface, self.context), 'eglMakeCurrent')
        self.width, self.height = width, height

    def activate(self):
        """Make this context current on its creating thread."""
        if get_ident() != self._thread:
            raise AngleError('ANGLE context must be used on its creating thread')
        if not self.context:
            raise AngleError('ANGLE context is closed')
        self._check(self._make_current(self.display, self.surface, self.surface, self.context), 'eglMakeCurrent')

    def resize(self, width, height):
        """Replace the pbuffer while retaining the context and its GL objects."""
        width, height = int(width), int(height)
        if width < 1 or height < 1:
            raise ValueError('ANGLE pbuffer dimensions must be positive')
        self.activate()
        if (width, height) == (self.width, self.height):
            return
        i = c.c_int32
        replacement = self._check(self._create_surface(
            self.display, self._config,
            (i * 5)(0x3057, width, 0x3056, height, 0x3038)), 'eglCreatePbufferSurface')
        try:
            self._check(self._make_current(self.display, replacement, replacement, self.context),
                        'eglMakeCurrent')
        except Exception:
            self._destroy_surface(self.display, replacement)
            raise
        previous = self.surface
        self.surface = replacement
        self.width, self.height = width, height
        self._destroy_surface(self.display, previous)

    def close(self):
        if get_ident() != self._thread:
            raise AngleError('ANGLE context must be closed on its creating thread')
        if self.display and hasattr(self, '_terminate'):
            if self.context:
                if self._current_context() == self.context:
                    self._make_current(self.display, None, None, None)
                self._destroy_context(self.display, self.context)
                self.context = None
            if self.surface:
                self._destroy_surface(self.display, self.surface)
                self.surface = None
            with _DISPLAY_LOCK:
                users = _DISPLAY_USERS.get(self._display_key, 0)
                if users == 1:
                    self._terminate(self.display)
                    del _DISPLAY_USERS[self._display_key]
                elif users > 1:
                    _DISPLAY_USERS[self._display_key] = users - 1
            self.display = None
            self._display_key = None
        if self._dll_path is not None:
            self._dll_path.close()
            self._dll_path = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
