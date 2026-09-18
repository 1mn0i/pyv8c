"""One shared sink per realm; browser classes carry no trace methods.

The binding reports an event as ``(event, member, detail)`` and nothing more --
see ``v8n.trace`` for why -- so the realm a line came from is supplied here, by
binding it into the sink a realm installs.  That keeps the log readable without
asking the binding to report something it deliberately does not have.

A host may replace the sink.  What it receives is
``(event, member, detail, scope)``, four arguments rather than the binding's
three, so a process with several realms can tell them apart; returning a string
replaces the formatted line, and returning ``None`` logs the default one.
"""

from loguru import logger

from v8n.trace import format_trace


def log_trace(event, member, detail="", scope=""):
    """The default sink: format one event and write it through loguru."""
    message = format_trace(event, member, detail, scope)
    if message is not None:
        logger.info(message)
    return message


def trace_sink(scope, callback=None):
    """The hook one realm installs, with *scope* bound into it.

    Which sink to use is decided once, here, rather than on every event: the
    default path is the one every traced realm runs, and it should not pay for
    a comparison it can only ever answer one way.
    """
    if callback is None or callback is log_trace:
        def sink(event, member, detail=""):
            log_trace(event, member, detail, scope)
    else:
        def sink(event, member, detail=""):
            message = format_trace(event, member, detail, scope)
            answer = callback(event, member, detail, scope)
            if answer is not None:
                message = answer
            if message is not None:
                logger.info(message)
    sink.__name__ = f"_pyv8c_trace_{scope}"
    return sink


__all__ = ["log_trace", "trace_sink"]
