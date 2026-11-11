"""Canvas contexts implementation."""

from ....idl import interface
from ....foundation.arguments import IdlArgumentError, dictionary, is_nullish


_CONTEXT_KINDS = ("2d", "webgl", "webgl2", "bitmaprenderer", "webgpu")


_COLOR_SPACES = ("srgb", "display-p3")


def requested_context(kind):
    """The context kind a page asked for, or ``None`` when a canvas has none to give."""
    requested = str(kind).lower()
    if requested == "experimental-webgl": return "webgl"
    return requested if requested in _CONTEXT_KINDS else None


@interface('ImageBitmapRenderingContext', None)
class ImageBitmapRenderingContext:
    """What ``getContext('bitmaprenderer')`` hands out: a canvas showing one bitmap."""

    def __init__(self, canvas=None, **_):
        self._canvas = canvas
        self._bitmap = None

    def get_canvas(self):
        return self._canvas

    def fn_transferFromImageBitmap(self, bitmap=None, *_):
        """Show *bitmap*; a null clears whatever the canvas was showing."""
        self._bitmap = None if is_nullish(bitmap) else bitmap
        return None


@interface('GPUCanvasContext', None)
class GPUCanvasContext:
    """What ``getContext('webgpu')`` hands out; it shows nothing until configured."""

    def __init__(self, canvas=None, **_):
        self._canvas = canvas
        self._configuration = None

    def get_canvas(self):
        return self._canvas

    def fn_configure(self, configuration=None, *_):
        """Record the configuration; a context without a device draws nothing."""
        self._configuration = {} if is_nullish(configuration) else dict(configuration)

    def fn_unconfigure(self, *_):
        self._configuration = None

    def fn_getCurrentTexture(self, *_):
        if self._configuration is None:
            from ...page_errors import dom_exception
            raise dom_exception(
                "InvalidStateError",
                "Failed to execute 'getCurrentTexture' on 'GPUCanvasContext':"
                " Canvas is not configured.")
        return None


def enum_error(interface, member, value, enum_name):
    """What the bindings raise for a value outside a WebIDL enum."""
    return IdlArgumentError(
        f"Failed to execute '{member}' on '{interface}':"
        f" The provided value '{value}' is not a valid enum value of type {enum_name}.")


def creation_settings(interface, options):
    """What ``getContext('2d', options)`` asked for, as ``getContextAttributes`` reports it."""
    settings = dictionary(options)
    record = {key: settings[key] for key in
              ("alpha", "desynchronized", "willReadFrequently", "colorSpace") if key in settings}
    if "colorSpace" in record and str(record["colorSpace"]) not in _COLOR_SPACES:
        raise enum_error(interface, "getContext", record["colorSpace"], "PredefinedColorSpace")
    return record
