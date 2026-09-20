from pyv8 import IdlSpec, MethodSpec
import unittest

import pyv8


class TestPyClassMaintenance(unittest.TestCase):
    """测试 pyclass 元数据校验和 handler 边界路径。"""

    def test_invalid_class_length_is_reported(self):
        """错误的模板长度应转换为 Python 异常，而不是进入 V8。"""

        with self.assertRaisesRegex(TypeError, "IdlSpec.length"):
            IdlSpec(length="invalid")

    def test_trace_handler_without_python_method_is_safe(self):
        """trace 模式下缺少 Python handler 时应安全回退到普通 JS 行为。"""

        class Window:
            pass

        context = pyv8.Context(Window(), trace=True)

        # named handler 已安装，但 Window 没有对应 Python 方法。
        self.assertTrue(context.exec_js("globalThis.missing === undefined"))
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_invalid_class_template_during_expose_is_reported(self):
        """Python 类型模板创建失败时不得继续解引用空的 py_class 指针。"""

        class Window:
            pass

        class InvalidLength:
            __v8_idl_spec__ = {"length": 2}

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "__v8_idl_spec__ must be an IdlSpec"):
            context.expose(InvalidLength)
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_short_attribute_config_is_rejected(self):
        """Web IDL attribute 配置长度不足时应抛出明确异常。"""

        class Window:
            pass

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "IdlSpec.attributes"):
            IdlSpec(attributes=(("value", "get_value"),))
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_short_operation_config_is_rejected(self):
        """Web IDL operation 配置长度不足时应抛出明确异常。"""

        class Window:
            pass

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "IdlSpec.methods"):
            IdlSpec(methods=(("value", "fn_run"),))
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_context_class_config_overlays_are_isolated(self):
        class Window:
            pass

        class Interface:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=1,
                has_constructor=1,
            )

            def __init__(self, win=None):
                pass

        def first(self):
            return "first"

        def second(self):
            return "second"

        first.__name__ = "first_callback"
        second.__name__ = "second_callback"
        operation = lambda name, callback: MethodSpec(name=name, callback=callback)

        first_context = pyv8.Context(Window(), class_configs={Interface: Interface.__v8_idl_spec__.replace(
            methods=(operation("marker", first),),
        )})
        second_context = pyv8.Context(Window(), class_configs={Interface: Interface.__v8_idl_spec__.replace(
            methods=(operation("otherMarker", second),),
        )})
        first_context.expose(Interface)
        second_context.expose(Interface)

        self.assertEqual(first_context.exec_js("new Interface().marker()"), "first")
        self.assertTrue(first_context.exec_js("typeof Interface.prototype.otherMarker === 'undefined'"))
        self.assertEqual(second_context.exec_js("new Interface().otherMarker()"), "second")
        self.assertTrue(second_context.exec_js("typeof Interface.prototype.marker === 'undefined'"))

    def test_context_constructor_adapters_are_isolated(self):
        class Window:
            pass

        class Interface:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=1,
                has_constructor=1,
                methods=(
                    MethodSpec(
                        name="marker",
                        callback="get_marker",
                        length=0,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )

            def __init__(self, value="default", win=None):
                self.value = value

            def get_marker(self):
                return self.value

        def adapter(prefix):
            def construct(cls, args, kwargs):
                return cls(prefix + str(args[0]), **kwargs)
            return construct

        first_context = pyv8.Context(Window(), class_configs={Interface: Interface.__v8_idl_spec__.replace(
            constructor_adapter=adapter("first:"),
        )})
        second_context = pyv8.Context(Window(), class_configs={Interface: Interface.__v8_idl_spec__.replace(
            constructor_adapter=adapter("second:"),
        )})
        first_context.expose(Interface)
        second_context.expose(Interface)

        self.assertEqual(first_context.exec_js("new Interface('x').marker()"), "first:x")
        self.assertEqual(second_context.exec_js("new Interface('x').marker()"), "second:x")

    def test_invalid_context_constructor_adapter_is_rejected(self):
        class Window:
            pass

        class Interface:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=1,
                has_constructor=1,
            )

        with self.assertRaisesRegex(TypeError, "IdlSpec.constructor_adapter"):
            IdlSpec(constructor_adapter=42)


if __name__ == "__main__":
    unittest.main()
