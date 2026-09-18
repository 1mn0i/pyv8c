from tests.values import snapshot

import time
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

        self.context.close()

    def test_script_failures_preserve_error_type_and_context_usability(self):
        """Compile and execution failures must not poison subsequent execution."""
        for source, name in (("1 +", "SyntaxError"),
                             ("throw new Error('boom')", "Error"),
                             ("missingBinding", "ReferenceError")):
            with self.subTest(name=name):
                with self.assertRaises(pyv8.JSException) as caught:
                    self.context.exec_js(source, filename="failure.js")
                self.assertEqual(caught.exception.original.name, name)
                self.assertIn("failure.js", str(caught.exception))
                self.assertEqual(self.context.exec_js("6 * 7"), 42)

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

    def test_python_exception_round_trip_and_js_metadata(self):
        """Python 异常应能穿过 JavaScript 并保留原类型、消息和堆栈。"""

        class PythonFailure(ValueError):
            __v8_error__ = True

        def raise_python_failure():
            raise PythonFailure("python callback boom")

        self.context.expose(raise_python_failure=raise_python_failure)

        # JavaScript 未捕获时，应在 Python 侧恢复原始异常类型。
        with self.assertRaisesRegex(PythonFailure, "python callback boom"):
            self.context.exec_js("raise_python_failure()")

        # JavaScript 捕获时，应能读取异常消息和 V8 捕获的 stack。
        result = snapshot(self.context.exec_js(
            """
            try {
                raise_python_failure();
            } catch (error) {
                [error.message, typeof error.stack, error.stack.includes("python callback boom")];
            }
            """
        ))
        self.assertEqual(result, ["python callback boom", "string", True])
        self.assertEqual(snapshot(self.context.exec_js("5 + 5")), 10)

    def test_timeout_cleanup_allows_next_execution(self):
        """执行超时后应清理定时器，并允许上下文继续运行。"""

        self.context.timeout = 0.05

        with self.assertRaises(pyv8.JavaScriptTerminated):
            self.context.exec_js("while (true) {}")

        # 清除超时设置后，上下文不应残留终止状态。
        self.context.timeout = 0
        self.assertEqual(self.context.exec_js("4 + 4"), 8)

    def test_timeout_nested_execution_cleanup(self):
        """嵌套执行超时后应清理内外两层 TimeoutGuard。"""

        self.context.timeout = 0.2

        def run_nested_timeout():
            return self.context.exec_js("while (true) {}", timeout=0.02)

        self.context.expose(run_nested_timeout=run_nested_timeout)
        with self.assertRaises(pyv8.JavaScriptTerminated):
            self.context.exec_js("run_nested_timeout()")

        self.context.timeout = 0
        self.assertEqual(self.context.exec_js("6 * 7"), 42)

    def test_timeout_early_exception_cancels_timer(self):
        """脚本提前抛出异常时应取消尚未触发的超时定时器。"""

        self.context.timeout = 0.05
        with self.assertRaises(pyv8.JSException):
            self.context.exec_js("throw new Error('early exit')")

        # 等待超过原超时时间，残留定时器不应终止后续执行。
        time.sleep(0.08)
        self.assertEqual(self.context.exec_js("20 + 22"), 42)


if __name__ == "__main__":
    unittest.main()
