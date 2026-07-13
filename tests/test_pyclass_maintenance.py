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


if __name__ == "__main__":
    unittest.main()
