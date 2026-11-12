"""WebGL context implementation."""

from pyv8c import pyv8
from ....idl import interface
from ....foundation.arguments import MISSING, dom_string, is_nullish, missing_error
from .constants import (PARAMETERS, ARRAY_PARAMETERS, STRINGS, WEBGL2_STRINGS, WEBGL2_PARAMETERS, WEBGL1_EXTENSION_MAP, WEBGL2_EXTENSION_MAP, UNMASKED_VENDOR_WEBGL, UNMASKED_RENDERER_WEBGL, VOID_MEMBERS, BOUND_PARAMETERS, ENUMS, COLOR_BUFFER_BIT, FLOAT)
from .objects import (WebGLBuffer, WebGLFramebuffer, WebGLRenderbuffer, WebGLTexture, WebGLShader, WebGLProgram, WebGLVertexArrayObject, WebGLQuery, WebGLSampler, WebGLTransformFeedback, WebGLSync, WebGLShaderPrecisionFormat)
from .extensions import EXTENSION_INTERFACES
from .backends.simulated import SIMULATED_BACKEND
from .backends.protocol import WebGLBackend


def _typed(kind, values):
    return pyv8.JSTypeArray(list(values), kind)


def _clamped(value):
    """A clear colour channel the way ``clearColor`` stores it, as Chrome clamps it."""
    return min(max(float(value), 0.0), 1.0)


_DRAWING_BUFFER_FORMAT = 32856


@interface('WebGLRenderingContext', None)
class WebGLRenderingContext:
        # Absent: ``fn_<member>`` binds them, and ``_VOID_MEMBERS``/``_STATE_MEMBERS`` are the bodies.

    _INTERFACE_NAME = "WebGLRenderingContext"

    #: The pnames this context answers with text, and the ones it answers with a value.
    _STRING_PARAMETERS = STRINGS
    _SCALAR_PARAMETERS = PARAMETERS

    def __init__(self, canvas=None, *_extra, **_):
        self._canvas = canvas
        self._error = 0
        self._enabled = set()
        self._objects = []
        self._bound = {}
        width = canvas.get_width() if canvas is not None else 300
        height = canvas.get_height() if canvas is not None else 150
        self._viewport = (0, 0, width, height)
        self._scissor = (0, 0, width, height)
        self._clear_color = (0.0, 0.0, 0.0, 0.0)
        self._color_mask = (True, True, True, True)
        self._cleared = False
        self._context_lost = False
        self._extension_objects = {}
        self._color_space = "srgb"
        self._unpack_color_space = "srgb"

    def _require(self, member, required, *values):
        provided = sum(1 for value in values if value is not MISSING)
        if provided < required:
            raise missing_error(self._INTERFACE_NAME, member, required, provided,
                                constructor=False)

    def _remember(self, value):
        self._objects.append(value)
        return value

    def _backend(self) -> WebGLBackend:
        services = getattr(self._canvas, '_services', None)
        manager = getattr(services, 'graphics_backend', None)
        if manager is None or self._INTERFACE_NAME != 'WebGLRenderingContext':
            return SIMULATED_BACKEND
        return manager.for_context(self)

    def get_canvas(self): return self._canvas
    def get_drawingBufferWidth(self): return self._canvas.get_width() if self._canvas else 300
    def get_drawingBufferHeight(self): return self._canvas.get_height() if self._canvas else 150
    def get_drawingBufferFormat(self): return _DRAWING_BUFFER_FORMAT
    def get_drawingBufferColorSpace(self): return self._color_space
    def set_drawingBufferColorSpace(self, value): self._color_space = str(dom_string(value))
    def get_unpackColorSpace(self): return self._unpack_color_space
    def set_unpackColorSpace(self, value): self._unpack_color_space = str(dom_string(value))

    # -- creation ---------------------------------------------------------------
    def fn_createBuffer(self, *_): return self._remember(WebGLBuffer(context=self))
    def fn_createFramebuffer(self, *_):
        return self._remember(WebGLFramebuffer(context=self))
    def fn_createProgram(self, *_): return self._remember(WebGLProgram(context=self))
    def fn_createRenderbuffer(self, *_):
        return self._remember(WebGLRenderbuffer(context=self))
    def fn_createTexture(self, *_): return self._remember(WebGLTexture(context=self))

    def fn_createShader(self, shader_type=MISSING, *arguments):
        self._require('createShader', 1, shader_type)
        return self._remember(WebGLShader(context=self, shader_type=shader_type))

    # -- state ------------------------------------------------------------------
    def fn_enable(self, cap=MISSING, *arguments):
        self._require('enable', 1, cap)
        self._enabled.add(cap)

    def fn_disable(self, cap=MISSING, *arguments):
        self._require('disable', 1, cap)
        self._enabled.discard(cap)

    def fn_isEnabled(self, cap=MISSING, *arguments):
        self._require('isEnabled', 1, cap)
        return cap in self._enabled

    def fn_viewport(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *arguments):
        self._require('viewport', 4, x, y, width, height)
        self._viewport = (x, y, width, height)
        self._backend().state(self, 'viewport', *self._viewport)

    def fn_scissor(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *arguments):
        self._require('scissor', 4, x, y, width, height)
        self._scissor = (x, y, width, height)

    def fn_clearColor(self, red=MISSING, green=MISSING, blue=MISSING, alpha=MISSING, *arguments):
        self._require('clearColor', 4, red, green, blue, alpha)
        self._clear_color = tuple(_clamped(value) for value in (red, green, blue, alpha))
        self._backend().state(self, 'clear_color', *self._clear_color)

    def fn_colorMask(self, red=MISSING, green=MISSING, blue=MISSING, alpha=MISSING, *arguments):
        self._require('colorMask', 4, red, green, blue, alpha)
        self._color_mask = tuple(bool(value) for value in (red, green, blue, alpha))
        self._backend().state(self, 'color_mask', *self._color_mask)

    def fn_clear(self, mask=MISSING, *arguments):
        self._require('clear', 1, mask)
        if int(mask) & COLOR_BUFFER_BIT:
            self._cleared = True
        self._backend().state(self, 'clear', mask)

    def fn_bindBuffer(self, target=MISSING, buffer=MISSING, *arguments):
        self._require('bindBuffer', 2, target, buffer)
        self._bound[target] = None if is_nullish(buffer) else buffer

    def fn_bindFramebuffer(self, target=MISSING, framebuffer=MISSING, *arguments):
        self._require('bindFramebuffer', 2, target, framebuffer)
        self._bound[36160] = None if is_nullish(framebuffer) else framebuffer

    def fn_bindRenderbuffer(self, target=MISSING, renderbuffer=MISSING, *arguments):
        self._require('bindRenderbuffer', 2, target, renderbuffer)
        self._bound[36161] = None if is_nullish(renderbuffer) else renderbuffer

    def fn_bindTexture(self, target=MISSING, texture=MISSING, *arguments):
        self._require('bindTexture', 2, target, texture)
        self._bound[target] = None if is_nullish(texture) else texture

    def fn_useProgram(self, program=MISSING, *arguments):
        self._require('useProgram', 1, program)
        self._bound[35725] = None if is_nullish(program) else program

    def fn_bufferData(self, target=MISSING, data=MISSING, usage=MISSING, *arguments):
        self._require('bufferData', 3, target, data, usage)
        return self._backend().upload(self, target, data, usage, *arguments)

    def fn_framebufferTexture2D(self, target=MISSING, attachment=MISSING, textarget=MISSING,
                                texture=MISSING, level=MISSING, *arguments):
        self._require('framebufferTexture2D', 5, target, attachment, textarget, texture, level)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            framebuffer.attachments[attachment] = texture

    def fn_framebufferRenderbuffer(self, target=MISSING, attachment=MISSING, rbtarget=MISSING,
                                   renderbuffer=MISSING, *arguments):
        self._require('framebufferRenderbuffer', 4, target, attachment, rbtarget, renderbuffer)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            framebuffer.attachments[attachment] = renderbuffer

    def fn_texImage2D(self, target=MISSING, level=MISSING, internalformat=MISSING, *arguments):
        self._require('texImage2D', 6, target, level, internalformat, *arguments)
        texture = self._bound.get(target)
        if isinstance(texture, WebGLTexture):
            texture.has_image = True

    def fn_renderbufferStorage(self, target=MISSING, internalformat=MISSING, width=MISSING,
                               height=MISSING, *arguments):
        self._require('renderbufferStorage', 4, target, internalformat, width, height)
        renderbuffer = self._bound.get(36161)
        if isinstance(renderbuffer, WebGLRenderbuffer):
            renderbuffer.has_storage = True

    def fn_attachShader(self, program=MISSING, shader=MISSING, *arguments):
        self._require('attachShader', 2, program, shader)
        if isinstance(program, WebGLProgram) and shader not in program.shaders:
            program.shaders.append(shader)

    def fn_detachShader(self, program=MISSING, shader=MISSING, *arguments):
        self._require('detachShader', 2, program, shader)
        if isinstance(program, WebGLProgram) and shader in program.shaders:
            program.shaders.remove(shader)

    def fn_shaderSource(self, shader=MISSING, source=MISSING, *arguments):
        self._require('shaderSource', 2, shader, source)
        if isinstance(shader, WebGLShader):
            shader.source = str(dom_string(source))

    def fn_compileShader(self, shader=MISSING, *arguments):
        self._require('compileShader', 1, shader)
        if isinstance(shader, WebGLShader) and shader._context is self:
            self._backend().compile_shader(self, shader)
        else:
            self._error = self.INVALID_OPERATION

    def _delete_object(self, member, value, kind):
        self._require(member, 1, value)
        if is_nullish(value):
            return
        if not isinstance(value, kind) or value._context is not self:
            self._error = self.INVALID_OPERATION
            return
        if getattr(value, '_deleted', False):
            return
        self._backend().delete(self, value)
        value._deleted = True
        if isinstance(value, WebGLBuffer):
            for target, bound in list(self._bound.items()):
                if bound is value:
                    self._bound[target] = None

    def fn_deleteBuffer(self, value=MISSING, *_): self._delete_object('deleteBuffer', value, WebGLBuffer)
    def fn_deleteShader(self, value=MISSING, *_): self._delete_object('deleteShader', value, WebGLShader)
    def fn_deleteProgram(self, value=MISSING, *_): self._delete_object('deleteProgram', value, WebGLProgram)

    def fn_linkProgram(self, program=MISSING, *arguments):
        self._require('linkProgram', 1, program)
        return self._backend().link_program(self, program, *arguments)

    # -- queries ----------------------------------------------------------------
    def fn_getError(self, *_):
        error, self._error = self._error, 0
        return error

    def fn_isContextLost(self, *_): return self._context_lost

    def fn_getContextAttributes(self, *_):
        return {"alpha": True, "antialias": True, "depth": True, "desynchronized": False,
                "failIfMajorPerformanceCaveat": False, "powerPreference": "default",
                "premultipliedAlpha": True, "preserveDrawingBuffer": False, "stencil": False,
                "xrCompatible": False}

    def fn_getExtension(self, name=MISSING, *arguments):
        self._require('getExtension', 1, name)
        interface_name = self._extensions().get(str(dom_string(name)))
        if interface_name is None:
            return pyv8.Null
        return self._extension_object(interface_name)

    def _extensions(self):
        """The GL name to interface map this context answers for."""
        return WEBGL1_EXTENSION_MAP

    def _extension_object(self, interface_name):
        """The one object this context hands back for an extension, built on first ask."""
        existing = self._extension_objects.get(interface_name)
        if existing is not None:
            return existing
        # This interface is not a global, so nothing materialises it: a Python-built
        # instance has to be committed to the realm before it can become a JS object.
        # Staging is idempotent, so a second canvas asks for it again safely.
        services = getattr(self._canvas, "_services", None)
        realm = getattr(services, "realm_services", None)
        if realm is not None:
            realm.materialize_interface(interface_name)
        value = EXTENSION_INTERFACES[interface_name](context=self)
        self._extension_objects[interface_name] = value
        return value

    def fn_getSupportedExtensions(self, *_): return list(self._extensions())

    def fn_getParameter(self, pname=MISSING, *arguments):
        self._require('getParameter', 1, pname)
        target = BOUND_PARAMETERS.get(pname)
        if target is not None:
            bound = self._bound.get(target)
            return pyv8.Null if bound is None else bound
        if pname == self.VIEWPORT:
            return _typed(pyv8.ArrayType.Int32Array, self._viewport)
        if pname == self.SCISSOR_BOX:
            return _typed(pyv8.ArrayType.Int32Array, self._scissor)
        if pname == self.COLOR_CLEAR_VALUE:
            return _typed(pyv8.ArrayType.Float32Array, self._clear_color)
        if pname == self.COLOR_WRITEMASK:
            return list(self._color_mask)
        if pname in (UNMASKED_VENDOR_WEBGL, UNMASKED_RENDERER_WEBGL):
            return self._unmasked(pname)
        if pname == 34047 and "EXTTextureFilterAnisotropic" in self._extension_objects:
            return 16
        for _name, constant, kind, values in ARRAY_PARAMETERS:
            if pname == constant and values is not None:
                return _typed(kind, values)
        for _name, constant, text in self._STRING_PARAMETERS:
            if pname == constant:
                return text
        for _name, constant, value in self._SCALAR_PARAMETERS:
            if pname == constant:
                return pyv8.Null if value is None else value
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def _unmasked(self, pname):
        """The GL identity the profile carries, for the two debug renderer pnames."""
        environment = getattr(getattr(self._canvas, "_services", None), "environment", None)
        if environment is None:
            return ""
        settings = environment.webgl
        return settings.vendor if pname == UNMASKED_VENDOR_WEBGL else settings.renderer

    def fn_getShaderPrecisionFormat(self, shader_type=MISSING, precision_type=MISSING,
                                    *arguments):
        self._require('getShaderPrecisionFormat', 2, shader_type, precision_type)
        if precision_type in (self.LOW_FLOAT, self.MEDIUM_FLOAT, self.HIGH_FLOAT):
            return WebGLShaderPrecisionFormat(127, 127, 23)
        if precision_type in (self.LOW_INT, self.MEDIUM_INT, self.HIGH_INT):
            return WebGLShaderPrecisionFormat(31, 30, 0)
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getShaderParameter(self, shader=MISSING, pname=MISSING, *arguments):
        self._require('getShaderParameter', 2, shader, pname)
        if not isinstance(shader, WebGLShader):
            self._error = 1282
            return pyv8.Null
        if pname == self.COMPILE_STATUS:
            return shader.compiled
        if pname == self.DELETE_STATUS:
            return getattr(shader, '_deleted', False)
        if pname == self.SHADER_TYPE:
            return shader.shader_type
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getShaderSource(self, shader=MISSING, *arguments):
        self._require('getShaderSource', 1, shader)
        return shader.source if isinstance(shader, WebGLShader) else pyv8.Null

    def fn_getShaderInfoLog(self, shader=MISSING, *arguments):
        self._require('getShaderInfoLog', 1, shader)
        return shader.info_log if isinstance(shader, WebGLShader) else pyv8.Null

    def fn_getProgramParameter(self, program=MISSING, pname=MISSING, *arguments):
        self._require('getProgramParameter', 2, program, pname)
        if not isinstance(program, WebGLProgram):
            self._error = 1282
            return pyv8.Null
        if pname in (self.LINK_STATUS, self.VALIDATE_STATUS):
            return program.linked
        if pname == self.DELETE_STATUS:
            return getattr(program, '_deleted', False)
        if pname in (self.ACTIVE_ATTRIBUTES, self.ACTIVE_UNIFORMS):
            return 0
        if pname == self.ATTACHED_SHADERS:
            return list(program.shaders)
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getProgramInfoLog(self, program=MISSING, *arguments):
        self._require('getProgramInfoLog', 1, program)
        return program.info_log if isinstance(program, WebGLProgram) else pyv8.Null

    def fn_getAttachedShaders(self, program=MISSING, *arguments):
        self._require('getAttachedShaders', 1, program)
        return list(program.shaders) if isinstance(program, WebGLProgram) else pyv8.Null

    def fn_getAttribLocation(self, program=MISSING, name=MISSING, *arguments):
        self._require('getAttribLocation', 2, program, name)
        return self._backend().attrib_location(self, program, name, *arguments)

    def fn_getUniformLocation(self, program=MISSING, name=MISSING, *arguments):
        self._require('getUniformLocation', 2, program, name)
        return self._backend().get_uniform_location(self, program, name, *arguments)

    def fn_uniform1f(self, location=MISSING, value=MISSING, *arguments):
        self._require('uniform1f', 2, location, value)
        return self._backend().uniform1f(self, location, value, *arguments)

    def fn_enableVertexAttribArray(self, index=MISSING, *arguments):
        self._require('enableVertexAttribArray', 1, index)
        return self._backend().enable_vertex(self, index, *arguments)

    def fn_vertexAttribPointer(self, index=MISSING, size=MISSING, kind=MISSING,
                               normalized=MISSING, stride=MISSING, offset=MISSING, *arguments):
        self._require('vertexAttribPointer', 6, index, size, kind, normalized, stride, offset)
        return self._backend().vertex_pointer(self, index, size, kind, normalized, stride, offset, *arguments)

    def fn_drawArrays(self, mode=MISSING, first=MISSING, count=MISSING, *arguments):
        self._require('drawArrays', 3, mode, first, count)
        return self._backend().draw_arrays(self, mode, first, count, *arguments)

    def fn_getUniform(self, program=MISSING, location=MISSING, *arguments):
        self._require('getUniform', 2, program, location)
        return pyv8.Null

    def fn_getActiveAttrib(self, program=MISSING, index=MISSING, *arguments):
        self._require('getActiveAttrib', 2, program, index)
        return pyv8.Null

    def fn_getActiveUniform(self, program=MISSING, index=MISSING, *arguments):
        self._require('getActiveUniform', 2, program, index)
        return pyv8.Null

    def fn_checkFramebufferStatus(self, target=MISSING, *arguments):
        self._require('checkFramebufferStatus', 1, target)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            attached = list(framebuffer.attachments.values())
            # An attachment without an image or storage is not an attachment to Chrome.
            if attached and all(getattr(item, "has_image", False)
                                or getattr(item, "has_storage", False) for item in attached):
                return self.FRAMEBUFFER_COMPLETE
        return self.FRAMEBUFFER_INCOMPLETE_MISSING_ATTACHMENT

    def fn_getBufferParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getBufferParameter', 2, target, pname)
        buffer = self._bound.get(target)
        if not isinstance(buffer, WebGLBuffer):
            self._error = 1282
            return pyv8.Null
        if pname == self.BUFFER_SIZE:
            return buffer.size
        if pname == self.BUFFER_USAGE:
            return buffer.usage
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getVertexAttribOffset(self, index=MISSING, pname=MISSING, *arguments):
        self._require('getVertexAttribOffset', 2, index, pname)
        return 0

    def fn_getVertexAttrib(self, index=MISSING, pname=MISSING, *arguments):
        self._require('getVertexAttrib', 2, index, pname)
        return 0

    def fn_getTexParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getTexParameter', 2, target, pname)
        return pyv8.Null

    def fn_getFramebufferAttachmentParameter(self, target=MISSING, attachment=MISSING,
                                             pname=MISSING, *arguments):
        self._require('getFramebufferAttachmentParameter', 3, target, attachment, pname)
        return pyv8.Null

    def fn_getRenderbufferParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getRenderbufferParameter', 2, target, pname)
        return 0

    def fn_isBuffer(self, value=None, *_):
        return isinstance(value, WebGLBuffer) and value in self._objects and not getattr(value, '_deleted', False)

    def fn_isFramebuffer(self, value=None, *_):
        return isinstance(value, WebGLFramebuffer) and value in self._objects

    def fn_isProgram(self, value=None, *_):
        return isinstance(value, WebGLProgram) and value in self._objects and not getattr(value, '_deleted', False)

    def fn_isRenderbuffer(self, value=None, *_):
        return isinstance(value, WebGLRenderbuffer) and value in self._objects

    def fn_isShader(self, value=None, *_):
        return isinstance(value, WebGLShader) and value in self._objects and not getattr(value, '_deleted', False)

    def fn_isTexture(self, value=None, *_):
        return isinstance(value, WebGLTexture) and value in self._objects

    def fn_readPixels(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, format=MISSING,
                      type=MISSING, pixels=MISSING, *arguments):
        self._require('readPixels', 7, x, y, width, height, format, type, pixels)
        return self._backend().read_pixels(self, x, y, width, height, format, type, pixels, *arguments)

    def _pixel_values(self, type, components):
        """The clear colour as ``readPixels`` hands it back, honouring ``colorMask``."""
        values = [value if self._color_mask[index] else 0.0
                  for index, value in enumerate(self._clear_color[:components])]
        return values if type == FLOAT else [int(round(value * 255)) for value in values]


def _void_member(name, required):
    def call(self, *arguments):
        if len(arguments) < required:
            raise missing_error('WebGLRenderingContext', name, required, len(arguments),
                                constructor=False)
        return None
    return call


@interface('WebGL2RenderingContext', 'WebGLRenderingContext')
class WebGL2RenderingContext(WebGLRenderingContext):
    """What a ``webgl2`` canvas hands out: the WebGL 1 answers, plus WebGL 2's."""

    _INTERFACE_NAME = "WebGL2RenderingContext"
    _STRING_PARAMETERS = WEBGL2_STRINGS
    # The WebGL 2 entries come first: the ones whose limit moved outrank WebGL 1's.
    _SCALAR_PARAMETERS = WEBGL2_PARAMETERS + PARAMETERS

    def _extensions(self):
        return WEBGL2_EXTENSION_MAP

    #: The internal formats WebGL 2 takes for a renderbuffer.
    _RENDERBUFFER_FORMATS = (
        "RGBA8", "RGBA4", "RGB5_A1", "RGB565", "DEPTH_COMPONENT16", "DEPTH_COMPONENT24",
        "DEPTH24_STENCIL8", "STENCIL_INDEX8", "SRGB8_ALPHA8", "R8", "RG8", "RGB8", "R16F",
        "RG16F", "RGB16F", "RGBA16F", "RGBA32F", "R11F_G11F_B10F",
    )

    def fn_getInternalformatParameter(self, target=MISSING, internalformat=MISSING, pname=MISSING,
                                      *arguments):
        """A format's sample counts, as an ``Int32Array``; a bad argument sets INVALID_ENUM."""
        self._require('getInternalformatParameter', 3, target, internalformat, pname)
        allowed = {getattr(self, name) for name in self._RENDERBUFFER_FORMATS}
        if target != self.RENDERBUFFER or internalformat not in allowed or pname != self.SAMPLES:
            self._error = self.INVALID_ENUM
            return pyv8.Null
        # 4 is the count Chrome reports for these formats on ordinary hardware.
        return _typed(pyv8.ArrayType.Int32Array, (4,))

    def fn_createVertexArray(self, *_):
        return self._remember(WebGLVertexArrayObject(context=self))

    def fn_bindVertexArray(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObject):
            array.created = True

    def fn_deleteVertexArray(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObject):
            array.created = False

    def fn_isVertexArray(self, value=None, *_):
        return isinstance(value, WebGLVertexArrayObject) and value.created

    def fn_createQuery(self, *_):
        return self._remember(WebGLQuery(context=self))

    def fn_isQuery(self, value=None, *_):
        return isinstance(value, WebGLQuery) and value in self._objects

    def fn_createSampler(self, *_):
        return self._remember(WebGLSampler(context=self))

    def fn_isSampler(self, value=None, *_):
        return isinstance(value, WebGLSampler) and value in self._objects

    def fn_createTransformFeedback(self, *_):
        return self._remember(WebGLTransformFeedback(context=self))

    def fn_isTransformFeedback(self, value=None, *_):
        return isinstance(value, WebGLTransformFeedback) and value in self._objects

    def fn_fenceSync(self, condition=MISSING, flags=MISSING, *arguments):
        self._require('fenceSync', 2, condition, flags)
        return self._remember(WebGLSync(context=self))

    def fn_isSync(self, value=None, *_):
        return isinstance(value, WebGLSync) and value in self._objects


for _name, _required in VOID_MEMBERS:
    if not hasattr(WebGLRenderingContext, "fn_" + _name):
        setattr(WebGLRenderingContext, "fn_" + _name, _void_member(_name, _required))


for _name, _constant, _value in PARAMETERS:
    setattr(WebGLRenderingContext, _name, _constant)


for _name, _constant, _kind, _values in ARRAY_PARAMETERS:
    setattr(WebGLRenderingContext, _name, _constant)


for _name, _constant, _text in STRINGS:
    setattr(WebGLRenderingContext, _name, _constant)


for _name, _value in ENUMS:
    setattr(WebGLRenderingContext, _name, _value)


for _name, _value in (
    ("VERTEX_SHADER", 35633), ("FRAGMENT_SHADER", 35632), ("LOW_FLOAT", 36336),
    ("MEDIUM_FLOAT", 36337), ("HIGH_FLOAT", 36338), ("LOW_INT", 36339), ("MEDIUM_INT", 36340),
    ("HIGH_INT", 36341),
    ("RENDERBUFFER", 36161), ("RGBA8", 32856), ("RGBA4", 32854), ("RGB5_A1", 32855),
    ("RGB565", 36194), ("DEPTH_COMPONENT16", 33189), ("DEPTH_COMPONENT24", 33190),
    ("DEPTH24_STENCIL8", 35056), ("STENCIL_INDEX8", 36168), ("SRGB8_ALPHA8", 35907),
    ("R8", 33321), ("RG8", 33323), ("RGB8", 32849), ("R16F", 33325), ("RG16F", 33327),
    ("RGB16F", 34843), ("RGBA16F", 34842), ("RGBA32F", 34836), ("R11F_G11F_B10F", 35898),
):
    setattr(WebGLRenderingContext, _name, _value)
