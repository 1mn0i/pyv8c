"""Realm-local binding state, and the one batch a realm is created with.

The previous session kept a mapping of compiled specs per realm and committed
new ones as they were materialised, one native call per interface.  A batch
description replaces both halves of that: the realm's eager interfaces are
described once, handed over once, and every other exposed name is built the
first time something reads it.

Building a description and installing it are deliberately separate.  A
description is pure: the realm it is handed to may still fail to be created, and
a session that had already recorded the names would then describe a realm that
does not exist.

Exposed names are not template properties.  The binding can declare them that
way, and it is what the previous version did, but a global object that carries
more than about a thousand properties stops being a fast-properties object to
V8 and the next global load aborts inside the engine.  pyv8c exposes every name
the baseline says a Window has, which is more than that, so the names are served
by the global object's own interceptor instead: one handler, no property per
name, and a name becomes a real property only once a page actually reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .description import InterfaceDescription


@dataclass(frozen=True)
class RealmBatch:
    """One realm's own batch, and the names it installs.

    The names travel beside the description because a description cannot be
    read back for them cheaply, and the session needs them the moment the realm
    exists: they are what ``materialized_interfaces`` counts and what keeps a
    later materialization from re-installing an interface the realm has.
    """

    description: dict
    names: tuple[str, ...]


class BindingSession:
    """Registration state for one V8 realm."""

    def __init__(self, universe, realm=None):
        self.universe = universe
        #: The realm token callbacks are bound to.  It is pyv8c's own object --
        #: a services facade on a page, the scope on a worker -- not the native
        #: realm, which arrives through :meth:`attach_realm`.
        self.realm = realm
        self._installed: set[str] = set()
        self._native = None
        self._exposed: dict[str, tuple[str, ...]] = {}
        self._exposed_sets: dict[str, frozenset[str]] = {}
        #: Names whose install is on the stack, innermost last.
        self._materializing: set[str] = set()
        #: Names in a batch that is being installed right now.
        #:
        #: They are neither installed nor absent, and the difference matters:
        #: installing an interface can cross a Python object of a type in the
        #: same batch, and a host that answered "not installed" would install it
        #: a second time -- which the realm refuses, because one Python type
        #: belongs to one interface.
        self._installing: set[str] = set()
        #: Python types already asked about, whether or not they had an interface.
        self._resolved_types: set[type] = set()
        self._known: frozenset[str] | None = None
        #: The context type this realm was described for, which is what says
        #: which names it exposes.  Set by :meth:`realm_description`.
        self._context_type = "Window"
        self._on_materialize = None

    # -- the native realm ---------------------------------------------------

    def attach_realm(self, native, names=(), on_materialize=None):
        """Record the native realm, and which names its own batch installed.

        Called once the realm object exists, which is the first moment a name
        can be said to be installed rather than merely described.
        """
        self._native = native
        self._installed.update(names)
        self._on_materialize = on_materialize

    def detach_realm(self):
        self._native = None
        self._installed.clear()
        self._exposed.clear()
        self._exposed_sets.clear()
        self._on_materialize = None
        self.realm = None

    # -- queries the host and the web layer ask -----------------------------

    def interface_names(self, context_type="Window"):
        return self.universe.interface_names(context_type)

    def class_for(self, name):
        return self.universe.class_for(name)

    @property
    def materialized_interfaces(self):
        return tuple(sorted(self._installed))

    def interface_materialized(self, name):
        return name in self._installed or name in self._installing

    def exposed_names(self, context_type="Window"):
        """Names this realm exposes without pre-building them."""
        if context_type not in self._exposed:
            eager = set(self.universe.eager_names(context_type))
            self._exposed[context_type] = tuple(
                name for name in self.interface_names(context_type) if name not in eager
            )
            self._exposed_sets[context_type] = frozenset(self._exposed[context_type])
        return self._exposed[context_type]

    def interface_exposed(self, name, context_type="Window"):
        """Whether this realm may answer *name* with an interface.

        A set rather than a scan of the name table, because this is asked once
        per read of a global name that is not already a property -- which is
        every feature-detection probe a page makes.

        A name whose own install is on the stack answers no.  Installing an
        interface makes V8 look the name up again before the property exists,
        and answering the same way twice would build it forever; the native
        lazy-property form carried the same guard for the same reason.
        """
        self.exposed_names(context_type)
        if name in self._materializing:
            return False
        return name in self._exposed_sets.get(context_type, frozenset())

    # -- descriptions -------------------------------------------------------

    def realm_description(self, context_type="Window", *, roots=()):
        """The batch that creates this realm: its eager interfaces, and nothing else.

        One description rather than one call per interface, because the realm's
        global object template has to exist before its context does: the batch
        is a construction parameter, not something installed afterwards.

        Pure.  Call :meth:`attach_realm` with the returned names once the realm
        exists, so a realm that failed to be created leaves no trace here.
        """
        builder = InterfaceDescription()
        self._context_type = context_type
        names = self._ordered_names(self._resolve_names(context_type, roots))
        for name in names:
            # The host interface is exposed on its own global object in every
            # embedded engine there is, and the rest only when this context type
            # is one the interface is exposed in.  An ancestor that travelled
            # with a child -- WindowProperties behind Window -- is installed and
            # left unnamed, which is what a page finds there too.
            exposed = name in roots or self._is_exposed(name)
            builder.add_plan(self.universe.plan_for(name, self.realm), exposed=exposed)
        return RealmBatch(builder.description(), names)

    def _is_exposed(self, name):
        """Whether this realm's context type exposes *name* as a global."""
        return name in frozenset(self.interface_names(self._context_type))

    def prepare_interface(self, value):
        """Install the interface a Python object stands for, before it crosses.

        The binding calls this for every object whose type has no interface in
        this realm yet, which is how an object built in Python arrives in
        JavaScript carrying its prototype and its members instead of as a
        generic host object whose properties are Python attributes.

        Types are remembered, so a plain host object costs one set lookup per
        type, and a type that does have an interface is never asked about again:
        installing it registers the type with the realm, which is what the
        binding looks up first next time.
        """
        kind = type(value)
        if kind in self._resolved_types:
            return
        self._resolved_types.add(kind)

        # Two declarations can be in play: the registration an @interface
        # decorator leaves, which is what names the interface, and the members a
        # class declares for itself.  A class with neither has no interface and
        # is not this method's business.
        registration = getattr(kind, "__idl_spec__", None)
        declaration = getattr(kind, "__v8_idl_spec__", None)
        if (registration is None and declaration is None) or self._native is None:
            return
        # A private facade in pyv8c declares members and no name, and then the
        # class's own name is the interface name it is installed under.
        name = (getattr(registration, "name", None)
                or getattr(declaration, "name", None)
                or kind.__name__)
        if name in self._materializing or self.interface_materialized(name):
            return
        if name in self._known_names():
            self.materialize(name)
            return
        self._install_declared(kind, declaration or registration, name)

    def _install_declared(self, kind, declaration, name):
        """Install one class's own declaration, for a name the baseline lacks.

        ``WindowProxy`` is what this exists for: a page reaches one through an
        iframe's ``contentWindow``, and JavaScript has to see an object of its
        type with its members rather than a generic host object.  It is installed
        under its name but not exposed as one -- it is a facade pyv8c defines,
        not an interface a renderer reports, so ``typeof WindowProxy`` stays
        undefined.
        """
        builder = InterfaceDescription()
        builder.add_plan(
            self.universe.plan_declared(kind, name, declaration, self.realm), exposed=False,
        )
        self._installing.add(name)
        try:
            self._native.install(builder.description())
        finally:
            self._installing.discard(name)
        self._installed.add(name)

    def _known_names(self):
        if self._known is None:
            self._known = frozenset(self.universe.baseline.interfaces)
        return self._known

    def materialize(self, name):
        """Install one interface into the attached realm now, and return its class.

        This is what the global object's interceptor calls on the first read of
        an exposed name: the install writes the name onto the global object, so
        the read that triggered it -- and every later one -- is answered by an
        ordinary property.
        """
        if self._native is None:
            raise RuntimeError("this binding session is not attached to a realm")
        description, names = self._description_for(name)
        if names:
            self._materializing.add(name)
            self._installing.update(names)
            try:
                self._native.install(description)
            finally:
                self._materializing.discard(name)
                self._installing.difference_update(names)
            # Recorded only after the install returns: it raises on failure, and
            # a name that was not installed must not look as if it were.
            self._installed.update(names)
            if self._on_materialize is not None:
                self._on_materialize(name)
        return self.universe.class_for(name)

    def exposed_interface(self, name):
        """Install one exposed interface and answer with its constructor.

        The install writes the name onto the global object, so the constructor
        is read back from there: it is the function JavaScript will use, not a
        second one that stands for it.  Answering None for a name whose own
        install is on the stack is what keeps that read honest -- the property
        does not exist yet, and reading it would call the interceptor that asked
        in the first place, forever.
        """
        if self._native is None or name in self._installing:
            return None
        self.materialize(name)
        return self._native.glob.get(name)

    # -- internals ----------------------------------------------------------

    def _resolve_names(self, context_type, roots):
        """Every name the realm installs: its exposed set, the roots, and groups.

        A feature's interfaces are installed as one group, so a name pulled in
        from another feature brings that feature's whole atomic set with it --
        which is what keeps the group's members from being split across two
        installs.
        """
        names = set(self.universe.resolve_names(context_type, roots))
        for name in tuple(names):
            names.update(self.universe.runtime.interface_group(name))
        for name in tuple(names):
            names.update(self.universe.walk_parent_names(name))
        return names

    def _ordered_names(self, names):
        """Ancestors first, so a child never names a parent that is not there.

        The binding resolves parents within a batch anyway, but ordering it here
        means an install failure names the interface that was actually wrong
        rather than the child that referred to it.
        """
        order, seen = [], set()
        for name in sorted(names):
            for interface_name in reversed(tuple(self.universe.walk_parent_names(name))):
                if interface_name not in seen:
                    seen.add(interface_name)
                    order.append(interface_name)
        return tuple(order)

    def _description_for(self, name):
        """A batch for *name*: its feature's group, and every ancestor still needed.

        The binding refuses a parent it has neither in the batch nor installed,
        so the outstanding part of the chain travels with the name that needs
        it.  A name already installed is left out, which is what makes the
        second read of an exposed interface free.

        A feature that declares itself atomic -- IndexedDB, whose database,
        transaction and request objects are useless apart -- brings its whole
        group, which is what the realm's own description does for a name that is
        installed at startup.  Splitting one of those across two installs would
        leave the first batch holding an interface whose group is not there.
        """
        builder = InterfaceDescription()
        wanted = set(self.universe.runtime.interface_group(name))
        wanted.add(name)
        for interface_name in tuple(wanted):
            wanted.update(self.universe.walk_parent_names(interface_name))
        names = [
            interface_name
            for interface_name in self._ordered_names(wanted)
            if interface_name not in self._installed
            and interface_name not in self._installing
        ]
        for interface_name in names:
            builder.add_plan(
                self.universe.plan_for(interface_name, self.realm),
                exposed=self._is_exposed(interface_name),
            )
        return builder.description(), tuple(names)
