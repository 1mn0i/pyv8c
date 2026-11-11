"""Canvas encoding implementation."""

import io
import math
import struct
import zlib

from pyv8c import pyv8
from ....foundation.arguments import MISSING, dom_string


def _png_chunk(kind, data):
    """One PNG chunk, including the CRC of its type and data."""
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data)))


def _canvas_png(width, height, pixels):
    """Encode RGBA rows with PNG's Up filter and low compression.

    The separate flushed and final IDAT chunks follow Chromium's streaming
    encoder. Very short streams use literal Huffman coding, as its encoder does.
    """
    row_size = width * 4
    filtered = bytearray()
    previous = bytes(row_size)
    for offset in range(0, len(pixels), row_size):
        row = pixels[offset:offset + row_size]
        filtered.append(2)
        filtered.extend((value - above) & 255 for value, above in zip(row, previous))
        previous = row
    strategy = zlib.Z_HUFFMAN_ONLY if len(filtered) <= 5 else zlib.Z_DEFAULT_STRATEGY
    compressor = zlib.compressobj(1, zlib.DEFLATED, 15, 8, strategy)
    first = compressor.compress(filtered) + compressor.flush(zlib.Z_SYNC_FLUSH)
    last = compressor.flush(zlib.Z_FINISH)
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + _png_chunk(b'IHDR', header)
            + _png_chunk(b'IDAT', first) + _png_chunk(b'IDAT', last)
            + _png_chunk(b'IEND', b''))


def normalize_encoding_type(kind):
    if kind is MISSING or kind is pyv8.Undefined:
        return 'image/png'
    requested = dom_string(kind).lower()
    return requested if requested in ('image/png', 'image/jpeg', 'image/webp') else 'image/png'


def normalize_encoding_quality(value):
    # The IDL argument is `any`: Chrome considers only primitive JS numbers.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
        if math.isfinite(value) and 0 <= value <= 1:
            return value
    return None


def encode_canvas(canvas, kind, quality):
    """Encode the current surface without acquiring or changing its context."""
    width, height = canvas._width, canvas._height
    surface = canvas._context2d
    image = surface._image if surface is not None else None
    context = getattr(canvas, '_webgl', None) or getattr(canvas, '_webgl2', None)
    pixels = context._backend().surface_pixels(context) if context is not None else None
    if pixels is not None and kind != 'image/png':
        try:
            from PIL import Image
            image = Image.frombytes('RGBA', (width, height), pixels)
        except ImportError:
            return encode_canvas(canvas, 'image/png', None)
    if kind == 'image/png':
        if pixels is None:
            pixels = image.convert('RGBA').tobytes() if image is not None else bytes(width * height * 4)
        return kind, _canvas_png(width, height, pixels)
    try:
        from PIL import Image, features
    except ImportError:
        # Pillow is optional; the stdlib PNG encoder remains available.
        return encode_canvas(canvas, 'image/png', None)
    if not features.check('jpg' if kind == 'image/jpeg' else 'webp'):
        return encode_canvas(canvas, 'image/png', None)
    try:
        if image is None:
            image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        stream = io.BytesIO()
        if kind == 'image/jpeg':
            # JPEG has no alpha; Chromium composites transparent pixels on black.
            rgba = image.convert('RGBA')
            rgb = Image.new('RGB', rgba.size, (0, 0, 0))
            rgb.paste(rgba, mask=rgba.getchannel('A'))
            jpeg_quality = 92 if quality is None else math.floor(quality * 100 + 0.5)
            options = {'quality': jpeg_quality, 'optimize': True}
            if jpeg_quality == 100:
                options['subsampling'] = 0
            rgb.save(stream, format='JPEG', **options)
        else:
            lossless = quality == 1
            webp_quality = 75 if lossless else 80 if quality is None else quality * 100
            image.save(stream, format='WEBP', quality=webp_quality, lossless=lossless)
        return kind, stream.getvalue()
    except (OSError, ValueError, KeyError):
        return kind, None
