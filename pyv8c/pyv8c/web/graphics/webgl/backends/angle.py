"""Experimental WebGL 1 subset backed by an ANGLE GLES2 pbuffer."""
import ctypes as c
from pathlib import Path

from pyv8c import pyv8
from ..objects import WebGLBuffer, WebGLProgram, WebGLShader, WebGLUniformLocation
from .egl import AngleContext, AngleError, bind_function, library_names


class AngleWebGL:
    """Adapter for the explicitly supported triangle-drawing operations.

    This is not WebGL-conformant and must not be used for untrusted content.
    The owning page must call ``close`` on the creation thread.
    """

    def __init__(self, library_dir, canvas):
        self.context = AngleContext(library_dir, max(1, canvas.get_width()), max(1, canvas.get_height()))
        self.canvas = canvas
        gl = self.context.gl
        u, i, p = c.c_uint, c.c_int, c.c_void_p
        self.create_shader = bind_function(gl, 'glCreateShader', u, u)
        self.shader_source = bind_function(gl, 'glShaderSource', None, u, i, c.POINTER(c.c_char_p), c.POINTER(i))
        self._compile_shader = bind_function(gl, 'glCompileShader', None, u)
        self.shader_status = bind_function(gl, 'glGetShaderiv', None, u, u, c.POINTER(i))
        self.shader_log = bind_function(gl, 'glGetShaderInfoLog', None, u, i, c.POINTER(i), p)
        self.delete_shader = bind_function(gl, 'glDeleteShader', None, u)
        self.create_program = bind_function(gl, 'glCreateProgram', u)
        self.attach = bind_function(gl, 'glAttachShader', None, u, u)
        self.link = bind_function(gl, 'glLinkProgram', None, u)
        self.program_status = bind_function(gl, 'glGetProgramiv', None, u, u, c.POINTER(i))
        self.program_log = bind_function(gl, 'glGetProgramInfoLog', None, u, i, c.POINTER(i), p)
        self.delete_program = bind_function(gl, 'glDeleteProgram', None, u)
        self.use = bind_function(gl, 'glUseProgram', None, u)
        self.gen_buffers = bind_function(gl, 'glGenBuffers', None, i, c.POINTER(u))
        self.delete_buffers = bind_function(gl, 'glDeleteBuffers', None, i, c.POINTER(u))
        self.bind_buffer = bind_function(gl, 'glBindBuffer', None, u, u)
        self.buffer_data = bind_function(gl, 'glBufferData', None, u, c.c_ssize_t, p, u)
        self.attrib = bind_function(gl, 'glGetAttribLocation', i, u, c.c_char_p)
        self.uniform_location = bind_function(gl, 'glGetUniformLocation', i, u, c.c_char_p)
        self._gl_uniform1f = bind_function(gl, 'glUniform1f', None, i, c.c_float)
        self.enable_attrib = bind_function(gl, 'glEnableVertexAttribArray', None, u)
        self.pointer = bind_function(gl, 'glVertexAttribPointer', None, u, i, u, c.c_ubyte, i, p)
        self._viewport = bind_function(gl, 'glViewport', None, i, i, i, i)
        self.draw = bind_function(gl, 'glDrawArrays', None, u, i, i)
        self.read = bind_function(gl, 'glReadPixels', None, i, i, i, i, u, u, p)
        self._clear_color = bind_function(gl, 'glClearColor', None, c.c_float, c.c_float, c.c_float, c.c_float)
        self._color_mask = bind_function(gl, 'glColorMask', None, c.c_ubyte, c.c_ubyte, c.c_ubyte, c.c_ubyte)
        self._clear = bind_function(gl, 'glClear', None, u)

    def active(self):
        size = (max(1, self.canvas.get_width()), max(1, self.canvas.get_height()))
        self.context.resize(*size)

    def state(self, receiver, name, *values):
        self.active()
        getattr(self, '_' + name)(*values)

    def _log(self, handle, status, get_log):
        length = c.c_int()
        status(handle, 0x8B84, c.byref(length))
        if length.value <= 1:
            return ''
        buffer = c.create_string_buffer(length.value)
        get_log(handle, length.value, None, buffer)
        return buffer.value.decode('utf-8', errors='replace')

    def compile_shader(self, receiver, shader):
        self.active()
        handle = getattr(shader, '_angle_shader', 0)
        if not handle:
            handle = self.create_shader(int(shader.shader_type))
            shader._angle_shader = handle
        source = c.c_char_p(shader.source.encode('utf-8'))
        self.shader_source(handle, 1, c.byref(source), None)
        self._compile_shader(handle)
        status = c.c_int()
        self.shader_status(handle, 0x8B81, c.byref(status))
        shader.compiled = bool(status.value)
        shader.info_log = self._log(handle, self.shader_status, self.shader_log)

    def delete(self, receiver, value):
        self.active()
        if isinstance(value, WebGLBuffer):
            handle = c.c_uint(getattr(value, '_angle_buffer', 0))
            if handle.value:
                self.delete_buffers(1, c.byref(handle))
                value._angle_buffer = 0
        elif isinstance(value, WebGLShader):
            handle = getattr(value, '_angle_shader', 0)
            if handle: self.delete_shader(handle)
        elif isinstance(value, WebGLProgram):
            handle = getattr(value, '_angle_program', 0)
            if handle: self.delete_program(handle)

    def surface_pixels(self, receiver):
        self.active()
        width, height = receiver.get_drawingBufferWidth(), receiver.get_drawingBufferHeight()
        if not width or not height:
            return b''
        result = (c.c_ubyte * (width * height * 4))()
        self.read(0, 0, width, height, 0x1908, 0x1401, result)
        raw = bytes(result)
        stride = width * 4
        return b''.join(raw[row * stride:(row + 1) * stride] for row in range(height - 1, -1, -1))

    def link_program(self, receiver, program, *_):
        self.active()
        receiver._require('linkProgram', 1, program)
        if not isinstance(program, WebGLProgram):
            return
        stages = {shader.shader_type for shader in program.shaders if isinstance(shader, WebGLShader)}
        program.linked = receiver.VERTEX_SHADER in stages and receiver.FRAGMENT_SHADER in stages
        if not program.linked:
            return
        old = getattr(program, '_angle_program', 0)
        native = self.create_program()
        for shader in program.shaders:
            handle = getattr(shader, '_angle_shader', 0)
            if not handle or not shader.compiled:
                program.linked = False
                program.info_log = 'Shader was not successfully compiled.'
                self.delete_program(native)
                return
            self.attach(native, handle)
        self.link(native)
        status = c.c_int()
        self.program_status(native, 0x8B82, c.byref(status))
        program.linked = bool(status.value)
        program.info_log = self._log(native, self.program_status, self.program_log)
        program._angle_program = native
        if old: self.delete_program(old)

    def attrib_location(self, receiver, program, name, *_):
        self.active()
        if not program.linked:
            return -1
        return self.attrib(program._angle_program, str(name).encode('utf-8'))

    def get_uniform_location(self, receiver, program, name, *_):
        self.active()
        if not getattr(program, 'linked', False):
            return pyv8.Null
        index = self.uniform_location(program._angle_program, str(name).encode('utf-8'))
        if index < 0:
            return pyv8.Null
        location = WebGLUniformLocation()
        location._angle_owner = self
        location._angle_program = program
        location._angle_location = index
        return location

    def uniform1f(self, receiver, location, value, *_):
        self.active()
        if location is pyv8.Null or location is None:
            return
        program = receiver._bound.get(0x8B8D)
        if (not isinstance(location, WebGLUniformLocation)
                or getattr(location, '_angle_owner', None) is not self
                or getattr(location, '_angle_program', None) is not program):
            receiver._error = receiver.INVALID_OPERATION
            return
        self.use(program._angle_program)
        self._gl_uniform1f(location._angle_location, float(value))

    def upload(self, receiver, target, data, usage, *_):
        self.active()
        receiver._require('bufferData', 3, target, data, usage)
        buffer = receiver._bound.get(target)
        if not isinstance(buffer, WebGLBuffer) or int(target) != 0x8892:
            raise AngleError('experimental ANGLE backend requires a bound ARRAY_BUFFER')
        raw = None if isinstance(data, int) else bytes(data)
        size = int(data) if raw is None else len(raw)
        if size < 0:
            receiver._error = receiver.INVALID_VALUE
            return
        buffer.size = size
        buffer.usage = usage
        handle = c.c_uint(getattr(buffer, '_angle_buffer', 0))
        if not handle.value:
            self.gen_buffers(1, c.byref(handle))
        buffer._angle_buffer = handle.value
        self.bind_buffer(int(target), handle.value)
        payload = c.create_string_buffer(raw) if raw is not None else None
        self.buffer_data(int(target), size, payload, int(usage))

    def vertex_pointer(self, receiver, index, size, kind, normalized, stride, offset, *_):
        self.active()
        buffer = receiver._bound.get(0x8892)
        if buffer is None or not hasattr(buffer, '_angle_buffer'):
            raise AngleError('experimental ANGLE backend requires uploaded vertex data')
        if int(kind) != 0x1406 or normalized or int(stride) or int(offset):
            raise AngleError('experimental ANGLE backend supports packed float vertices only')
        self.bind_buffer(0x8892, buffer._angle_buffer)
        self.pointer(int(index), int(size), int(kind), False, 0, None)

    def enable_vertex(self, receiver, index, *_):
        self.active()
        self.enable_attrib(int(index))

    def draw_arrays(self, receiver, mode, first, count, *_):
        self.active()
        program = receiver._bound.get(0x8B8D)
        if not getattr(program, 'linked', False):
            raise AngleError('experimental ANGLE backend requires a linked program')
        self.use(program._angle_program)
        self.draw(int(mode), int(first), int(count))

    def read_pixels(self, receiver, x, y, width, height, fmt, kind, pixels, *_):
        self.active()
        width, height = int(width), int(height)
        if int(fmt) != 0x1908 or int(kind) != 0x1401 or width < 0 or height < 0:
            raise AngleError('experimental ANGLE backend supports RGBA unsigned-byte reads only')
        length = width * height * 4
        if int(pixels._get('length')) < length:
            raise AngleError('readPixels destination is too small')
        result = (c.c_ubyte * length)()
        self.read(int(x), int(y), width, height, int(fmt), int(kind), result)
        for index, value in enumerate(result):
            pixels[index] = value

    def close(self):
        self.context.close()


class AngleBackend:
    """Create and own experimental per-canvas backends for one browser."""

    def __init__(self, library_dir):
        self.library_dir = Path(library_dir).expanduser().resolve()
        for name in library_names():
            if not (self.library_dir / name).is_file():
                raise AngleError(f'ANGLE library not found: {self.library_dir / name}')
        self._contexts = {}

    def for_context(self, receiver):
        adapter = self._contexts.get(receiver)
        if adapter is None:
            services = getattr(receiver._canvas, '_services', None)
            if services is None or not hasattr(services, '_profile'):
                raise AngleError('experimental ANGLE backend supports page canvases only')
            adapter = AngleWebGL(self.library_dir, receiver._canvas)
            self._contexts[receiver] = adapter
        return adapter

    def close(self):
        for adapter in self._contexts.values():
            adapter.close()
        self._contexts.clear()

    def close_page(self, services):
        """Release this page's contexts on its owner thread."""
        for receiver, adapter in list(self._contexts.items()):
            if getattr(receiver._canvas, '_services', None) is services:
                adapter.close()
                del self._contexts[receiver]
