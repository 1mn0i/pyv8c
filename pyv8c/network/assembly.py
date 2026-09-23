"""Assembling one context's engines from the options the Browser was built with."""
from __future__ import annotations

from .backend import NetworkBackend
from .registry import create_transport

#: The method that makes an object an engine for each role.
_SERVES = {"http": "request_sync", "ws": "websocket"}


def _options(browser, role):
    """The *role* engine's options, as a copy; ``None`` when none were set."""
    return dict(getattr(browser, f"{role}_options")) or None


def _serves(candidate, role):
    """Whether *candidate* can perform this role."""
    return hasattr(candidate, _SERVES[role])


def _engine(browser, role, value):
    """One role's engine: a registered name, a ready object, or a factory.

    A name is built from the registry with that role's options; a factory is
    called per context.  A ready engine can take no options -- it was built
    before the context existed -- which is refused rather than ignored.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return create_transport(value, kind=role, options=_options(browser, role), strict=True)
    engine = value() if callable(value) and not _serves(value, role) else value
    if not _serves(engine, role):
        raise TypeError(
            f"{role}_engine must be a registered engine name, an object with "
            f"{_SERVES[role]}, or a factory for one"
        )
    if getattr(browser, f"{role}_options"):
        raise TypeError(
            f"{role}_options cannot configure a ready {role}_engine; name a "
            "registered engine to pass options to it"
        )
    return engine


def _refuse_unconsumed_options(browser):
    """Reject options no engine would read, instead of dropping them quietly."""
    given = [role for role in ("http", "ws") if getattr(browser, f"{role}_options")]
    if given:
        listed = " and ".join(f"{role}_options" for role in given)
        raise TypeError(
            f"{listed} was given but no network engine is configured; "
            "set real_network=True, http_engine or ws_engine"
        )


def _refuse_ready_engines(browser):
    """A name is only a preference; a ready engine would be dropped, so it is refused."""
    for role in ("http", "ws"):
        value = getattr(browser, f"{role}_engine")
        if value is not None and not isinstance(value, str):
            raise TypeError(
                f"real_network=False assembles no engines, so {role}_engine has "
                "nothing to serve; set real_network=True to use it"
            )


def _refuse_shared_options(browser, value):
    """One engine cannot be configured twice; say which options to move."""
    if browser.ws_options:
        raise TypeError(
            f"http_engine and ws_engine both give {value!r}, which is built once, "
            "so ws_options would be ignored; give the WebSocket role its own engine "
            "or move those settings into http_options"
        )


def assemble_backend(browser):
    """Assemble one context's engines from what the Browser was built with.

    ``real_network=False`` assembles neither role, and options are checked against
    the engine that will read them rather than ignored.
    """
    if not browser.real_network:
        _refuse_ready_engines(browser)
        _refuse_unconsumed_options(browser)
        return None

    http = _engine(browser, "http", browser.http_engine)
    ws_value = browser.ws_engine
    if ws_value is None:
        ws = None
    elif ws_value is browser.http_engine or (
        isinstance(ws_value, str) and ws_value == browser.http_engine
    ):
        # One name, one engine: a second build means a second client and pool.
        _refuse_shared_options(browser, ws_value)
        ws = http
    else:
        ws = _engine(browser, "ws", ws_value)

    if http is None and ws is None:
        _refuse_unconsumed_options(browser)
        return None
    return NetworkBackend(http, ws)
