"""WebGL objects implementation."""

from ....idl import interface


@interface('WebGLObject', None)
class WebGLObject:
    """What a context hands out for everything a page cannot query."""

    def __init__(self, context=None, **_):
        self._context = context


@interface('WebGLBuffer', 'WebGLObject')
class WebGLBuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.size = 0
        self.usage = 35044


@interface('WebGLFramebuffer', 'WebGLObject')
class WebGLFramebuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.attachments = {}


@interface('WebGLRenderbuffer', 'WebGLObject')
class WebGLRenderbuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.width = 0
        self.height = 0
        self.has_storage = False


@interface('WebGLTexture', 'WebGLObject')
class WebGLTexture(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.has_image = False


@interface('WebGLShader', 'WebGLObject')
class WebGLShader(WebGLObject):
    def __init__(self, context=None, shader_type=35633, **_):
        super().__init__(context=context)
        self.shader_type = shader_type
        self.source = ""
        self.compiled = False
        self.info_log = ""


@interface('WebGLProgram', 'WebGLObject')
class WebGLProgram(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.linked = False
        self.shaders = []
        self.info_log = ""


@interface('WebGLVertexArrayObject', 'WebGLObject')
class WebGLVertexArrayObject(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        # Binding is what brings a vertex array into being, as ``isVertexArray`` reports.
        self.created = False


@interface('WebGLVertexArrayObjectOES', 'WebGLObject')
class WebGLVertexArrayObjectOES(WebGLObject):
    """What a WebGL 1 context hands out through ``OES_vertex_array_object``."""

    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.created = False


@interface('WebGLQuery', 'WebGLObject')
class WebGLQuery(WebGLObject):
    pass


@interface('WebGLTimerQueryEXT', 'WebGLObject')
class WebGLTimerQueryEXT(WebGLObject):
    """What a WebGL 1 context hands out through ``EXT_disjoint_timer_query``."""


@interface('WebGLSampler', 'WebGLObject')
class WebGLSampler(WebGLObject):
    pass


@interface('WebGLTransformFeedback', 'WebGLObject')
class WebGLTransformFeedback(WebGLObject):
    pass


@interface('WebGLSync', 'WebGLObject')
class WebGLSync(WebGLObject):
    pass


@interface('WebGLUniformLocation', None)
class WebGLUniformLocation:
    pass


@interface('WebGLActiveInfo', None)
class WebGLActiveInfo:
    def __init__(self, name="", size=0, value_type=0, **_):
        self._name, self._size, self._type = name, size, value_type

    def get_size(self): return self._size
    def get_type(self): return self._type
    def get_name(self): return self._name


@interface('WebGLShaderPrecisionFormat', None)
class WebGLShaderPrecisionFormat:
    def __init__(self, range_min=0, range_max=0, precision=0, **_):
        self._range = (range_min, range_max, precision)

    def get_rangeMin(self): return self._range[0]
    def get_rangeMax(self): return self._range[1]
    def get_precision(self): return self._range[2]
