import unittest

import pyv8


class TestPyClassMaintenance(unittest.TestCase):
    """测试 pyclass 元数据校验和 handler 边界路径。"""

    def test_invalid_class_length_is_reported(self):
        """错误的模板长度应转换为 Python 异常，而不是进入 V8。"""

        class InvalidLength:
            __v8_length__ = "invalid"

        with self.assertRaises(TypeError):
            pyv8.Context(InvalidLength())

    def test_hook_handler_without_python_method_is_safe(self):
        """hook 模式下缺少 Python handler 时应安全回退到普通 JS 行为。"""

        class Window:
            pass

        context = pyv8.Context(Window(), hook=True)

        # named handler 已安装，但 Window 没有对应 Python 方法。
        self.assertTrue(context.exec_js("globalThis.missing === undefined"))
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_invalid_class_template_during_expose_is_reported(self):
        """Python 类型模板创建失败时不得继续解引用空的 py_class 指针。"""

        class Window:
            pass

        class InvalidLength:
            __v8_length__ = "invalid"

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "__v8_length__"):
            context.expose(InvalidLength)
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_short_attribute_config_is_rejected(self):
        """Web IDL attribute 配置长度不足时应抛出明确异常。"""

        class Window:
            pass

        class InvalidAttribute:
            __v8_attribute__ = (("value", "get_value"),)

            def get_value(self):
                return 42

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "10-item tuple"):
            context.expose(target=InvalidAttribute())
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_short_operation_config_is_rejected(self):
        """Web IDL operation 配置长度不足时应抛出明确异常。"""

        class Window:
            pass

        class InvalidOperation:
            __v8_method__ = (("run", "fn_run"),)

            def fn_run(self):
                return 42

        context = pyv8.Context(Window())
        with self.assertRaisesRegex(TypeError, "9-item tuple"):
            context.expose(target=InvalidOperation())
        self.assertEqual(context.exec_js("40 + 2"), 42)

    def test_context_class_config_overlays_are_isolated(self):
        class Window:
            pass

        class Interface:
            __v8_constructor__ = 1
            __v8_has_constructor__ = 1

            def __init__(self, win=None):
                pass

        def first(self):
            return "first"

        def second(self):
            return "second"

        first.__v8_direct_callback__ = True
        second.__v8_direct_callback__ = True
        first.__name__ = "first_callback"
        second.__name__ = "second_callback"
        first_owner = type("FirstCallbacks", (), {"first_callback": first})
        second_owner = type("SecondCallbacks", (), {"second_callback": second})
        operation = lambda name, callback: (
            name, callback, 0, 0, 1, 0, 0, 0, 0,
        )

        first_context = pyv8.Context(Window(), class_configs={Interface: {
            "__v8_method__": (operation("marker", "first_callback"),),
            "__v8_callback_owner__": first_owner,
        }})
        second_context = pyv8.Context(Window(), class_configs={Interface: {
            "__v8_method__": (operation("otherMarker", "second_callback"),),
            "__v8_callback_owner__": second_owner,
        }})
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
            __v8_constructor__ = 1
            __v8_has_constructor__ = 1
            __v8_method__ = (("marker", "get_marker", 0, 0, 1, 0, 0, 0, 0),)

            def __init__(self, value="default", win=None):
                self.value = value

            def get_marker(self):
                return self.value

        def adapter(prefix):
            def construct(cls, args, kwargs):
                return cls(prefix + str(args[0]), **kwargs)
            return construct

        first_context = pyv8.Context(Window(), class_configs={Interface: {
            "__v8_constructor_adapter__": adapter("first:"),
        }})
        second_context = pyv8.Context(Window(), class_configs={Interface: {
            "__v8_constructor_adapter__": adapter("second:"),
        }})
        first_context.expose(Interface)
        second_context.expose(Interface)

        self.assertEqual(first_context.exec_js("new Interface('x').marker()"), "first:x")
        self.assertEqual(second_context.exec_js("new Interface('x').marker()"), "second:x")

    def test_invalid_context_constructor_adapter_is_rejected(self):
        class Window:
            pass

        class Interface:
            __v8_constructor__ = 1
            __v8_has_constructor__ = 1

        context = pyv8.Context(Window(), class_configs={Interface: {
            "__v8_constructor_adapter__": "not-callable",
        }})
        with self.assertRaisesRegex(TypeError, "__v8_constructor_adapter__ must be callable"):
            context.expose(Interface)


if __name__ == "__main__":
    unittest.main()
