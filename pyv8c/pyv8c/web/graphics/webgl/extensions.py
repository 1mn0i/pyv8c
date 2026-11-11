"""WebGL extensions implementation."""

from ....idl import interface
from .objects import WebGLVertexArrayObjectOES, WebGLTimerQueryEXT
from .constants import WEBGL1_EXTENSIONS, WEBGL2_EXTENSIONS


class _Extension:
    """What every extension object holds: the context that handed it out."""

    def __init__(self, context=None, **_):
        self._context = context


@interface('WebGLDebugRendererInfo', None)
class WebGLDebugRendererInfo(_Extension):
    """The extension whose two pnames name the machine's GL implementation."""


@interface('OESVertexArrayObject', None)
class OESVertexArrayObject(_Extension):
    def fn_createVertexArrayOES(self, *_):
        return self._context._remember(WebGLVertexArrayObjectOES(context=self._context))

    def fn_bindVertexArrayOES(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObjectOES):
            array.created = True

    def fn_deleteVertexArrayOES(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObjectOES):
            array.created = False

    def fn_isVertexArrayOES(self, array=None, *_):
        return isinstance(array, WebGLVertexArrayObjectOES) and array.created


@interface('EXTDisjointTimerQuery', None)
class EXTDisjointTimerQuery(_Extension):
    def fn_createQueryEXT(self, *_):
        return self._context._remember(WebGLTimerQueryEXT(context=self._context))

    def fn_isQueryEXT(self, query=None, *_):
        return isinstance(query, WebGLTimerQueryEXT)


@interface('WebGLLoseContext', None)
class WebGLLoseContext(_Extension):
    """The extension a page uses to lose and restore its context on purpose."""

    def fn_loseContext(self, *_):
        self._context._context_lost = True

    def fn_restoreContext(self, *_):
        # The reference browser never hands a lost context back.
        return None


def _shape_only_extension(interface_name):
    """An extension this module leaves to the baseline: constants and member shapes."""
    return interface(interface_name, None)(type(interface_name, (_Extension,), {
        "__doc__": f"The ``{interface_name}`` extension object.",
        "__module__": __name__,
    }))


def _extension_interfaces():
    """Every extension interface a context can hand out, by interface name."""
    composed = {cls.__idl_spec__.name: cls for cls in (
        WebGLDebugRendererInfo, OESVertexArrayObject, EXTDisjointTimerQuery, WebGLLoseContext,
    )}
    for _gl_name, interface_name in (*WEBGL1_EXTENSIONS, *WEBGL2_EXTENSIONS):
        if interface_name not in composed:
            composed[interface_name] = _shape_only_extension(interface_name)
    return composed


EXTENSION_INTERFACES = _extension_interfaces()
