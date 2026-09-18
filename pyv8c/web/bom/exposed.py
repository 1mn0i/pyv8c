"""Exposing a realm's interface names through its global object.

A window exposes about a thousand interface names and a page touches a dozen of
them.  The binding can declare them one template property each, and that is what
an earlier version of this layer did, but V8 stops treating a global object as a
fast-properties object somewhere around a thousand properties and the next
global load then aborts inside the engine rather than failing in a way a host
can catch.  The count is the problem, not the names: nine hundred properties
read fine, a thousand do not.

So the names are answered by the global object's own named-property interceptor
instead.  An interceptor is not a property, so a thousand names cost one handler
rather than a thousand properties; and because building an interface writes its
name onto the global object, a name becomes a real property the first time
anything reads it -- after which the read does not come back here at all.

The trade is one Python call for the first read of each name a page actually
touches, which is what laziness costs in any case: it is the same work the
binding's own lazy properties did, done where the property budget is not a
limit.
"""

from __future__ import annotations

from v8n.flag import V8Attribute

__all__ = ["ExposedInterfacesMixin"]


class ExposedInterfacesMixin:
    """Answers a global interface's named properties with its exposed names.

    Mixed into every interface the host marks ``global_this``.  A global object
    is identified by the realm it is, and which names a realm exposes depends on
    its type, so a subclass says so with :attr:`_exposed_context_type`.
    """

    #: The realm type whose exposed names this global answers for.
    _exposed_context_type = "Window"

    def _exposed_services(self):
        """The realm services behind this global, or ``None`` when it has none.

        A global object built without a realm -- by a test, or by an entry point
        that hands it a bare environment -- declares no names rather than
        failing every property read.
        """
        services = getattr(self, "_services", None)
        return getattr(services, "realm_services", None)

    def __v8_name_get__(self, name):
        services = self._exposed_services()
        if services is None or not isinstance(name, str) or not name:
            return None
        if not services.interface_exposed(name, self._exposed_context_type):
            # Not an exposed interface: decline, so the lookup continues to the
            # prototype chain.  Answering None as a value would shadow every
            # name above this object instead.
            return None
        return services.exposed_interface(name)

    def __v8_name_query__(self, name):
        services = self._exposed_services()
        if services is None or not isinstance(name, str) or not name:
            return None
        if not services.interface_exposed(name, self._exposed_context_type):
            return None
        # Present and not enumerable, which is how the binding declared these
        # names when they were template properties: `in` and `window.Name`
        # answer, for-in and Object.keys do not.
        return V8Attribute.DontEnum

    def __v8_name_enum__(self):
        # Declining rather than listing: enumerating would have to build every
        # name in the realm to answer a question about how many there are.
        return None
