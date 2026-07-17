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


if __name__ == "__main__":
    unittest.main()
