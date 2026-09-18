"""``window.chrome``, the object Chromium installs through V8 extras.

A page always finds a plain object owning ``loadTimes``, ``csi`` and ``app`` in
that order, and the global property itself is writable and enumerable while
staying non-configurable (Chromium keeps it, so scripts may still replace the
value but not delete or redefine the slot).  The members are native functions
backed by this module, so ``String(chrome.csi)`` reports native code, exactly
like the real binding does.

The object is built by :data:`_BOOTSTRAP` in JavaScript rather than by a host
class: a host class would leak its own prototype and constructor, while
``chrome`` has to be an ordinary object
(``Object.getPrototypeOf(chrome) === Object.prototype``).
"""
import time

import v8n

__all__ = ["ChromeBindings", "install_window_chrome"]

# Blink reports a normal navigation as 15 (WebNavigationType::kNavigationTypeOther).
_TRANSITION_TYPE = 15
# Navigation milestones in milliseconds after navigationStart.  A milestone that
# has not been reached yet is reported as 0, the way Chromium reports timing
# fields it has no value for; the numbers themselves are the usual order of
# magnitude of a document that was already parsed when the script runs.
_MILESTONES = (
    ("requestTime", 0.4),
    ("startLoadTime", 0.6),
    ("commitLoadTime", 22.5),
    ("finishDocumentLoadTime", 45.2),
    ("finishLoadTime", 78.4),
    ("firstPaintTime", 62.1),
)
_LOAD_TIME_FIELDS = (
    "requestTime", "startLoadTime", "commitLoadTime", "finishDocumentLoadTime",
    "finishLoadTime", "firstPaintTime", "firstPaintAfterLoadTime",
    "navigationType", "wasFetchedViaSpdy", "wasNpnNegotiated",
    "npnNegotiatedProtocol", "wasAlternateProtocolAvailable", "connectionInfo",
)
_APP_FUNCTIONS = (
    "getDetails", "getIsInstalled", "installState", "runningState",
)
# Windows' time.time() only ticks every ~15ms, while Chromium reports
# sub-millisecond timings: anchor the high-resolution clock to the epoch once.
_EPOCH_OFFSET_MS = time.time() * 1000.0 - time.perf_counter() * 1000.0


def _now_ms():
    """Current time in epoch milliseconds with the resolution Chrome reports."""
    return time.perf_counter() * 1000.0 + _EPOCH_OFFSET_MS
# Temporary global used to hand the native functions to the bootstrap; the
# bootstrap deletes it again in the same synchronous step.
_BRIDGE_GLOBAL = "__pyv8cChromeHost__"

_BOOTSTRAP = """
(() => {
    const host = globalThis.__BRIDGE__;
    const app = {
        isInstalled: false,
        getDetails: host.getDetails,
        getIsInstalled: host.getIsInstalled,
        installState: host.installState,
        runningState: host.runningState,
        InstallState: {DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'},
        RunningState: {CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'},
    };
    const chrome = {loadTimes: host.loadTimes, csi: host.csi, app: app};
    try {
        Object.defineProperty(globalThis, 'chrome', {
            value: chrome, writable: true, enumerable: true, configurable: false,
        });
    } finally {
        delete globalThis.__BRIDGE__;
    }
})();
""".replace("__BRIDGE__", _BRIDGE_GLOBAL)


class ChromeBindings:
    """Python side of the ``chrome`` object; its methods back the native functions."""

    def __init__(self, services=None):
        self._services = services
        self._document = None
        self._navigation_start = 0.0

    def _navigation_started_at(self):
        """Epoch milliseconds of the current document's navigationStart.

        A new document restarts the navigation clock, so the reported times stay
        tied to the document that is loaded right now.
        """
        services = self._services
        document = getattr(getattr(services, "document_services", None), "document", None)
        if document is not self._document:
            self._document = document
            self._navigation_start = _now_ms()
        return self._navigation_start

    def loadTimes(self):
        start = self._navigation_started_at()
        now = _now_ms()
        values = {name: start + offset for name, offset in _MILESTONES}
        values = {name: value if value <= now else 0.0 for name, value in values.items()}
        values.update(
            firstPaintAfterLoadTime=0.0,
            navigationType="Other",
            wasFetchedViaSpdy=True,
            wasNpnNegotiated=True,
            npnNegotiatedProtocol="h2",
            wasAlternateProtocolAvailable=False,
            connectionInfo="h2",
        )
        return {field: values[field] for field in _LOAD_TIME_FIELDS}

    def csi(self):
        start = self._navigation_started_at()
        now = _now_ms()
        loaded = start + dict(_MILESTONES)["finishLoadTime"]
        return {
            "startE": start,
            "onloadT": loaded if loaded <= now else 0.0,
            "pageT": now,
            "tran": _TRANSITION_TYPE,
        }

    def getDetails(self):
        return v8n.Null

    def getIsInstalled(self):
        return False

    def installState(self):
        return "not_installed"

    def runningState(self):
        return "cannot_run"


def install_window_chrome(realm, services=None):
    """Install ``window.chrome`` for one Window realm.

    Chromium runs this before any page script, so the object is present for
    inline scripts in the document head as well.  Worker globals do not get one.
    """
    bindings = ChromeBindings(services)
    # The functions cross as one host object: a dict converts to a plain
    # JavaScript object, so the bootstrap reads them by name.
    bridge = {name: getattr(bindings, name) for name in ("loadTimes", "csi", *_APP_FUNCTIONS)}
    # Installing the object is host work, not page behaviour: neither the bridge
    # global nor the descriptor of ``chrome`` may be reported as a trace event,
    # and a page that never touches ``chrome`` must produce no event at all.
    # Chromium's own V8 extras are equally invisible to instrumentation.
    hook = realm.trace_hook
    if hook is not None:
        realm.trace_hook = None
    try:
        setattr(realm.glob, _BRIDGE_GLOBAL, bridge)
        realm.evaluate(_BOOTSTRAP, filename="chrome.js")
    finally:
        if hook is not None:
            realm.trace_hook = hook
