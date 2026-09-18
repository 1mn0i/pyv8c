"""The Inspector, of which only the configuration exists so far.

The previous binding shipped a DevTools service on top of an Inspector the
extension exposed: a Debugger per context, a machine-readable protocol router,
a scheduler that runs the debugger on the realm's own thread, and a socket
transport.  The kernel here has none of it -- there is no ``v8::debug`` session,
no pause control, and no way to observe a script before it runs -- so there is
nothing for a router to route.

What is here is the option record, because it is pure data and a host that
configures debugging should not have to change shape when the transport lands.
The rest arrives with the native layer, and until then a host is told so by
name rather than handed a session that cannot answer a protocol message.
"""

from .options import DevToolsOptions

#: Why a session cannot be started yet.  Named once here so every refusal and
#: every docstring says the same thing.
UNAVAILABLE = (
    "the Inspector is not implemented in this kernel: there is no debugger "
    "session to attach to.  See native/src/inspect, which does not exist yet"
)

__all__ = ["DevToolsOptions", "UNAVAILABLE"]
