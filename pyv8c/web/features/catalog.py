"""The built-in FeatureCatalog.  Keep this module declaration-only."""

from ...engine.features import FeatureCatalog, FeatureSpec

CORE_INTERFACES = (
    "DOMException", "DOMError", "DOMImplementation", "DOMStringMap", "DOMTokenList", "HTMLAllCollection", "HTMLCollection", "HTMLFormControlsCollection", "HTMLOptionsCollection", "NamedNodeMap", "NodeList", "RadioNodeList",
    "CustomEvent", "Event", "EventTarget", "KeyboardEvent", "MouseEvent", "UIEvent",
    "FocusEvent", "InputEvent", "CompositionEvent", "PointerEvent", "WheelEvent",
    "PopStateEvent", "HashChangeEvent", "StorageEvent", "AnimationEvent", "TransitionEvent",
    "PromiseRejectionEvent", "PageTransitionEvent", "BeforeUnloadEvent",
    "Node", "DocumentFragment", "Attr", "CharacterData", "Text", "Comment", "DocumentType", "Document",
    "CSSStyleDeclaration", "Element", "HTMLElement", "HTMLIFrameElement", "HTMLImageElement", "HTMLLinkElement", "HTMLMetaElement", "CanvasRenderingContext2D",
    "ValidityState", "FileList", "TextMetrics",
    "HTMLScriptElement", "HTMLStyleElement", "HTMLTemplateElement", "HTMLHtmlElement", "HTMLHeadElement",
    "HTMLBodyElement", "HTMLDivElement", "HTMLSpanElement", "HTMLAnchorElement", "HTMLInputElement",
    "HTMLFormElement", "HTMLCanvasElement", "HTMLMediaElement", "HTMLTableElement", "HTMLTableRowElement",
    "HTMLTableCellElement", "HTMLSelectElement", "HTMLTextAreaElement", "HTMLButtonElement", "HTMLOptionElement",
    "HTMLUnknownElement", "HTMLAudioElement", "HTMLVideoElement", "TextTrackList", "TimeRanges",
    "StyleSheetList", "ScreenOrientation", "PerformanceTiming", "PerformanceNavigation",
    "Headers", "Blob", "Request", "Response", "ProgressEvent", "MessageEvent", "CloseEvent", "ErrorEvent",
    "XMLHttpRequestEventTarget", "XMLHttpRequest", "Location", "Screen", "Storage", "URLSearchParams", "URL",
    "History", "Window", "WindowProperties", "BarProp", "MediaQueryList", "Performance", "Crypto",
    "MutationObserver", "MutationRecord", "CustomElementRegistry", "VisualViewport",
    "External", "StyleMedia", "CrashReportContext", "LaunchQueue", "SpeechSynthesis",
    "Scheduler", "Viewport", "ImageData", "Navigation", "NavigationActivation",
    "NavigationHistoryEntry",
    "TrustedHTML", "TrustedScript", "TrustedScriptURL", "TrustedTypePolicy",
    "TrustedTypePolicyFactory", "XPathEvaluator", "XPathExpression", "XPathResult",
    "Lock", "LockManager",
)

INDEXEDDB_INTERFACES = (
    "DOMStringList", "IDBRecord", "IDBVersionChangeEvent", "IDBRequest", "IDBOpenDBRequest", "IDBKeyRange",
    "IDBCursor", "IDBCursorWithValue", "IDBTransaction", "IDBObjectStore", "IDBIndex", "IDBDatabase", "IDBFactory",
)
NAVIGATOR_INTERFACES = (
    "MimeType", "Plugin", "PluginArray", "MimeTypeArray", "Geolocation",
    "NetworkInformation", "BatteryManager", "DeprecatedStorageQuota", "Navigator",
    "NavigatorUAData",
)
FILESYSTEM_INTERFACES = (
    "DOMFileSystem", "Entry", "DirectoryEntry", "FileEntry", "DirectoryReader", "FileWriter", "File",
)
WORKER_INTERFACES = ("WorkerGlobalScope", "DedicatedWorkerGlobalScope", "Worker")


CATALOG = FeatureCatalog((
    FeatureSpec("core", "pyv8c.web.features.core", CORE_INTERFACES,
                ("location", "screen", "localStorage", "sessionStorage", "history"), eager=True, atomic=False),
    FeatureSpec("navigator", "pyv8c.web.bom.navigator", NAVIGATOR_INTERFACES, ("navigator",)),
    FeatureSpec("indexeddb", "pyv8c.web.bom.indexeddb", INDEXEDDB_INTERFACES, ("indexedDB",), requires=("core",)),
    FeatureSpec("filesystem", "pyv8c.web.bom.filesystem", FILESYSTEM_INTERFACES, requires=("core",)),
    FeatureSpec("websocket", "pyv8c.web.websocket", ("WebSocket",), requires=("core",)),
    FeatureSpec("workers", "pyv8c.web.workers.api", WORKER_INTERFACES, requires=("core",)),
))
