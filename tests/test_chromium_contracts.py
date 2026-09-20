from tests.values import snapshot

from pyv8 import IdlSpec, AttributeSpec, MethodSpec, ConstantSpec
import unittest

import pyv8


class TestChromiumContracts(unittest.TestCase):
    """测试 Chromium-like 构造器、原型 descriptor 和 interceptor 契约。"""

    def setUp(self):
        class Window:
            pass

        self.context = pyv8.Context(Window())

    def tearDown(self):
        del self.context

    def test_constructor_metadata_and_prototype_constructor(self):
        """构造器元数据和 prototype.constructor 应接近原生接口对象。"""

        class ChromiumConstructor:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kAll,
                has_constructor=1,
                length=2,
            )

            def __init__(self, first=None, second=None, win=None):
                self.first = first
                self.second = second

        self.context.expose(ChromiumConstructor)
        result = snapshot(self.context.exec_js(
            """
            (() => {
                const descriptor = Object.getOwnPropertyDescriptor(
                    ChromiumConstructor.prototype,
                    "constructor"
                );
                return [
                    ChromiumConstructor.name,
                    ChromiumConstructor.length,
                    ChromiumConstructor.toString().includes("[native code]"),
                    descriptor.value === ChromiumConstructor,
                    descriptor.enumerable,
                    new ChromiumConstructor(1, 2) instanceof ChromiumConstructor,
                    ChromiumConstructor(1, 2) === undefined
                ];
            })()
            """
        ))
        self.assertEqual(
            result,
            ["ChromiumConstructor", 2, True, True, False, True, True],
        )

    def test_prototype_accessor_descriptor_and_receiver_check(self):
        """原型 getter 应保留 descriptor flag 并拒绝非法 receiver。"""

        class NavigatorLike:
            __v8_idl_spec__ = IdlSpec(
                attributes=(
                    AttributeSpec(
                        name="userAgent",
                        getter="get_user_agent",
                        setter=None,
                        location=pyv8.FlagLocation.kPrototype,
                        property_flags=pyv8.V8Attribute.DontEnum
                        | pyv8.V8Attribute.DontDelete,
                        receiver_check=pyv8.FlagReceiverCheck.kCheck,
                        cross_origin_get_check=pyv8.FlagCrossOriginCheck.kCheck,
                        cross_origin_set_check=pyv8.FlagCrossOriginCheck.kCheck,
                        side_effect=pyv8.SideEffectType.kHasNoSideEffect,
                    ),
                ),
            )

            def get_user_agent(self):
                return "pyv8-browser/1.0"

        self.context.expose(navigator=NavigatorLike())
        result = snapshot(self.context.exec_js(
            """
            (() => {
                const prototype = Object.getPrototypeOf(navigator);
                const descriptor = Object.getOwnPropertyDescriptor(
                    prototype,
                    "userAgent"
                );
                let illegalReceiverThrows = false;
                try {
                    descriptor.get.call({});
                } catch (error) {
                    illegalReceiverThrows = error instanceof TypeError;
                }
                return [
                    navigator.userAgent,
                    descriptor.enumerable,
                    descriptor.configurable,
                    descriptor.set === undefined,
                    illegalReceiverThrows
                ];
            })()
            """
        ))
        self.assertEqual(result, ["pyv8-browser/1.0", False, False, True, True])

    def test_interface_operation_and_constant_descriptors(self):
        """interface operation 与 Web IDL 常量应安装在正确位置并保留标志。"""

        class InterfaceContract:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kNew,
                constants_value=(ConstantSpec(name="READY", value=4),),
                methods=(
                    MethodSpec(
                        name="parse",
                        callback="fn_parse",
                        length=1,
                        location=pyv8.FlagLocation.kInterface,
                        property_flags=pyv8.V8Attribute.ReadOnly
                        | pyv8.V8Attribute.DontEnum
                        | pyv8.V8Attribute.DontDelete,
                        receiver_check=pyv8.FlagReceiverCheck.kDoNotCheck,
                        cross_origin_check=pyv8.FlagCrossOriginCheck.kCheck,
                        side_effect=pyv8.SideEffectType.kHasNoSideEffect,
                    ),
                ),
            )

            @staticmethod
            def fn_parse(value):
                return value * 2

            def __init__(self, win=None):
                pass

        self.context.expose(InterfaceContract)
        result = snapshot(self.context.exec_js(
            """
            (() => {
                const operation = Object.getOwnPropertyDescriptor(
                    InterfaceContract,
                    "parse"
                );
                const constant = Object.getOwnPropertyDescriptor(
                    InterfaceContract,
                    "READY"
                );
                const prototypeConstant = Object.getOwnPropertyDescriptor(
                    InterfaceContract.prototype,
                    "READY"
                );
                return [
                    InterfaceContract.parse(21),
                    new InterfaceContract().parse === undefined,
                    operation.writable,
                    operation.enumerable,
                    operation.configurable,
                    constant.value,
                    constant.writable,
                    constant.configurable,
                    prototypeConstant.value
                ];
            })()
            """
        ))
        self.assertEqual(
            result,
            [42, True, False, False, False, 4, False, False, 4],
        )

    def test_invalid_constant_metadata_is_rejected(self):
        """验证常量配置 tuple 长度错误时不会静默读取越界元素。"""

        with self.assertRaisesRegex(TypeError, "IdlSpec.constants_value"):
            IdlSpec(constants_value=(("BROKEN",),))
        self.assertEqual(self.context.exec_js("40 + 2"), 42)

    def test_same_named_classes_are_rejected_in_one_context(self):
        """验证同一 Context 不会让同名 Python 类互相覆盖模板。"""
        first = type("DuplicateInterface", (), {})
        second = type("DuplicateInterface", (), {})
        self.context.expose(first)
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.context.expose(second)

    def test_named_and_indexed_interceptor_contract(self):
        """named/indexed interceptor 的五种 handler 应能组合工作。"""

        class BrowserCollection:
            def __init__(self):
                self.named = {"visible": 1, "hidden": 2}
                self.indexed = [10, 20]

            __v8_idl_spec__ = IdlSpec(
                name_get="__v8_name_get__",
                name_set="__v8_name_set__",
                name_query="__v8_name_query__",
                name_del="__v8_name_del__",
                name_enum="__v8_name_enum__",
                index_get="__v8_index_get__",
                index_set="__v8_index_set__",
                index_query="__v8_index_query__",
                index_del="__v8_index_del__",
                index_enum="__v8_index_enum__",
            )
            def __v8_name_get__(self, name):
                return self.named.get(name)

            def __v8_name_set__(self, name, value):
                self.named[name] = value
                return True

            def __v8_name_query__(self, name):
                if name not in self.named:
                    return None
                if name == "hidden":
                    return pyv8.V8Attribute.DontEnum
                return pyv8.V8Attribute.kNone

            def __v8_name_del__(self, name):
                return self.named.pop(name, None) is not None

            def __v8_name_enum__(self):
                return list(self.named)

            def __v8_index_get__(self, index):
                if 0 <= index < len(self.indexed):
                    return self.indexed[index]
                return None

            def __v8_index_set__(self, index, value):
                while len(self.indexed) <= index:
                    self.indexed.append(None)
                self.indexed[index] = value
                return True

            def __v8_index_query__(self, index):
                if 0 <= index < len(self.indexed):
                    return pyv8.V8Attribute.kNone
                return None

            def __v8_index_del__(self, index):
                if 0 <= index < len(self.indexed):
                    self.indexed[index] = None
                    return True
                return False

            def __v8_index_enum__(self):
                return len(self.indexed)

        collection = BrowserCollection()
        self.context.expose(collection=collection)

        initial = snapshot(self.context.exec_js(
            "[collection.visible, collection[0], 'visible' in collection, 0 in collection]"
        ))
        self.assertEqual(initial, [1, 10, True, True])

        self.context.exec_js("collection.added = 3; collection[2] = 30")
        self.assertEqual(collection.named["added"], 3)
        self.assertEqual(collection.indexed[2], 30)

        keys = snapshot(self.context.exec_js("Object.keys(collection)"))
        self.assertIn("visible", keys)
        self.assertIn("added", keys)
        self.assertNotIn("hidden", keys)
        self.assertIn("0", keys)
        self.assertIn("2", keys)

        deleted = snapshot(self.context.exec_js(
            "[delete collection.visible, delete collection[0]]"
        ))
        self.assertEqual(deleted, [True, True])
        self.assertIsNone(collection.indexed[0])
        self.assertNotIn("visible", collection.named)
        self.assertTrue(
            snapshot(self.context.exec_js(
                "collection.visible === undefined && collection[0] === undefined"
            ))
        )

    def test_array_prototype_intrinsics_keep_their_native_semantics(self):
        class IterableCollection:
            __v8_idl_spec__ = IdlSpec(
                name_get="__v8_name_get__",
                index_get="__v8_index_get__",
                array=pyv8.FlagArrayProto.iterator
                | pyv8.FlagArrayProto.entries
                | pyv8.FlagArrayProto.keys
                | pyv8.FlagArrayProto.values
                | pyv8.FlagArrayProto.forEach,
            )

            def __init__(self):
                self.values = ["a", "b"]

            def __v8_name_get__(self, name):
                return len(self.values) if name == "length" else None

            def __v8_index_get__(self, index):
                return self.values[index] if index < len(self.values) else None

        self.context.expose(collection=IterableCollection())
        result = snapshot(self.context.exec_js(
            """
            (() => {
                const visited = [];
                collection.forEach((value, index, owner) => {
                    visited.push([value, index, owner === collection]);
                });
                return [
                    Array.from(collection.keys()),
                    Array.from(collection.values()),
                    Array.from(collection.entries()),
                    visited
                ];
            })()
            """
        ))
        self.assertEqual(
            result,
            [
                [0, 1], ["a", "b"], [[0, "a"], [1, "b"]],
                [["a", 0, True], ["b", 1, True]],
            ],
        )

    def test_generated_browser_api_graph(self):
        """大量带 accessor、operation 和 interceptor 的模板应可组合安装。"""

        attribute_config = (AttributeSpec(name="kind", getter="get_kind", property_flags=pyv8.V8Attribute.DontEnum, side_effect=1),)
        method_config = (MethodSpec(name="matches", callback="fn_matches", length=1, side_effect=1),)

        def get_kind(self):
            return self._kind

        def fn_matches(self, value):
            return self._kind == value

        def name_get(self, name):
            if name == "dynamicName":
                return f"dynamic-{self._kind}"
            return None

        api_classes = []
        api_objects = {}
        for index in range(64):
            api_class = type(
                f"BrowserApi{index}",
                (),
                {
                    "__v8_idl_spec__": IdlSpec(
                        attributes=attribute_config, methods=method_config, name_get="__v8_name_get__",
                    ),
                    "get_kind": get_kind,
                    "fn_matches": fn_matches,
                    "__v8_name_get__": name_get,
                },
            )
            api_classes.append(api_class)
            instance = api_class()
            instance._kind = index
            api_objects[f"api{index}"] = instance

        self.context.expose(*api_classes, **api_objects)
        result = snapshot(self.context.exec_js(
            """
            [
                api0.kind,
                api63.kind,
                api0.matches(0),
                api63.matches(63),
                api0.dynamicName,
                api63.dynamicName,
                Object.getPrototypeOf(api0) !== Object.getPrototypeOf(api63)
            ]
            """
        ))
        self.assertEqual(
            result,
            [0, 63, True, True, "dynamic-0", "dynamic-63", True],
        )


if __name__ == "__main__":
    unittest.main()
