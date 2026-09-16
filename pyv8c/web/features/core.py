"""Eager feature implementation for page-essential Web APIs."""

from ...engine.builtin_errors import implementations as error_interfaces
from ...engine.features import FeatureImplementation
from ...foundation.environment import BrowserEnvironment
from ..bom import navigation, screen, storage, url, window
from ..dom import (
    character_data,
    collections,
    document,
    events,
    html_canvas,
    html_classes,
    html_elements,
    html_input,
    nodes,
)
from ..fetch import blob, headers, request, response, xhr
from ..fetch import events as fetch_events


def _interfaces(*classes):
    return {cls.__idl_spec__.name: cls for cls in classes}


FEATURE = FeatureImplementation(
    _interfaces(
        *error_interfaces(),
        collections.DOMTokenList, collections.HTMLAllCollection, collections.HTMLCollection, collections.NamedNodeMap, collections.NodeList,
        events.CustomEvent, events.Event, events.EventTarget, events.KeyboardEvent, events.MouseEvent, events.UIEvent,
        events.AnimationEvent, events.BeforeUnloadEvent, events.CompositionEvent, events.FocusEvent,
        events.HashChangeEvent, events.InputEvent, events.PageTransitionEvent, events.PointerEvent,
        events.PopStateEvent, events.PromiseRejectionEvent, events.StorageEvent, events.TransitionEvent,
        events.WheelEvent,
        nodes.Node, nodes.DocumentFragment, nodes.Attr,
        character_data.CharacterData, character_data.Text, character_data.Comment, character_data.DocumentType,
        document.Document, document.DOMImplementation,
        html_elements.CSSStyleDeclaration, html_elements.Element, html_elements.HTMLElement,
        html_elements.HTMLIFrameElement, html_elements.HTMLImageElement, html_elements.HTMLLinkElement,
        html_elements.HTMLMetaElement, html_elements.HTMLScriptElement, html_elements.HTMLStyleElement,
        html_elements.HTMLTemplateElement,
        html_canvas.HTMLCanvasElement, html_canvas.CanvasRenderingContext2D,
        html_input.ValidityState, html_input.FileList,
        *(html_classes.HTML_CLASSES[name] for name in (
            "HTMLHtmlElement", "HTMLHeadElement", "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement",
            "HTMLAnchorElement", "HTMLInputElement", "HTMLFormElement", "HTMLCanvasElement", "HTMLMediaElement",
            "HTMLTableElement", "HTMLTableRowElement", "HTMLTableCellElement", "HTMLSelectElement",
            "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement", "HTMLUnknownElement",
        )),
        headers.Headers, blob.Blob, request.Request, response.Response, fetch_events.MessageEvent, fetch_events.CloseEvent,
        fetch_events.ErrorEvent, fetch_events.ProgressEvent, xhr.XMLHttpRequestEventTarget, xhr.XMLHttpRequest,
        navigation.Location, navigation.History, screen.Screen, storage.Storage,
        url.URLSearchParams, url.URL,
        window.Window, window.WindowProperties,
    ),
    {
        "location": lambda owner: navigation.Location(services=owner._services),
        "screen": lambda owner: screen.Screen(environment=BrowserEnvironment.from_value(getattr(getattr(owner._services, "realm_services", None), "environment", None))),
        "localStorage": lambda owner: storage.Storage(),
        "sessionStorage": lambda owner: storage.Storage(),
        "history": lambda owner: navigation.History(services=owner._services, initial_url=owner.location.href),
    },
)
