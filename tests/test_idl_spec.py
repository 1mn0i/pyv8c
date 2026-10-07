from tests.values import snapshot

import gc
import unittest
import weakref

import pyv8
from pyv8 import IdlSpec, AttributeSpec, MethodSpec, ConstantSpec


class TestIdlSpec(unittest.TestCase):
    def test_native_fields_and_immutable_replace(self):
        member = MethodSpec(name="run", callback=lambda receiver: 1)
        spec = IdlSpec(length=2, methods=(member,), global_this=False)
        self.assertEqual(spec.length, 2)
        self.assertFalse(spec.global_this)
        other = spec.replace(length=3)
        self.assertEqual(spec.length, 2)
        self.assertEqual(other.length, 3)
        self.assertIs(other.methods, spec.methods)
        for value in (spec, member, AttributeSpec(name="x", getter="get_x"),
                      ConstantSpec(name="READY", value=1)):
            with self.subTest(value=type(value)):
                with self.assertRaises((AttributeError, TypeError)):
                    value.length = 4
                with self.assertRaises(TypeError):
                    value["length"] = 4
        with self.assertRaises(TypeError):
            dict(spec)

    def test_unknown_fields_and_wrong_types_are_rejected(self):
        for fields in (
            {"unknown": 1}, {"length\0ignored": 1}, {"__v8_length__": 1}, {"length": "2"},
            {"length": -1}, {"attributes": []}, {"methods": (("run", "run"),)},
            {"callback_owner": object()}, {"parent_class": object()},
            {"constructor_adapter": 42}, {"name_get": True}, {"to_string_tag": 42},
        ):
            with self.subTest(fields=fields), self.assertRaises(TypeError):
                IdlSpec(**fields)
        with self.assertRaises((TypeError, OverflowError)):
            IdlSpec(length=2 ** 64)
        with self.assertRaises(TypeError):
            MethodSpec(name="run", callback="run", length=256)
        with self.assertRaises(TypeError):
            AttributeSpec(name="x", getter="x", property_flags=8)
        with self.assertRaises(TypeError):
            AttributeSpec(name="x")
        with self.assertRaises(TypeError):
            ConstantSpec(name="x\0hidden", value=1)

    def test_array_takes_the_bits_the_shapes_name(self):
        """``array`` is what an interface borrows from ``Array.prototype``, as installed bits.

        ``pyv8.ArrayProto.iterator`` is the iterator alone (``HTMLCollection``) and ``.iterable``
        is that iterator with ``entries``/``keys``/``values``/``forEach`` (``NodeList``, a
        Maplike); both shapes were measured against Chrome.  The intrinsics behind the bits belong
        to the binding, and ``0`` -- what an unset field holds -- borrows nothing.
        """
        self.assertEqual(IdlSpec(array=pyv8.ArrayProto.iterator).array, 1)
        self.assertEqual(IdlSpec(array=pyv8.ArrayProto.iterable).array, 31)
        self.assertEqual(IdlSpec().array, 0)
        self.assertEqual(IdlSpec(array=pyv8.ArrayProto.iterable).replace(length=3).array, 31)

    def test_an_array_value_outside_the_two_shapes_is_told_the_range(self):
        """One vocabulary, so one answer: the two bits a shape names, and nothing else."""
        for value in ("iterable", "", -1, 32, b"iterable", 1.5, 2 ** 32):
            with self.subTest(value=value):
                with self.assertRaises(TypeError) as caught:
                    IdlSpec(array=value)
                self.assertIn("0..31", str(caught.exception))

    def test_definition_kind_takes_the_number_the_kind_names(self):
        """One field, three kinds: which interface object a definition builds.

        Blink's ``WrapperTypeInfo`` decides between a function with a prototype, a namespace
        object and a function without one, and the three are exclusive -- a namespace has no
        prototype either, which is why one number answers both and two flags could not.
        """
        self.assertEqual(IdlSpec().definition_kind, pyv8.DefinitionKind.kInterface)
        self.assertEqual(IdlSpec(definition_kind=0).definition_kind, 0)
        self.assertEqual(
            IdlSpec(definition_kind=pyv8.DefinitionKind.kIdlNamespace).definition_kind, 1)
        self.assertEqual(
            IdlSpec(definition_kind=pyv8.DefinitionKind.kWrapperTypeNoPrototype).definition_kind, 2)
        self.assertEqual(IdlSpec(definition_kind=1).replace(length=3).definition_kind, 1)

    def test_a_definition_kind_outside_the_three_is_told_the_range(self):
        """A kind is one of the three numbers, so a name or an out-of-range int is refused."""
        for value in ("namespace", "", -1, 3, None, b"namespace", 1.5):
            with self.subTest(value=value):
                with self.assertRaises(TypeError) as caught:
                    IdlSpec(definition_kind=value)
                self.assertIn("0..2", str(caught.exception))

    def test_disabled_handler_is_not_called(self):
        class Window:
            __v8_idl_spec__ = IdlSpec(name_get=None)

            def __v8_name_get__(self, name):
                raise AssertionError("disabled interceptor was called")

        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        self.assertTrue(context.exec_js("globalThis.missing === undefined"))

    def test_callback_cycles_are_collected(self):
        def owner_ref():
            class Owner:
                pass
            Owner.__v8_idl_spec__ = IdlSpec(parent_class=Owner)
            return weakref.ref(Owner)

        reference = owner_ref()
        gc.collect()
        self.assertIsNone(reference())

    def test_class_and_context_reject_untyped_specs(self):
        class Invalid:
            __v8_idl_spec__ = {"length": 2}

        with self.assertRaisesRegex(TypeError, "must be an IdlSpec"):
            pyv8.Context(Invalid())

        class Widget:
            pass

        class Window:
            pass

        # The specs an answer hands over are the realm's own; an untyped one is
        # refused before it can build anything.
        def factory(name):
            return (Widget, {"Widget": {}}) if name == "Widget" else None

        context = pyv8.Context(Window(), lazy_factory=factory)
        self.addCleanup(context.close)
        context.install(["Widget"])
        with self.assertRaisesRegex(TypeError, "IdlSpec"):
            context.exec_js("typeof Widget")

    def test_function_metadata_uses_the_same_spec(self):
        class Window:
            pass
        def callback(*args):
            return len(args)
        callback.__v8_idl_spec__ = IdlSpec(name="example", length=2, constructor_behavior=0)
        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        context.expose(callback=callback)
        self.assertEqual(snapshot(context.exec_js("[callback.name, callback.length, callback(1, 2)]")), ["example", 2, 2])
        self.assertTrue(snapshot(context.exec_js("try { new callback(); false } catch (error) { error instanceof TypeError }")))

    def test_native_decorators_do_not_mutate_parent_spec(self):
        class Base:
            __v8_idl_spec__ = IdlSpec(length=2)
        class Child(Base):
            pass
        pyv8.hidden(Child)
        self.assertFalse(Base.__v8_idl_spec__.hidden)
        self.assertTrue(Child.__v8_idl_spec__.hidden)

    def test_named_callbacks_are_bound_once_without_mutating_functions(self):
        class Interface:
            __v8_idl_spec__ = IdlSpec(methods=(MethodSpec(name="run", callback="run"),),
                attributes=(AttributeSpec(name="value", getter="get_value"),))
            def run(self):
                return 4
            def get_value(self):
                return 5
        class Window:
            pass
        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        context.expose(target=Interface())
        Interface.run = lambda self: 99
        self.assertEqual(snapshot(context.exec_js("[target.run(), target.value]")), [4, 5])
        self.assertNotIn("__v8_direct_callback__", Interface.get_value.__dict__)
        self.assertNotIn("__v8_raw_name__", Interface.get_value.__dict__)

    def test_explicit_spec_does_not_expose_undeclared_members(self):
        class Explicit:
            __v8_idl_spec__ = IdlSpec()
            accidental = 42
        class Generic:
            implicit = 7
        class Window:
            pass
        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        context.expose(explicit=Explicit(), generic=Generic())
        self.assertTrue(context.exec_js("explicit.accidental === undefined"))
        self.assertEqual(context.exec_js("generic.implicit"), 7)


def test_undetectable_requires_a_call_and_constant_ranges():
    import pytest
    from pyv8 import IdlSpec, ConstantSpec

    with pytest.raises(TypeError, match="requires a call"):
        IdlSpec(undetectable=True)
    for value in (-2147483648, 4294967295):
        assert ConstantSpec(name="LIMIT", value=value).value == value
    for value in (-2147483649, 4294967296):
        with pytest.raises(OverflowError):
            ConstantSpec(name="LIMIT", value=value)


def test_a_name_the_realm_only_hands_over_is_answered_by_the_factory():
    """A name that is never a global still gets its overlay before its template."""
    import pyv8

    class Extension:
        __v8_idl_spec__ = pyv8.IdlSpec(
            constants_value=(pyv8.ConstantSpec(name="VENDOR", value=37445),),
            to_string_tag="Extension",
        )

    class Window:
        pass

    asked = []

    def factory(name):
        asked.append(name)
        if name != "Extension":
            return None
        return Extension, {"Extension": Extension.__v8_idl_spec__}

    context = pyv8.Context(Window(), lazy_factory=factory)
    try:
        assert asked == ["Window"]
        # Handed over, never exposed: the name stays off the global object.
        context.expose(handed_over=Extension())
        assert "Extension" in asked
        assert context.exec_js("'Extension' in globalThis") is False
        assert context.exec_js("handed_over.VENDOR") == 37445
    finally:
        context.close()


def test_a_name_the_realm_cannot_describe_is_asked_about_once():
    """A refusal is remembered, so an unknown name costs one ask per realm."""
    import pyv8

    class Other:
        pass

    class Window:
        pass

    asked = []

    def factory(name):
        asked.append(name)
        return None

    context = pyv8.Context(Window(), lazy_factory=factory)
    try:
        context.expose(other=Other())
        assert context.exec_js("typeof other") == "object"
        assert asked == ["Window", "Other"]

        # The template is built by then, so a second object under the same name
        # answers from the registry.
        context.expose(second=Other())
        assert context.exec_js("typeof second") == "object"
        assert asked == ["Window", "Other"]
    finally:
        context.close()


def test_generic_object_template_member_fails_safely_without_a_context():
    import pytest
    import pyv8

    class Window:
        pass

    class Payload:
        pass

    class Interface:
        payload = Payload()

    context = pyv8.Context(Window())
    try:
        with pytest.raises(RuntimeError, match="require a V8 context"):
            context.expose(Interface)
        assert context.exec_js("1 + 1") == 2
        context.expose(payload=Payload())
        assert context.exec_js("typeof payload") == "object"
    finally:
        context.close()


def test_generic_callable_survives_class_mutation_during_reflection():
    import gc
    import pyv8

    class Window:
        pass

    class Mutation:
        def __getattribute__(self, name):
            if name == "__v8_idl_spec__":
                if "__call__" in Interface.__dict__:
                    del Interface.__call__
                    gc.collect()
                return pyv8.IdlSpec(hidden=True)
            return object.__getattribute__(self, name)

    class Interface:
        def __call__(self):
            return 42

        mutation = Mutation()

    context = pyv8.Context(Window())
    try:
        context.expose(instance=Interface())
        assert "__call__" not in Interface.__dict__
        assert context.exec_js("instance()") == 42
    finally:
        context.close()


def test_explicit_callbacks_are_isolate_local_and_released_on_close():
    import gc
    import weakref
    import pyv8

    class Window:
        pass

    class Interface:
        pass

    class State:
        def __init__(self, value):
            self.value = value

        def read(self):
            return self.value

    def realm_factory(state):
        """A realm's answer, built the way a binding session builds one."""
        def factory(name):
            if name != "Interface":
                return None
            spec = pyv8.IdlSpec(methods=(pyv8.MethodSpec(
                name="read", callback=state.read, location=pyv8.FlagLocation.kInterface,
            ),))
            return Interface, {"Interface": spec}

        return factory

    isolates = [pyv8.Isolate(), pyv8.Isolate()]
    contexts = []
    references = []
    try:
        for index, isolate in enumerate(isolates):
            state = State(index)
            references.append(weakref.ref(state))
            context = pyv8.Context(Window(), isolate=isolate, lazy_factory=realm_factory(state))
            context.expose(Interface)
            contexts.append(context)
            del state
        assert contexts[0].exec_js("Interface.read()") == 0
        assert contexts[1].exec_js("Interface.read()") == 1
        assert all(reference() is not None for reference in references)
        for context in contexts:
            context.close()
        gc.collect()
        assert all(reference() is None for reference in references)
    finally:
        for context in contexts:
            context.close()
        for isolate in isolates:
            isolate.close()


def test_registered_inheritance_flags_override_class_declarations():
    import pyv8

    class Window:
        pass

    class Base:
        pass

    class Middle(Base):
        __v8_idl_spec__ = pyv8.IdlSpec(skip_inherit=False)

    class Leaf(Middle):
        pass

    def factory(name):
        if name != "Leaf":
            return None
        return Leaf, {
            "Leaf": pyv8.IdlSpec(),
            "Middle": Middle.__v8_idl_spec__.replace(skip_inherit=True),
        }

    context = pyv8.Context(Window(), lazy_factory=factory)
    try:
        context.expose(Leaf)
        assert context.exec_js("Object.getPrototypeOf(Leaf).name") == "Base"
        # The class declaration changed, but the realm answered for Middle
        # already, and its answer is what the built template carries.
        Middle.__v8_idl_spec__ = pyv8.IdlSpec(skip_inherit=False)
        context.expose(Leaf)
        assert context.exec_js("Object.getPrototypeOf(Leaf).name") == "Base"
    finally:
        context.close()
