"""Eager feature implementation for page-essential Web APIs."""

from ...engine.builtin_errors import implementations as error_interfaces
from ...engine.features import FeatureImplementation
from ...foundation.environment import BrowserEnvironment
from ..bom import api as bom
from ..bom import filesystem
from ..bom import window
from ..dom import collections, elements, events, model
from ..fetch import blob, headers, request, response, xhr
from ..fetch import events as fetch_events


def _interfaces(*classes):
    return {cls.__idl_spec__.name: cls for cls in classes}


FEATURE = FeatureImplementation(
    _interfaces(
        *error_interfaces(),
        collections.DOMTokenList, collections.HTMLAllCollection, collections.HTMLCollection, collections.NamedNodeMap, collections.NodeList,
        events.CustomEvent, events.Event, events.EventTarget, events.KeyboardEvent, events.MouseEvent, events.UIEvent,
        model.Node, model.DocumentFragment, model.Attr, model.CharacterData, model.Text, model.Comment, model.DocumentType, model.Document,
        elements.Element, elements.HTMLElement, elements.HTMLIFrameElement, elements.HTMLImageElement, elements.HTMLLinkElement,
        elements.HTMLMetaElement, elements.HTMLScriptElement, elements.HTMLStyleElement, elements.HTMLTemplateElement,
        *(model.HTML_CLASSES[name] for name in (
            "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement",
            "HTMLAnchorElement", "HTMLInputElement", "HTMLFormElement", "HTMLCanvasElement", "HTMLMediaElement",
            "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement", "HTMLSelectElement",
            "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement", "HTMLUnknownElement",
        )),
        headers.Headers, blob.Blob, request.Request, response.Response, fetch_events.MessageEvent, fetch_events.CloseEvent,
        fetch_events.ErrorEvent, xhr.XMLHttpRequestEventTarget, xhr.XMLHttpRequest,
        bom.Location, bom.Screen, bom.Storage, bom.URLSearchParams, bom.URL, bom.History,
        filesystem.DOMFileSystem, filesystem.Entry, filesystem.DirectoryEntry, filesystem.FileEntry,
        filesystem.DirectoryReader, filesystem.FileWriter, filesystem.File,
        window.Window, window.WindowProperties,
    ),
    {
        "location": lambda owner: bom.Location(services=owner._services),
        "screen": lambda owner: bom.Screen(environment=BrowserEnvironment.from_value(getattr(getattr(owner._services, "realm_services", None), "environment", None))),
        "localStorage": lambda owner: bom.Storage(),
        "sessionStorage": lambda owner: bom.Storage(),
        "history": lambda owner: bom.History(services=owner._services, initial_url=owner._history_initial_url),
    },
)
