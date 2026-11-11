"""Local resource resolvers: no transport and no redirect machinery."""
import base64
from urllib.parse import unquote_to_bytes
from ...foundation.environment import extension_resource
from ...foundation.host_errors import LoadFailure
from ...foundation.logging import logger
from ...web.fetch.response import Response

#: how the load completes (an image completes as one so its element answers for the size).
PRELOAD_KINDS = {
    "image": ("image", "image", "img"),
    "audio": ("media", "audio", "preload"),
    "video": ("media", "video", "preload"),
    "font": ("font", "font", "preload"),
    "script": ("script", "script", "preload"),
    "style": ("style", "style", "preload"),
    "fetch": ("fetch", "fetch", "preload"),
    "track": ("track", "track", "preload"),
}

#: The kind of file each suffix promises; an unknown suffix is served as bytes.
EXTENSION_TYPES = {
    "css": "text/css", "js": "text/javascript", "mjs": "text/javascript",
    "json": "application/json", "html": "text/html", "htm": "text/html",
    "txt": "text/plain", "svg": "image/svg+xml",
    "png": "image/png", "gif": "image/gif", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "webp": "image/webp", "bmp": "image/bmp", "ico": "image/x-icon",
}

#: A one-pixel PNG, and a small SVG: an image that decodes, for a path promising one.
_EXTENSION_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8AAAwAB/QFz7GjWAAAAAElFTkSuQmCC"
)
_EXTENSION_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"></svg>'


def data_url_response(url, services):
    """The response a ``data:`` URL carries, decoded in place.

    A ``data:`` URL *is* its payload: Chromium fetches nothing for it, and a frame whose
    ``src`` is one gets its document without waiting on an answer no network would give.
    """
    header, comma, payload = str(url)[len("data:"):].partition(",")
    if not comma:
        raise LoadFailure("data: URL carries no payload")
    if header.endswith(";base64"):
        header = header[: -len(";base64")]
        try:
            body = base64.b64decode(payload + "=" * (-len(payload) % 4), validate=False)
        except ValueError as exc:  # binascii.Error is one
            raise LoadFailure(f"data: URL payload is not base64: {exc}") from None
    else:
        body = unquote_to_bytes(payload)
    content_type = header or "text/plain;charset=US-ASCII"
    return Response(body, {"url": str(url), "headers": {"content-type": content_type}},
                    services=services)


def extension_response(page, target, request):
    """The response for a resource this browser's extensions expose, or ``None``.

    Chrome answers one locally, in the file's own kind, and a page whose origin the
    extension's ``matches`` names reads it without a CORS check. This framework has
    declarations but not extension files: the returned contents are placeholders,
    not the extension's actual resources.
    """
    resource = extension_resource(page.environment, target, source_url=page.url)
    if resource is None:
        return None
    if str(request.get("mode", "")) == "navigate" and not request.get("browser_initiated"):
        return None
    logger.warning("extension resource is a placeholder, not extension content: {}", target)
    content_type = EXTENSION_TYPES.get(resource.rsplit(".", 1)[-1].lower(), "application/octet-stream")
    if content_type == "image/svg+xml":
        body = _EXTENSION_SVG
    elif content_type.startswith("image/"):
        body = _EXTENSION_PNG
    else:
        body = b""
    return Response(
        body,
        {"url": target, "type": "basic",
         "headers": {"content-type": content_type, "content-length": str(len(body))}},
        services=page.services,
    )


