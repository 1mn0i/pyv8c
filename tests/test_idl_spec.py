import gc
import unittest
import weakref

import pyv8
from pyv8 import IdlSpec, AttributeSpec, MethodSpec, ConstantSpec, ConstantCallbackSpec


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
                      ConstantSpec(name="READY", value=1), ConstantCallbackSpec(name="READY", callback="ready")):
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
        class Window:
            pass
        with self.assertRaisesRegex(TypeError, "IdlSpec"):
            pyv8.Context(Window(), class_configs={Window: {}})

    def test_function_metadata_uses_the_same_spec(self):
        class Window:
            pass
        def callback(*args):
            return len(args)
        callback.__v8_idl_spec__ = IdlSpec(name="example", length=2, constructor_behavior=0)
        context = pyv8.Context(Window())
        self.addCleanup(context.close)
        context.expose(callback=callback)
        self.assertEqual(context.exec_js("[callback.name, callback.length, callback(1, 2)]"), ["example", 2, 2])
        self.assertTrue(context.exec_js("try { new callback(); false } catch (error) { error instanceof TypeError }"))

    def test_rejected_update_is_atomic_and_context_owns_registry(self):
        class Window:
            pass
        class Interface:
            pass
        class Other:
            pass
        configs = {}
        context = pyv8.Context(Window(), class_configs=configs)
        self.addCleanup(context.close)
        with self.assertRaisesRegex(TypeError, "IdlSpec"):
            context.update_configs({Interface: IdlSpec(), Window: {}})
        with self.assertRaises(TypeError):
            context.update_configs({"Interface": IdlSpec()})
        spec = IdlSpec()
        self.assertEqual(context.update_configs({Interface: spec}), 1)
        self.assertEqual(context.update_configs({Interface: spec}), 0)
        with self.assertRaisesRegex(RuntimeError, "already registered"):
            context.update_configs({Other: IdlSpec(), Interface: IdlSpec()})
        self.assertEqual(context.update_configs({Other: IdlSpec()}), 1)
        self.assertEqual(configs, {})

    def test_native_decorators_do_not_mutate_parent_spec(self):
        class Base:
            __v8_idl_spec__ = IdlSpec(length=2)
        class Child(Base):
            pass
        pyv8._pyv8.hidden(Child)
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
        self.assertEqual(context.exec_js("[target.run(), target.value]"), [4, 5])
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


def test_javascript_callback_types_and_native_boundaries():
    import pytest
    from pyv8 import AttributeSpec, MethodSpec, IdlSpec, ConstantSpec

    with pytest.raises(TypeError, match="Faker"):
        MethodSpec(name="bad", callback=lambda: None, callback_type=1)
    with pytest.raises(TypeError, match="Faker"):
        AttributeSpec(name="bad", getter=lambda: None, callback_type=1)
    with pytest.raises(TypeError, match="requires a call"):
        IdlSpec(undetectable=True)
    for value in (-2147483648, 4294967295):
        assert ConstantSpec(name="LIMIT", value=value).value == value
    for value in (-2147483649, 4294967296):
        with pytest.raises(OverflowError):
            ConstantSpec(name="LIMIT", value=value)


def test_registration_after_template_installation_is_rejected():
    import pytest
    import pyv8

    class Interface:
        pass

    class Window:
        pass

    context = pyv8.Context(Window())
    try:
        context.expose(Interface)
        with pytest.raises(RuntimeError, match="before template installation"):
            context.update_configs({Interface: pyv8.IdlSpec()})
        assert context.exec_js("typeof Interface") == "function"
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

    isolates = [pyv8.Isolate(), pyv8.Isolate()]
    contexts = []
    references = []
    try:
        for index, isolate in enumerate(isolates):
            state = State(index)
            references.append(weakref.ref(state))
            spec = pyv8.IdlSpec(methods=(pyv8.MethodSpec(
                name="read", callback=state.read, location=pyv8.FlagLocation.kInterface,
            ),))
            context = pyv8.Context(Window(), isolate=isolate, class_configs={Interface: spec})
            context.expose(Interface)
            contexts.append(context)
            del state, spec
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

    context = pyv8.Context(Window(), class_configs={Middle: Middle.__v8_idl_spec__.replace(skip_inherit=True)})
    try:
        context.expose(Leaf)
        assert context.exec_js("Object.getPrototypeOf(Leaf).name") == "Base"
        Middle.__v8_idl_spec__ = pyv8.IdlSpec(skip_inherit=False)
        context.expose(Leaf)
        assert context.exec_js("Object.getPrototypeOf(Leaf).name") == "Base"
    finally:
        context.close()
