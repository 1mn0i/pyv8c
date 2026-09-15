"""The built-in FeatureCatalog.  Keep this module declaration-only."""

from ...engine.features import FeatureCatalog, FeatureSpec

CORE_INTERFACES = (
    "DOMException", "DOMError", "DOMTokenList", "HTMLAllCollection", "HTMLCollection", "NamedNodeMap", "NodeList",
    "CustomEvent", "Event", "EventTarget", "KeyboardEvent", "MouseEvent", "UIEvent",
    "Node", "DocumentFragment", "Attr", "CharacterData", "Text", "Comment", "DocumentType", "Document",
    "CSSStyleDeclaration", "Element", "HTMLElement", "HTMLIFrameElement", "HTMLImageElement", "HTMLLinkElement", "HTMLMetaElement", "CanvasRenderingContext2D",
    "HTMLScriptElement", "HTMLStyleElement", "HTMLTemplateElement", "HTMLHtmlElement", "HTMLHeadElement",
    "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement", "HTMLAnchorElement", "HTMLInputElement",
    "HTMLFormElement", "HTMLCanvasElement", "HTMLMediaElement", "HTMLTableElement", "HTMLTableRowElement",
    "HTMLTableCellElement", "HTMLSelectElement", "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement",
    "HTMLUnknownElement", "Headers", "Blob", "Request", "Response", "MessageEvent", "CloseEvent", "ErrorEvent",
    "XMLHttpRequestEventTarget", "XMLHttpRequest", "Location", "Screen", "Storage", "URLSearchParams", "URL",
    "History", "DOMFileSystem", "Entry", "DirectoryEntry", "FileEntry", "DirectoryReader", "FileWriter", "File", "Window", "WindowProperties",
)

INDEXEDDB_INTERFACES = (
    "DOMStringList", "IDBRecord", "IDBVersionChangeEvent", "IDBRequest", "IDBOpenDBRequest", "IDBKeyRange",
    "IDBCursor", "IDBCursorWithValue", "IDBTransaction", "IDBObjectStore", "IDBIndex", "IDBDatabase", "IDBFactory",
)
NAVIGATOR_INTERFACES = (
    "MimeType", "Plugin", "PluginArray", "MimeTypeArray", "Geolocation",
    "NetworkInformation", "BatteryManager", "DeprecatedStorageQuota", "Navigator",
)
WORKER_INTERFACES = ("WorkerGlobalScope", "DedicatedWorkerGlobalScope", "Worker")


CATALOG = FeatureCatalog((
    FeatureSpec("core", "pyv8c.web.features.core", CORE_INTERFACES,
                ("location", "screen", "localStorage", "sessionStorage", "history"), eager=True, atomic=False),
    FeatureSpec("navigator", "pyv8c.web.bom.navigator", NAVIGATOR_INTERFACES, ("navigator",)),
    FeatureSpec("indexeddb", "pyv8c.web.bom.indexeddb", INDEXEDDB_INTERFACES, ("indexedDB",), requires=("core",)),
    FeatureSpec("websocket", "pyv8c.web.network.websocket", ("WebSocket",), requires=("core",)),
    FeatureSpec("workers", "pyv8c.web.workers.api", WORKER_INTERFACES, requires=("core",)),
))
