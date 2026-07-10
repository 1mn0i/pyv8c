import unittest

import pyv8


class TestExceptionSafety(unittest.TestCase):
    """测试 V8 异常传播和超时清理的安全性。"""

    def setUp(self):
        """每个测试开始前创建独立的 JavaScript 上下文。"""

        class Window:
            pass

        self.context = pyv8.Context(Window())

    def tearDown(self):
        """每个测试结束后释放 JavaScript 上下文。"""

        del self.context

    def test_getter_exception_surfaces_as_js_exception(self):
        """JavaScript getter 抛出的异常应转换为 JSException。"""

        js_obj = self.context.exec_js(
            """
            new class ThrowingGetter {
                get boom() {
                    throw new Error("getter boom");
                }
            }
            """
        )

        with self.assertRaises(pyv8.JSException):
            _ = js_obj.boom

        # getter 异常不能破坏后续 JavaScript 执行。
        self.assertEqual(self.context.exec_js("1 + 1"), 2)

    def test_dir_exception_does_not_crash(self):
        """Proxy 的 ownKeys 异常应传播到 Python，而不是导致进程崩溃。"""

        js_obj = self.context.exec_js(
            """
            new Proxy({}, {
                ownKeys() {
                    throw new Error("ownKeys boom");
                }
            })
            """
        )

        with self.assertRaises(pyv8.JSException):
            dir(js_obj)

        # 枚举异常后上下文仍应保持可用。
        self.assertEqual(self.context.exec_js("2 + 2"), 4)

    def test_constructor_exception_does_not_crash(self):
        """JavaScript 构造函数抛出异常时应安全返回 JSException。"""

        Constructor = self.context.exec_js(
            """
            function Constructor() {
                throw new Error("construct boom");
            }
            Constructor
            """
        )

        with self.assertRaises(pyv8.JSException):
            pyv8.new(Constructor)

        # 构造失败不能影响同一上下文中的后续执行。
        self.assertEqual(self.context.exec_js("3 + 3"), 6)

    def test_timeout_cleanup_allows_next_execution(self):
        """执行超时后应清理定时器，并允许上下文继续运行。"""

        self.context.timeout = 0.05

        with self.assertRaises(pyv8.JavaScriptTerminated):
            self.context.exec_js("while (true) {}")

        # 清除超时设置后，上下文不应残留终止状态。
        self.context.timeout = 0
        self.assertEqual(self.context.exec_js("4 + 4"), 8)


if __name__ == "__main__":
    unittest.main()
