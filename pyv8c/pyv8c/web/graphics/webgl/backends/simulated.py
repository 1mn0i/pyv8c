"""State-only WebGL operations for the default simulated backend."""
from ..... import pyv8

from ..constants import RGB
from ..objects import WebGLBuffer, WebGLProgram, WebGLShader


class SimulatedBackend:
    """The same backend contract as ANGLE, without native graphics resources."""

    def for_context(self, receiver):
        return self

    def close_page(self, services):
        pass

    def state(self, receiver, name, *values):
        pass

    def compile_shader(self, receiver, shader):
        shader.compiled = True
        shader.info_log = ''

    def delete(self, receiver, value):
        pass

    def surface_pixels(self, receiver):
        width, height = receiver.get_drawingBufferWidth(), receiver.get_drawingBufferHeight()
        values = receiver._pixel_values(0x1401, 4) if receiver._cleared else [0] * 4
        return bytes(values) * width * height

    def upload(self, receiver, target, data, usage, *_):
        buffer = receiver._bound.get(target)
        if isinstance(buffer, WebGLBuffer):
            buffer.size = int(data) if isinstance(data, int) else len(bytes(data))
            buffer.usage = usage

    def link_program(self, receiver, program, *_):
        if isinstance(program, WebGLProgram):
            stages = {shader.shader_type for shader in program.shaders if isinstance(shader, WebGLShader)}
            program.linked = receiver.VERTEX_SHADER in stages and receiver.FRAGMENT_SHADER in stages

    def attrib_location(self, receiver, program, name, *_):
        return -1

    def get_uniform_location(self, receiver, program, name, *_):
        return pyv8.Null

    def uniform1f(self, receiver, location, value, *_):
        pass

    def enable_vertex(self, receiver, index, *_):
        pass

    def vertex_pointer(self, receiver, index, size, kind, normalized, stride, offset, *_):
        pass

    def draw_arrays(self, receiver, mode, first, count, *_):
        pass

    def read_pixels(self, receiver, x, y, width, height, format, type, pixels, *_):
        view = pixels if isinstance(pixels, pyv8.JSTypeArray) else None
        if view is None:
            receiver._error = receiver.INVALID_ENUM
            return None
        components = 3 if format == RGB else 4
        values = receiver._pixel_values(type, components) if receiver._cleared else [0] * components
        for index in range(min(int(view._get('length')), int(width) * int(height) * components)):
            view[index] = values[index % components]
        return None


SIMULATED_BACKEND = SimulatedBackend()
