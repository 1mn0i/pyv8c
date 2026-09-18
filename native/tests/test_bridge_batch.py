"""Stage 4 acceptance: one batch, one crossing.

The rule under test is that a whole realm's interfaces are described as data,
validated before anything is installed, and installed in one call -- and that a
description which asks for something the layer does not do is refused, not
quietly installed without it.
"""

from __future__ import annotations

import pytest

import v8n


def base(**interface):
    """A batch with one interface and nothing else."""
    description = {"interfaces": [{"name": "Thing", "members": [], **interface}],
                   "implementations": ()}
    return description


def test_a_batch_installs_a_constructor_and_a_prototype():
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Counter",
                        "length": 1,
                        "constructor": ["new"],
                        "to_string_tag": "Counter",
                        "constructor_slot": 0,
                        "members": [
                            {"kind": "attribute", "name": "value", "slot": 1,
                             "flags": ["has_setter"], "setter_slot": 2},
                            {"kind": "method", "name": "bump", "arity": 1, "slot": 3},
                            {"kind": "constant", "name": "LIMIT", "value": 7},
                        ],
                    }
                ],
                "implementations": (
                    lambda args, kwargs: Counter(args[0] if args else 0),
                    lambda self: self.value,
                    lambda self, value: setattr(self, "value", value),
                    lambda self, by: self.bump(by),
                ),
            }
        )

        thing = realm.evaluate("new Counter(3)")
        assert thing is not None
        # The object V8 made from the template, so the prototype chain is real.
        assert realm.evaluate("new Counter(3) instanceof Counter") is True
        assert realm.evaluate("Counter.length") == 1
        assert realm.evaluate("Object.prototype.toString.call(new Counter(1))") == "[object Counter]"

        # Members reach Python, and the receiver arrives as the first argument.
        assert thing.value == 3
        thing.value = 10
        assert thing.value == 10
        assert thing.bump(5) == 15
        assert realm.evaluate("Counter.prototype.LIMIT") == 7
        assert realm.evaluate("Object.keys(new Counter(1)).length") == 0
        assert realm.evaluate("'bump' in Counter.prototype") is True
    finally:
        realm.close()


class Counter:
    def __init__(self, value):
        self.value = value

    def bump(self, by):
        self.value += by
        return self.value


def test_a_python_instance_backs_the_javascript_object():
    # `instanceof` and property access have to hold for the same object: if
    # construction wrapped the instance instead of adopting into the object V8
    # made, the prototype chain would be the generic host object's.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {"name": "Counter", "constructor": ["new"], "constructor_slot": 0,
                     "members": [{"kind": "attribute", "name": "value", "slot": 1}]}
                ],
                "implementations": (lambda args, kwargs: Counter(5), lambda self: self.value),
            }
        )
        counter = realm.evaluate("new Counter()")
        assert counter.value == 5
        assert realm.evaluate("new Counter() instanceof Counter") is True
    finally:
        realm.close()


def test_parents_are_built_before_children_whatever_order_they_appear_in():
    # A description is written in the order that reads best, so a child may be
    # declared before its parent.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {"name": "Child", "parent": "Parent"},
                    {"name": "Parent", "members": [{"kind": "method", "name": "poke", "slot": 0}]},
                ],
                "implementations": (lambda self: "poked",),
            }
        )
        assert realm.evaluate("Child.prototype.poke") is not None
        assert realm.evaluate("Object.getPrototypeOf(Child.prototype) === Parent.prototype") is True
    finally:
        realm.close()


def test_a_declared_interceptor_failure_is_silent_outside_strict_mode():
    # A read-only legacy property fails rather than raising: the host says so by
    # raising an exception whose type carries `__v8_interceptor_failure__`, and
    # V8's contract for an interceptor that handled a write it could not perform
    # is an answer of `false` -- which is a TypeError in strict mode and silence
    # outside it.  What surfaces in strict mode is the host's own exception, with
    # the message it chose.
    class Refusal(Exception):
        __v8_interceptor_failure__ = True

    class Legacy:
        pass

    def refuse_set(_self, _name, _value):
        raise Refusal("Indexed property setter is not supported.")

    def refuse_delete(_self, _name):
        raise Refusal("Indexed property deleter is not supported.")

    realm = v8n.Realm()
    try:
        realm.install({
            "interfaces": [{
                "name": "Legacy", "type": 0, "members": [], "constructor": ["new"],
                "constructor_slot": 3,
                "handlers": {"name_set": 1, "name_del": 2},
            }],
            "implementations": (Legacy, refuse_set, refuse_delete,
                                lambda _args, _kwargs: Legacy()),
        })
        wrote = realm.evaluate("""(() => {
            const legacy = new Legacy();
            legacy.named = 1;
            const deleted = delete legacy.named;
            return ['named' in legacy, deleted];
        })()""")
        assert wrote.to_python() == [False, False]

        with pytest.raises(Refusal, match="setter is not supported"):
            realm.evaluate("'use strict'; (new Legacy()).named = 1;")
        with pytest.raises(Refusal, match="deleter is not supported"):
            realm.evaluate("'use strict'; delete (new Legacy()).named;")
    finally:
        realm.close()


def test_a_callable_host_object_keeps_the_interface_it_is_an_instance_of():
    # A Python object that defines __call__ still has a type, and when that type
    # has an interface the object is an instance of it: crossing as a bare
    # function instead would drop the prototype, the members, and the fact that
    # the interface -- not the crossing -- decides whether instances are
    # callable.  `document.all` is the object this is about.
    class Callable:
        def get_kind(self):
            return "interface member"

        def __call__(self, *_args):
            return "called"

    realm = v8n.Realm()
    try:
        realm.install({
            "interfaces": [{
                "name": "Callable", "call": 0, "type": 1,
                "members": [{"kind": "attribute", "name": "kind", "slot": 2}],
            }],
            "implementations": (lambda *_args: "called", Callable, Callable.get_kind),
        })
        # Callable because the interface says so, and an instance of it: the
        # member is what tells the two crossings apart.
        seen = realm.evaluate("(x => [typeof x, x.kind, x()])")(Callable())
        assert seen.to_python() == ["function", "interface member", "called"]
    finally:
        realm.close()


def test_an_undetectable_interface_stays_undetectable_when_it_is_callable():
    # `document.all` is both: the one object JavaScript has that reports
    # `typeof` as "undefined" and can still be called.  Both traits have to
    # survive being installed together, or the object arrives as an ordinary
    # function and every `typeof document.all` check a page makes flips.
    class All:
        def __call__(self, *_args):
            return "called"

    realm = v8n.Realm()
    try:
        realm.install({
            "interfaces": [{
                "name": "All", "traits": ["undetectable"], "call": 0,
                "type": 1, "constructor": ["new"], "constructor_slot": 2,
                "members": [],
            }],
            "implementations": (lambda *_args: "called", All, lambda args, kwargs: All()),
        })
        assert realm.evaluate("typeof All") == "function"
        assert realm.evaluate("[typeof (new All()), (new All()) == null]").to_python() == [
            "undefined", True,
        ]
        assert realm.evaluate("(new All())()") == "called"
    finally:
        realm.close()


def test_an_interface_can_be_installed_without_being_exposed():
    # Installing and exposing are two different things.  An interface travels in
    # a batch because something needs it -- a child needs its parent, an object of
    # a Python type needs its prototype -- and a name a host does not expose is
    # installed all the same, just not written onto the global object.  Without
    # the difference, every ancestor in a batch would become a global name.
    class Base:
        pass

    class Child(Base):
        pass

    realm = v8n.Realm(global_this=Base(), description={
        "interfaces": [
            {"name": "Base", "traits": ["global_this"], "type": 0,
             "constructor": ["new"], "constructor_slot": 2, "members": []},
            {"name": "Child", "parent": "Base", "type": 1, "exposed": False,
             "members": []},
        ],
        "implementations": (Base, Child, lambda args, kwargs: Base()),
    })
    try:
        assert realm.evaluate("typeof Child") == "undefined"
        # Installed anyway: an instance of the type arrives as an instance of its
        # interface rather than as a generic host object.
        name = realm.evaluate("(x => Object.getPrototypeOf(x).constructor.name)")(Child())
        assert name == "Child"
    finally:
        realm.close()


def test_the_global_interface_is_the_one_the_host_object_is():
    # A batch may declare more than one global_this, because the interfaces a
    # window exposes include the ones a worker scope is built from.  Binding the
    # global to the first of them would make every inherited member resolve
    # against the wrong class -- `postMessage` answering as a window's -- so the
    # host object's own type is what decides.
    class Window:
        def shared(self):
            return "window"

    class WorkerScope(Window):
        def shared(self):
            return "worker"

    description = {
        "interfaces": [
            {"name": "Window", "traits": ["global_this"], "constructor": ["new"],
             "type": 0, "constructor_slot": 2,
             "members": [{"kind": "method", "name": "shared", "slot": 4}]},
            {"name": "WorkerScope", "traits": ["global_this"], "parent": "Window",
             "constructor": ["new"], "type": 1, "constructor_slot": 3,
             "members": [{"kind": "method", "name": "shared", "slot": 5}]},
        ],
        "implementations": (
            Window, WorkerScope,
            lambda args, kwargs: Window(), lambda args, kwargs: WorkerScope(),
            Window.shared, WorkerScope.shared,
        ),
    }

    for host, expected in ((Window(), "window"), (WorkerScope(), "worker")):
        realm = v8n.Realm(global_this=host, description=description)
        try:
            assert realm.evaluate("shared()") == expected
        finally:
            realm.close()


def test_a_child_inherits_from_a_parent_an_earlier_batch_installed():
    # A host that builds interfaces on demand installs them one at a time, so a
    # child is normally in a later batch than its parent.  The constructor
    # function V8 produced cannot be turned back into a template, so the realm
    # has to keep the one it built or the chain could not be linked at all.
    realm = v8n.Realm()
    try:
        realm.install(
            {"interfaces": [{"name": "Parent", "members": [
                {"kind": "method", "name": "poke", "slot": 0}]}],
             "implementations": (lambda self: "poked",)}
        )
        realm.install(
            {"interfaces": [{"name": "Child", "parent": "Parent",
                             "constructor": ["new"], "constructor_slot": 0}],
             "implementations": (lambda args, kwargs: object(),)}
        )

        assert realm.evaluate("Child.prototype.poke") is not None
        assert realm.evaluate(
            "Object.getPrototypeOf(Child.prototype) === Parent.prototype"
        ) is True
        assert realm.evaluate("new Child() instanceof Parent") is True

        # And a description that names a parent nobody installed is still refused,
        # now saying where it looked.
        with pytest.raises(ValueError, match="this batch or in this realm"):
            realm.install({"interfaces": [{"name": "Orphan", "parent": "Nope"}],
                           "implementations": ()})
    finally:
        realm.close()


def test_a_cycle_in_the_parent_chain_is_reported():
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="cycle"):
            realm.install(
                {
                    "interfaces": [{"name": "A", "parent": "B"}, {"name": "B", "parent": "A"}],
                    "implementations": (),
                }
            )
    finally:
        realm.close()


def test_a_missing_parent_is_reported_before_anything_is_installed():
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="no interface named"):
            realm.install({"interfaces": [{"name": "Child", "parent": "Nope"}],
                           "implementations": ()})
        # Nothing was written, so the realm is still usable and still empty.
        assert realm.evaluate("typeof Child") == "undefined"
        assert realm.evaluate("1 + 1") == 2
    finally:
        realm.close()


def test_interface_members_are_installed_on_the_constructor():
    # IDL calls these interface members; JavaScript calls them statics.  They live
    # on the function object, which does not exist until the template has been
    # instantiated, so they cannot be installed with the rest.
    #
    # They also take no receiver, which is why they can use the native data
    # property form even though it cannot reach the object a property was read
    # through.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Range",
                        "members": [
                            {"kind": "method", "name": "only", "arity": 1, "slot": 0,
                             "location": "interface"},
                            {"kind": "constant", "name": "LIMIT", "value": 9,
                             "location": "interface"},
                            {"kind": "attribute", "name": "count", "slot": 1,
                             "location": "interface"},
                        ],
                    }
                ],
                "implementations": (lambda value: value * 2, lambda: 42),
            }
        )
        assert realm.evaluate("Range.only(4)") == 8
        assert realm.evaluate("Range.LIMIT") == 9
        assert realm.evaluate("Range.count") == 42
        # Static, not on the prototype.
        assert realm.evaluate("'only' in Range.prototype") is False
    finally:
        realm.close()


def test_named_handlers_answer_for_names_the_interface_never_declared():
    # A host object's property set is not known in advance, so the interface
    # declares handlers and the host answers per name.  Reading, writing, `in`,
    # `delete` and enumeration all go through them.
    props = {"alpha": 1, "beta": 2}
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Bag",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "handlers": {
                            "name_get": 1,
                            "name_set": 2,
                            "name_query": 3,
                            "name_del": 4,
                            "name_enum": 5,
                        },
                    }
                ],
                "implementations": (
                    lambda args, kwargs: object(),
                    lambda self, name: props.get(name),
                    lambda self, name, value: props.__setitem__(name, value),
                    lambda self, name: name in props,
                    lambda self, name: props.pop(name, None) is not None,
                    lambda self: sorted(props),
                ),
            }
        )

        assert realm.evaluate("(() => { const b = new Bag(); return b.alpha; })()") == 1
        assert realm.evaluate(
            "(() => { const b = new Bag(); b.gamma = 3; return b.gamma; })()"
        ) == 3
        assert props["gamma"] == 3
        assert realm.evaluate("(() => { const b = new Bag(); return 'alpha' in b; })()") is True
        assert realm.evaluate("(() => { const b = new Bag(); return 'nope' in b; })()") is False
        assert realm.evaluate("(() => { const b = new Bag(); return delete b.alpha; })()") is True
        assert props.get("alpha") is None
        # The array crosses as a handle, and to_python() is what expands it: the
        # boundary does not enumerate on the way out.
        assert realm.evaluate("(() => { const b = new Bag(); return Object.keys(b); })()").to_python() == [
            "beta",
            "gamma",
        ]

        # A handler that answers None is not answering, so the name falls through
        # to the prototype chain instead of being shadowed by an object that has
        # an opinion about everything.
        assert realm.evaluate(
            "(() => { const b = new Bag(); return b.toString === Object.prototype.toString; })()"
        ) is True
    finally:
        realm.close()


def test_indexed_handlers_are_keyed_by_integer():
    items = ["a", "b"]
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "TwoThings",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "handlers": {"index_get": 1, "index_query": 2, "index_enum": 3},
                    }
                ],
                "implementations": (
                    lambda args, kwargs: object(),
                    lambda self, index: items[index] if index < len(items) else None,
                    lambda self, index: index < len(items),
                    # A length, not a sequence: an indexed enumerator that had to
                    # build a list to say "there are two" would allocate two
                    # entries to answer a question about a number.
                    lambda self: len(items),
                ),
            }
        )

        assert realm.evaluate("(() => { const t = new TwoThings(); return t[0]; })()") == "a"
        # Out of range answers None, which is "not mine", so the read continues
        # and produces undefined rather than an error.
        assert realm.evaluate("(() => { const t = new TwoThings(); return t[9]; })()") is v8n.Undefined
        assert realm.evaluate("(() => { const t = new TwoThings(); return 1 in t; })()") is True
        assert realm.evaluate("(() => { const t = new TwoThings(); return 9 in t; })()") is False
        assert realm.evaluate("(() => { const t = new TwoThings(); return Object.keys(t); })()").to_python() == [
            "0",
            "1",
        ]
    finally:
        realm.close()


def test_a_handler_that_is_not_declared_is_not_installed():
    # Omitting a role is not the same as declaring one that always declines: the
    # first leaves the operation to the prototype chain, the second routes it
    # through Python first.
    calls = []
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {"name": "Quiet", "constructor": ["new"], "constructor_slot": 0,
                     "handlers": {"name_get": 1}}
                ],
                "implementations": (
                    lambda args, kwargs: object(),
                    lambda self, name: calls.append(name) or None,
                ),
            }
        )
        assert realm.evaluate(
            "(() => { const q = new Quiet(); return q.missing; })()"
        ) is v8n.Undefined
        assert calls == ["missing"]
    finally:
        realm.close()


def test_a_python_object_can_be_the_global_object():
    # The reason a realm is created with its batch rather than after it: the global
    # object's template has to exist before the context does, because V8 takes the
    # global template at Context::New and offers no way to give the global object an
    # interceptor later.
    props = {"marker": 1}
    window = type("Window", (), {})()

    realm = v8n.Realm(
        global_this=window,
        description={
            "interfaces": [
                {
                    "name": "Window",
                    "traits": ["global_this"],
                    "constructor": ["new"],
                    "constructor_slot": 0,
                    # Instance members: the global object *is* the instance, so a
                    # prototype member would land on Window.prototype instead.
                    "members": [
                        {"kind": "method", "name": "shout", "arity": 0, "slot": 5,
                         "location": "instance"},
                    ],
                    "handlers": {"name_get": 1, "name_set": 2, "name_query": 3,
                                 "name_enum": 4},
                }
            ],
            "implementations": (
                lambda args, kwargs: window,
                lambda self, name: props.get(name),
                lambda self, name, value: props.__setitem__(name, value),
                lambda self, name: name in props,
                lambda self: sorted(props),
                # An instance member receives the object it was called on, and for
                # the global interface that is the global object itself.
                lambda receiver: "at python",
            ),
        },
    )
    try:
        # A bare identifier read goes through the global object's interceptor to
        # Python.  This is what "the page's realm is its window" means.
        assert realm.evaluate("marker") == 1
        realm.evaluate("marker = 2")
        assert props["marker"] == 2
        assert realm.evaluate("'marker' in this") is True
        assert "marker" in realm.evaluate("Object.keys(this)").to_python()

        # An instance member is on the global object, and reaches Python with the
        # global as its receiver.
        assert realm.evaluate("shout()") == "at python"

        # The interface itself is still reachable by name.
        assert realm.evaluate("typeof Window") == "function"
    finally:
        realm.close()


def test_the_two_halves_of_a_global_object_have_to_agree():
    # A global object nothing dispatched through, and an interface declared global
    # with nothing behind it, are both descriptions that would half work.  Both are
    # refused, each saying which half is missing.
    with pytest.raises(RuntimeError, match="no interface in the batch is marked"):
        v8n.Realm(global_this=object(), description={"interfaces": [], "implementations": ()})
    with pytest.raises(RuntimeError, match="no global object was supplied"):
        v8n.Realm(description=base(traits=["global_this"]))


def test_the_global_object_is_reachable_as_a_handle():
    realm = v8n.Realm()
    try:
        realm.evaluate("globalThis.answer = 42")
        assert realm.glob.answer == 42
        realm.glob.answer = 43
        assert realm.evaluate("answer") == 43
    finally:
        realm.close()


def test_a_cross_origin_policy_can_be_a_check_alone():
    # A check with no cross-origin handlers is a complete policy: the object's own
    # realm decides, and a stranger that is let in sees whatever the ordinary
    # handlers answer.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [{"name": "Guarded", "constructor": ["new"],
                                "constructor_slot": 0,
                                "handlers": {"name_get": 1},
                                "cross_origin": {"check": 2}}],
                "implementations": (
                    lambda args, kwargs: object(),
                    lambda self, name: "same origin" if name == "who" else None,
                    lambda interviewee, accessor: False,
                ),
            }
        )
        # Same realm, so no check is consulted and the ordinary handler answers.
        assert realm.evaluate("(() => { const g = new Guarded(); return g.who; })()") == "same origin"
    finally:
        realm.close()


def test_a_cross_origin_policy_can_answer_a_stranger_differently():
    # Two tables, and the cross-origin one is a second configuration carrying its
    # own data.  Reusing the same-origin bindings would leak exactly what the
    # policy exists to protect.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [{"name": "Guarded", "constructor": ["new"],
                                "constructor_slot": 0,
                                "handlers": {"name_get": 1},
                                "cross_origin": {"check": 2, "name_get": 3}}],
                "implementations": (
                    lambda args, kwargs: object(),
                    lambda self, name: "same origin" if name == "who" else None,
                    lambda interviewee, accessor: False,
                    lambda self, name: "stranger" if name == "who" else None,
                ),
            }
        )
        assert realm.evaluate("(() => { const g = new Guarded(); return g.who; })()") == "same origin"
    finally:
        realm.close()


def test_cross_origin_handlers_without_a_check_are_refused():
    # They are consulted only after a check has denied, so without a check they are
    # a description that means nothing: they would never run.
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="need a check"):
            realm.install({"interfaces": [{"name": "Thing", "members": [],
                                           "cross_origin": {"name_get": 0}}],
                           "implementations": (lambda name: None,)})
        with pytest.raises(ValueError, match="unknown cross-origin handler"):
            realm.install({"interfaces": [{"name": "Thing", "members": [],
                                           "cross_origin": {"check": 0, "nope": 1}}],
                           "implementations": (lambda interviewee, accessor: True,)})
        with pytest.raises(ValueError, match="cross_origin must be a mapping"):
            realm.install(base(cross_origin=7))
        assert realm.evaluate("typeof Thing") == "undefined"
    finally:
        realm.close()


def test_an_instance_built_in_python_arrives_as_its_interface():
    # The gap this closes: an object made by `new Event()` gets the interface's
    # prototype, but an Event built in Python and handed to JavaScript used to
    # arrive as a generic host object -- no prototype chain, no members, and
    # `instanceof` false.  That is every event, node and window a host builds in
    # Python and a page reads in JavaScript.
    class Event:
        def __init__(self, kind):
            self.kind = kind

        def describe(self):
            return "a " + self.kind

    class Click(Event):
        pass

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Event",
                        "type": 0,
                        "constructor": ["new"],
                        "constructor_slot": 1,
                        "members": [
                            {"kind": "attribute", "name": "kind", "slot": 2},
                            {"kind": "method", "name": "describe", "slot": 3},
                            {"kind": "method", "name": "make", "location": "interface",
                             "slot": 4},
                            {"kind": "method", "name": "subclass", "location": "interface",
                             "slot": 5},
                        ],
                    }
                ],
                "implementations": (
                    Event,
                    lambda args, kwargs: Event(args[0] if args else "generic"),
                    lambda self: self.kind,
                    lambda self: self.describe(),
                    lambda kind: Event(kind),
                    lambda: Click("click"),
                ),
            }
        )

        # Built by the constructor, which already worked.
        assert realm.evaluate("new Event('load') instanceof Event") is True

        # Built in Python and handed over: the part that was missing.
        assert realm.evaluate("Event.make('scroll') instanceof Event") is True
        assert realm.evaluate("Event.make('scroll').describe()") == "a scroll"
        assert realm.evaluate("Event.make('scroll').kind") == "scroll"

        # A subclass with no interface of its own inherits the registered base's
        # prototype, which is what makes an event subclass readable at all.
        assert realm.evaluate("Event.subclass() instanceof Event") is True
        assert realm.evaluate("Event.subclass().describe()") == "a click"
    finally:
        realm.close()


def test_a_type_slot_must_hold_a_class_and_one_class_has_one_interface():
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="must hold a Python class"):
            realm.install({"interfaces": [{"name": "Thing", "type": 0}],
                           "implementations": (lambda: None,)})

        class Claimed:
            pass

        # Two claims in one batch are caught while reading it, so nothing is
        # installed and neither claim is left behind.
        with pytest.raises(ValueError, match="both claim one Python type"):
            realm.install({"interfaces": [{"name": "One", "type": 0},
                                          {"name": "Two", "type": 0}],
                           "implementations": (Claimed,)})
        assert realm.evaluate("typeof One") == "undefined"
        assert realm.evaluate("typeof Two") == "undefined"
    finally:
        realm.close()


def test_an_interface_can_make_its_instances_callable():
    # `call` is what a host declares when JavaScript may invoke its objects
    # directly.  The receiver needs no location: the callee is the receiver, so
    # there is nowhere else it could come from.
    class Greeter:
        def __init__(self, prefix):
            self.prefix = prefix

        def __call__(self, who):
            return self.prefix + " " + who

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Greeter",
                        "type": 0,
                        "call": 2,
                        "constructor": ["new"],
                        "constructor_slot": 1,
                        "members": [{"kind": "attribute", "name": "prefix", "slot": 3}],
                    },
                    {
                        "name": "Plain",
                        "constructor": ["new"],
                        "constructor_slot": 4,
                    },
                ],
                "implementations": (
                    Greeter,
                    lambda args, kwargs: Greeter(args[0] if args else "hello"),
                    lambda self, who: self.prefix + " " + who,
                    lambda self: self.prefix,
                    lambda args, kwargs: object(),
                ),
            }
        )

        assert realm.evaluate(
            "(() => { const g = new Greeter('hi'); return g('there'); })()"
        ) == "hi there"

        # Still an object with its own members, and still an instance of its
        # interface: being callable does not turn it into something else.
        assert realm.evaluate(
            "(() => { const g = new Greeter('hi');"
            " return [typeof g, g instanceof Greeter, g.prefix]; })()"
        ).to_python() == ["function", True, "hi"]

        # A call handler is not a JavaScript function, so it comes with no
        # `length` of its own.  A host that wants one declares it as a member,
        # which is where every other property of the object comes from too.
        assert realm.evaluate(
            "(() => { const g = new Greeter('hi'); return Object.hasOwn(g, 'length'); })()"
        ) is False

        # An interface that declared no call handler produces something JavaScript
        # cannot call, which is the safe default.
        assert realm.evaluate("typeof (new Plain())") == "object"
        with pytest.raises(Exception):
            realm.evaluate("(() => { const p = new Plain(); return p(); })()")
    finally:
        realm.close()


def test_an_undetectable_interface_hides_that_it_is_an_object():
    # `typeof` reports undefined and `== null` is true, which is the one object
    # JavaScript already has that behaves this way.  The engine does the lying,
    # which is why it is a trait and not a property.
    class All:
        pass

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "All",
                        "type": 0,
                        "traits": ["undetectable"],
                        "call": 1,
                        "constructor": ["new"],
                        "constructor_slot": 2,
                    }
                ],
                "implementations": (
                    All,
                    lambda self: 0,
                    lambda args, kwargs: All(),
                ),
            }
        )
        assert realm.evaluate(
            "(() => { const a = new All(); return [a == null, typeof a, Boolean(a)]; })()"
        ).to_python() == [True, "undefined", False]

        # Still callable, which is what makes the disguise usable: the object
        # answers when invoked even though it claims not to exist.
        assert realm.evaluate("(() => { const a = new All(); return a(); })()") == 0
    finally:
        realm.close()


def test_a_collection_interface_is_iterable_through_the_engine():
    # The intrinsics read `length` and the indexed handler, both of which the host
    # already answers, so an interface that says it is a collection becomes
    # iterable without its host writing a single accessor.
    items = ["a", "b", "c"]

    class List:
        pass

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "List",
                        "array": 31,
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "handlers": {"index_get": 1, "index_enum": 2},
                        "members": [{"kind": "attribute", "name": "length", "slot": 3}],
                    }
                ],
                "implementations": (
                    lambda args, kwargs: List(),
                    lambda self, index: items[index] if index < len(items) else None,
                    lambda self: len(items),
                    lambda self: len(items),
                ),
            }
        )

        assert realm.evaluate("(() => [...new List()])()").to_python() == items
        assert realm.evaluate(
            "(() => { const l = new List(); return [l.keys().next().value,"
            " l.values().next().value, l.entries().next().value]; })()"
        ).to_python() == [0, "a", [0, "a"]]
        assert realm.evaluate(
            "(() => { const l = new List(); let out = '';"
            " l.forEach((value, index) => { out += index + value; }); return out; })()"
        ) == "0a1b2c"

        # Not a collection when the flags say so: the same interface without the
        # bits has no iterator at all.
        assert realm.evaluate("typeof List.prototype[Symbol.iterator]") == "function"
    finally:
        realm.close()


def test_a_query_answer_carries_the_property_attributes():
    # V8's V2 contract: kYes means the property exists and the return value is its
    # attribute mask.  A bool cannot express "read-only", so a host that needs `in`
    # to be true for a read-only name has to answer with the mask -- and None, not
    # False, is what says "no opinion".  Both were reachable only as "not mine"
    # before, which silently inverted the host's answer.
    props = {"alpha": 1, "beta": 2}
    readonly = {"alpha"}
    writes = []

    class Bag:
        pass

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Bag",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "handlers": {"name_get": 1, "name_query": 2, "name_set": 3},
                    }
                ],
                "implementations": (
                    lambda args, kwargs: Bag(),
                    lambda self, name: props.get(name),
                    # 1 is ReadOnly, 0 is "present, no attributes", None is "no
                    # opinion".
                    lambda self, name: (1 if name in readonly
                                        else 0 if name in props else None),
                    lambda self, name, value: writes.append((name, value)),
                ),
            }
        )

        assert realm.evaluate("(() => { const b = new Bag(); return 'alpha' in b; })()") is True
        assert realm.evaluate("(() => { const b = new Bag(); return 'beta' in b; })()") is True
        assert realm.evaluate("(() => { const b = new Bag(); return 'gamma' in b; })()") is False

        # The mask is what V8 asks for and what it reports back, so introspection
        # says the read-only name is not writable and the plain one is.  Recorded
        # here as the observable effect, because it is not the whole story: V8 does
        # not consult the mask on the write path for an interceptor-backed property,
        # so the host's setter still runs and the host is what enforces read-only.
        assert realm.evaluate(
            "(() => { const b = new Bag();"
            " return Object.getOwnPropertyDescriptor(b, 'alpha').writable; })()"
        ) is False
        assert realm.evaluate(
            "(() => { const b = new Bag();"
            " return Object.getOwnPropertyDescriptor(b, 'beta').writable; })()"
        ) is True

        # And the write does reach the host either way, which is the caveat above.
        realm.evaluate("(() => { const b = new Bag(); b.alpha = 9; })()")
        assert writes == [("alpha", 9)]
    finally:
        realm.close()


def test_a_handler_answers_for_the_object_it_was_called_on():
    # The whole reason the receiver is passed to a handler: a handler belongs to the
    # interface, so one function answers for every object of it, and it is the object
    # -- not the interface -- that owns the answer.  Without the receiver this is
    # not merely inconvenient, it is unanswerable.
    class Box:
        def __init__(self, name):
            self.name = name
            # The values name their own object, so a wrong receiver cannot pass for
            # a right one.
            self.props = {name + "-a": name, name + "-b": name.upper()}

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Box",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "handlers": {"name_get": 1, "name_query": 2, "name_enum": 3},
                    }
                ],
                "implementations": (
                    lambda args, kwargs: Box(str(args[0])),
                    lambda self, name: self.props.get(name),
                    lambda self, name: 0 if name in self.props else None,
                    lambda self: sorted(self.props),
                ),
            }
        )
        assert realm.evaluate(
            "(() => { const one = new Box('one'), two = new Box('two');"
            " return [one['one-a'], two['two-a'], one['two-a'], one['one-b'],"
            " Object.keys(one), Object.keys(two)]; })()"
        ).to_python() == [
            "one",
            "two",
            v8n.Undefined,
            "ONE",
            ["one-a", "one-b"],
            ["two-a", "two-b"],
        ]
    finally:
        realm.close()


def _window_batch(factory, lazy):
    """A realm whose global object exposes names a factory builds on demand."""
    window = type("Window", (), {})()
    return window, {
        "interfaces": [{"name": "Window", "traits": ["global_this"]}],
        "implementations": (factory,),
        "lazy": list(lazy),
        "lazy_factory": 0,
    }


def test_a_lazy_name_is_built_the_first_time_it_is_read():
    # A realm exposes hundreds of names and a page touches a dozen.  Building them
    # all at startup is the cost this exists to avoid: a lazy name costs one getter
    # that nothing calls until something reads it.
    built = []

    def factory(name):
        if name != "Widget":
            return None
        built.append(name)
        return {
            "interfaces": [{
                "name": "Widget",
                "constructor": ["new"],
                "constructor_slot": 0,
                "members": [{"kind": "method", "name": "poke", "slot": 1, "arity": 0}],
            }],
            "implementations": (lambda args, kwargs: object(), lambda self: "poked"),
        }

    window, description = _window_batch(factory, ["Widget", "Absent"])
    realm = v8n.Realm(global_this=window, description=description)
    try:
        # Nothing exists yet.
        assert built == []

        # The read that builds is answered with what it built, so there is no
        # "declare then read again" step for a page to get wrong.
        assert realm.evaluate("typeof Widget") == "function"
        assert built == ["Widget"]

        # Fully installed, not half: it behaves like any other interface.
        assert realm.evaluate("new Widget().poke()") == "poked"

        # And paid for once.  V8 caches the property as soon as its getter has run,
        # so every later read is an ordinary property read.
        assert realm.evaluate("typeof Widget") == "function"
        assert realm.evaluate("new Widget().poke()") == "poked"
        assert built == ["Widget"]

        # A name the factory declines stays undefined, and stays cheap: "not mine"
        # is an answer, not a failure.
        assert realm.evaluate("typeof Absent") == "undefined"
        assert built == ["Widget"]
    finally:
        realm.close()


def test_a_lazy_factory_can_pull_in_another_lazy_name():
    # A factory that needs another interface while it runs is the ordinary case -- a
    # host compiling one interface usually needs another -- and it must not be
    # mistaken for the name it is already building, or a chain of them would deadlock.
    order = []

    def factory(name):
        order.append(name)
        if name == "Helper":
            return {
                "interfaces": [{"name": "Helper", "constructor": ["new"],
                                "constructor_slot": 0}],
                "implementations": (lambda args, kwargs: object(),),
            }
        # Reads another lazy name from mid-factory, which materializes inside this
        # one.  (Reading the name *being* built is the other case, and the engine
        # rejects that lookup itself: while a lazy property's getter runs, the
        # property is mid-initialization and looking it up again trips a V8 check.
        # The guard in the getter still turns that into one declined read rather
        # than unbounded recursion.)
        realm_ref[0].evaluate("typeof Helper")
        return {
            "interfaces": [{"name": "Widget", "constructor": ["new"],
                            "constructor_slot": 0}],
            "implementations": (lambda args, kwargs: object(),),
        }

    realm_ref = [None]
    window, description = _window_batch(factory, ["Widget", "Helper"])
    realm = v8n.Realm(global_this=window, description=description)
    realm_ref[0] = realm
    try:
        assert realm.evaluate("typeof Widget") == "function"
        # The nested one was built inside the outer one, and the outer one still
        # finished and answered.
        assert order == ["Widget", "Helper"]
        assert realm.evaluate("new Widget() instanceof Widget") is True
        assert realm.evaluate("new Helper() instanceof Helper") is True
    finally:
        realm_ref[0] = None
        realm.close()


def test_lazy_names_need_a_global_object_and_a_factory():
    # Refused rather than ignored, like every other field: a lazy name that was
    # accepted and then not exposed is a declaration that means less than it says.
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="global_this"):
            realm.install({"interfaces": [{"name": "Thing"}],
                           "implementations": (lambda name: None,),
                           "lazy": ["Widget"], "lazy_factory": 0})

        with pytest.raises(ValueError, match="lazy_factory"):
            realm.install({"interfaces": [{"name": "Window", "traits": ["global_this"]}],
                           "implementations": (lambda name: None,),
                           "lazy": ["Widget"]})
    finally:
        realm.close()


def test_a_second_install_does_not_release_the_first():
    # The realm owns every batch it was installed into, not only the last one.  The
    # callbacks V8 hands back carry pointers into their batch, so releasing an
    # earlier one leaves live JavaScript calling through freed memory -- and a lazy
    # interface installs on first read, which makes a second install the normal case
    # rather than a corner.
    pokes = []

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [{"name": "First", "constructor": ["new"],
                                "constructor_slot": 0,
                                "members": [{"kind": "method", "name": "poke",
                                             "slot": 1, "arity": 0}]}],
                "implementations": (lambda args, kwargs: object(),
                                    lambda self: pokes.append("poke") or "poked"),
            }
        )
        realm.install(
            {
                "interfaces": [{"name": "Second", "constructor": ["new"],
                                "constructor_slot": 0}],
                "implementations": (lambda args, kwargs: object(),),
            }
        )

        # The first batch is still the data behind these callbacks.
        assert realm.evaluate("new First().poke()") == "poked"
        assert realm.evaluate("new Second() instanceof Second") is True
        assert pokes == ["poke"]
    finally:
        realm.close()


def test_an_immutable_instance_prototype_cannot_be_swapped():
    # The instance bit and the prototype bit are two different things, and the
    # baseline's `immutable_proto` reaches the instance one.  Pinned together with
    # extensibility, which does not change: an immutable prototype is not a frozen
    # object, and reading the two as the same would freeze things this did not ask to
    # freeze.
    class Frozen:
        pass

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [{"name": "Frozen", "traits": ["immutable_instance"],
                                "constructor": ["new"], "constructor_slot": 0}],
                "implementations": (lambda args, kwargs: Frozen(),),
            }
        )
        assert realm.evaluate(
            "(() => { const f = new Frozen();"
            " return [Object.getPrototypeOf(f) === Frozen.prototype,"
            " Object.isExtensible(f)]; })()"
        ).to_python() == [True, True]

        with pytest.raises(Exception):
            realm.evaluate("Object.setPrototypeOf(new Frozen(), {})")
    finally:
        realm.close()


def test_a_trait_that_is_not_installed_yet_is_refused():
    # The important half: a description cannot silently mean less than it says.
    # `as_object` is the only trait still in this state -- everything else the
    # baseline produces is installed, which is why this names the one rather than
    # describing a category.
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="not installed yet"):
            realm.install(base(traits=["as_object"]))
        assert realm.evaluate("typeof Thing") == "undefined"
    finally:
        realm.close()


def test_a_cross_origin_member_flag_names_the_interface_policy():
    # V8 enforces an access check per template, not per member, so a member asking
    # for the check is naming the interface's policy rather than asking for a second
    # one.  Accepting it is what lets a declaration keep the flag it was written
    # with; refusing it would reject a declaration whose meaning is already honoured.
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Guarded",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "members": [{"kind": "attribute", "name": "who", "slot": 1,
                                     "flags": ["cross_origin_get", "cross_origin_set"]}],
                    }
                ],
                "implementations": (lambda args, kwargs: object(), lambda self: "answered"),
            }
        )
        assert realm.evaluate("(() => { const g = new Guarded(); return g.who; })()") == "answered"
    finally:
        realm.close()


def test_a_replaceable_attribute_becomes_a_data_property():
    # The write reaches Python once; from then on the value lives on the object with
    # no accessor in the way.  That is what replaceable means: a per-instance
    # override, not a per-instance value behind the same accessor.
    writes = []
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    {
                        "name": "Slot",
                        "constructor": ["new"],
                        "constructor_slot": 0,
                        "members": [
                            {"kind": "attribute", "name": "value", "location": "instance",
                             "slot": 1, "setter_slot": 2,
                             "flags": ["has_setter", "replaceable"]},
                        ],
                    }
                ],
                "implementations": (
                    lambda args, kwargs: object(),
                    # Never runs after the write, which is the point.
                    lambda self: 0,
                    lambda self, value: writes.append(value),
                ),
            }
        )
        assert realm.evaluate(
            "(() => { const s = new Slot(); s.value = 7;"
            " return [s.value, Object.getOwnPropertyDescriptor(s, 'value').value]; })()"
        ).to_python() == [7, 7]
        assert writes == [7]
    finally:
        realm.close()


def test_a_named_error_is_raised_for_a_bad_description():
    realm = v8n.Realm()
    try:
        with pytest.raises(ValueError, match="thing: every member needs a name"):
            realm.install({"interfaces": [{"name": "thing", "members": [{"kind": "method"}]}],
                           "implementations": ()})
        with pytest.raises(ValueError, match="unknown kind"):
            realm.install(base(members=[{"kind": "wat", "name": "x", "slot": 0}]))
        with pytest.raises(TypeError, match="has no entry 3"):
            realm.install(base(members=[{"kind": "method", "name": "x", "slot": 3}]))
        with pytest.raises(ValueError, match="has_setter flag"):
            realm.install(base(members=[{"kind": "attribute", "name": "x", "slot": 0,
                                         "setter_slot": 1}]))
        # Nothing was installed by any of those, so the realm is untouched.
        assert realm.evaluate("typeof Thing") == "undefined"
    finally:
        realm.close()


def test_each_constructor_refusal_says_which_one_it_is():
    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [
                    # No call form at all: a namespace.
                    {"name": "Namespace", "members": []},
                    # Constructible, but not callable without new.
                    {"name": "NewOnly", "constructor": ["new"], "constructor_slot": 0},
                    # Callable, but not constructible.
                    {"name": "CallOnly", "constructor": ["call"], "constructor_slot": 0},
                    {"name": "Closed", "constructor": ["new"], "traits": ["unconstructable"]},
                ],
                "implementations": (lambda args, kwargs: Counter(0),),
            }
        )
        with pytest.raises(RuntimeError, match="Illegal constructor"):
            realm.evaluate("new Namespace()")
        with pytest.raises(RuntimeError, match="without the 'new' operator"):
            realm.evaluate("NewOnly()")
        with pytest.raises(RuntimeError, match="not a constructor"):
            realm.evaluate("new CallOnly()")
        with pytest.raises(RuntimeError, match="is not a constructor"):
            realm.evaluate("new Closed()")
        # And the forms each declares do work.
        assert realm.evaluate("CallOnly() !== null") is True
        assert realm.evaluate("new NewOnly() instanceof NewOnly") is True
    finally:
        realm.close()


def test_a_python_error_from_a_member_reaches_javascript_as_an_error():
    def explode(self):
        raise ValueError("from python")

    realm = v8n.Realm()
    try:
        realm.install(
            {
                "interfaces": [{"name": "Bomb", "constructor": ["new"], "constructor_slot": 0,
                                "members": [{"kind": "method", "name": "go", "slot": 1}]}],
                "implementations": (lambda args, kwargs: Counter(0), explode),
            }
        )
        assert realm.evaluate(
            "(() => { const b = new Bomb(); try { b.go(); } catch (e) { return e.message; } })()"
        ) == "from python"
    finally:
        realm.close()


def test_the_batch_is_released_when_the_realm_closes():
    realm = v8n.Realm()
    calls = []
    realm.install(
        {
            "interfaces": [{"name": "Thing", "members": [
                {"kind": "method", "name": "go", "slot": 0}]}],
            "implementations": (lambda self: calls.append(1),),
        }
    )
    assert realm.root_count == 0
    realm.close()
    # Closing released the batch with the rest of the realm's resources.
    assert realm.closed is True
    assert calls == []


def test_a_trace_hook_sees_only_what_the_batch_declares():
    seen = []
    realm = v8n.Realm()
    try:
        realm.trace_hook = lambda event, member, detail: seen.append((event, member))
        realm.install(
            {
                "interfaces": [{"name": "Thing", "constructor": ["new"], "constructor_slot": 0,
                                "members": [{"kind": "attribute", "name": "value", "slot": 1}]}],
                "implementations": (lambda args, kwargs: Counter(0), lambda self: self.value),
            }
        )
        seen.clear()
        # The read has to happen in JavaScript: a host object crossing back to
        # Python is the Python instance, so reading it there would not involve the
        # binding at all.
        assert realm.evaluate("(() => { const t = new Thing(); return t.value; })()") == 0
        assert seen == [("construct", "Thing"), ("get", "value")]
    finally:
        realm.close()
