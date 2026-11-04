"""首次使用才启动 V8：导入 pyv8 不产生任何引擎副作用。

每个用例都必须在独立进程里跑：启动 V8 不可逆，同一个进程无法先断言"尚未启动"、
再去把它启动起来。
"""

import subprocess
import sys
import textwrap
import unittest


class TestLazyStartup(unittest.TestCase):
    """验证导入时机、首次使用时机与配置入口。"""

    def run_code(self, code):
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    def assert_prints(self, code, expected):
        result = self.run_code(code)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), expected)

    def assert_fails_with(self, code, message):
        result = self.run_code(code)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn(message, output)
        return output

    def test_import_starts_nothing(self):
        """导入、取版本、标记一个类都不启动 V8。"""

        self.assert_prints(
            """
            import pyv8

            print(pyv8.is_initialized(), hasattr(pyv8, "Context"))
            pyv8.v8_version()
            print(pyv8.is_initialized())
            pyv8.hidden(type("Thing", (), {}))
            print(pyv8.is_initialized())
            """,
            ["False", "True", "False", "False"],
        )

    def test_first_context_starts_v8(self):
        """第一个 Context 才把 V8 启动起来，关闭后仍归 shutdown_v8() 所有。"""

        self.assert_prints(
            """
            import pyv8

            print(pyv8.is_initialized())
            context = pyv8.Context(object)
            print(pyv8.is_initialized(), context.exec_js("20 + 22"))
            context.close()
            print(pyv8.shutdown_v8())
            """,
            ["False", "True", "42", "True"],
        )

    def test_first_isolate_starts_v8(self):
        """自定义 Isolate 走同一条按需启动路径。"""

        self.assert_prints(
            """
            import pyv8

            isolate = pyv8.Isolate()
            print(pyv8.is_initialized())
            context = isolate.context(object)
            print(context.exec_js("6 * 7"))
            context.close()
            isolate.close()
            """,
            ["True", "42"],
        )

    def test_first_use_may_come_from_another_thread(self):
        """首次使用来自其他线程时同样能启动（宿主按 realm 起线程的场景）。"""

        self.assert_prints(
            """
            import threading

            import pyv8

            results = []

            def worker():
                context = pyv8.Context(object)
                results.append(context.exec_js("40 + 2"))
                context.close()

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join()
            print(results, pyv8.is_initialized())
            """,
            ["[42]", "True"],
        )

    def test_use_after_shutdown_reports_shut_down(self):
        """关闭是不可逆的：再次使用报既有错误，而不是把运行期悄悄重启。"""

        self.assert_fails_with(
            """
            import pyv8

            context = pyv8.Context(object)
            context.close()
            assert pyv8.shutdown_v8() is True
            pyv8.Context(object)
            """,
            "V8 has been shut down",
        )

    def test_configure_after_start_is_rejected(self):
        """V8 启动后不再接受新的启动参数与数据文件。"""

        self.assert_fails_with(
            """
            import pyv8

            pyv8.Context(object)
            pyv8.configure(params=["--expose-gc"])
            """,
            "already started",
        )

    def test_configure_before_start_applies_flags(self):
        """configure 记录的启动参数对随后的启动生效。"""

        self.assert_prints(
            """
            import pyv8

            print(pyv8.configure(params=["--expose-gc"]))
            print(pyv8.is_initialized())
            context = pyv8.Context(object)
            print(context.exec_js("typeof gc"))
            """,
            ["True", "False", "function"],
        )

    def test_configure_rejects_bad_arguments_before_start(self):
        """参数类型错误在配置时就报出来，而不是拖到首次使用。"""

        self.assert_fails_with(
            """
            import pyv8

            pyv8.configure(params="--expose-gc")
            """,
            "params parameter must be a list",
        )

    def test_missing_icu_data_names_the_remedy(self):
        """ICU 数据文件读不到时给出可操作的错误。"""

        output = self.assert_fails_with(
            """
            import pyv8

            pyv8.configure(icudtl_path="pyv8-there-is-no-such-directory/icudtl.dat")
            pyv8.Context(object)
            """,
            "icudtl.dat",
        )
        self.assertIn("configure(icudtl_path=...)", output)


if __name__ == "__main__":
    unittest.main()
