"""Fixed completion table for scripts, sheets, images, media and preloads."""
class ResourceCompletion:
    HANDLERS = {"script": "script", "style": "common", "link": "common",
                "img": "image", "audio": "media", "video": "media",
                "source": "media", "preload": "common", "iframe": "common"}

    def __init__(self, page):
        self.page = page

    def complete(self, node, kind, response, error, response_length):
        getattr(self, self.HANDLERS[kind])(node, kind, response, error, response_length)

    def script(self, node, kind, response, error, response_length):
        page = self.page
        if kind == "script" and not getattr(node, "_resource_async", False):
            node._resource_done, node._resource_response, node._resource_error = True, response, error
            if getattr(node, "_script_defer", False):
                page.scripts.run_deferred_scripts()
                page.lifecycle.maybe_fire_document_load()
                return
            page.scripts.drain_script_queue()
            page.lifecycle.maybe_fire_document_load()
            return
        self.common(node, kind, response, error, response_length)

    def media(self, node, kind, response, error, response_length):
        page = self.page
        if kind in ("audio", "video", "source"):
            # Nothing decodes here, so a media element ends the way Chrome ends bytes
            # it cannot use: progress, suspend, error -- and never a load.
            page.lifecycle.dispatch_page_event("progress", node, {"bubbles": False})
            page.lifecycle.dispatch_page_event("suspend", node, {"bubbles": False})
            page.lifecycle.dispatch_page_event("error", node, {"bubbles": False})
            page.lifecycle.maybe_fire_document_load()
            return

    def image(self, node, kind, response, error, response_length):
        if kind == "img":
            # ``complete`` turns true when a load settles, and the bytes are kept: the declared
            # size is what ``naturalWidth``/``naturalHeight`` answer with.
            node._image_complete = True
            if not error and response is not None:
                body = getattr(response, "body", None)
                #: A loader may answer with text, and a header parser reads bytes.
                node._image_bytes = body.encode("utf-8") if isinstance(body, str) else body
                node._image_type = str(response.headers.fn_get("content-type") or "")
        self.common(node, kind, response, error, response_length)

    def common(self, node, kind, response, error, response_length):
        page = self.page
        if error:
            if kind == "script":
                page.lifecycle.dispatch_script_error(node, error)
            else:
                page.lifecycle.dispatch_page_event("error", node, {"bubbles": False})
            page.lifecycle.dispatch_progress_event(node, "loadend")
        else:
            if kind == "script":
                page.scripts.execute_script(node, response.body_text(), response.url or page.url)
            elif kind in ("style", "link"):
                # A ``<link rel=stylesheet>`` is tracked under its own tag so that its
                # timing entry reports ``link``, which is the kind it arrives as here.
                node._stylesheet_text = response.body_text()
                # The cascade caches the rules a document's sheets give, so a sheet
                # that arrives after the first layout has to invalidate that cache.
                document = getattr(node, "ownerDocument", None)
                if document is not None:
                    document.style_version = getattr(document, "style_version", 0) + 1
                    document.layout_version += 1
                    document._layout_snapshot = None
            # The transfer is complete: one progress event carries the whole body.
            length = response_length(response)
            page.lifecycle.dispatch_progress_event(
                node, "progress", loaded=length, total=length, length_computable=True,
            )
            page.lifecycle.dispatch_page_event("load", node, {"bubbles": False})
            page.lifecycle.dispatch_progress_event(node, "loadend")
        page.lifecycle.maybe_fire_document_load()
