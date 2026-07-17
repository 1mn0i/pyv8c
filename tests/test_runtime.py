import gc
import subprocess
import sys
import textwrap
import unittest
import weakref
from pathlib import Path

import pyv8


class TestRuntimeLifecycle(unittest.TestCase):
    """测试 V8 Runtime 的资源所有权和显式关闭流程。"""

    def test_shutdown_rejects_live_context(self):
        """仍有活动 Context 时必须拒绝关闭 V8。"""

        context = pyv8.Context(object)

        with self.assertRaisesRegex(RuntimeError, "persistent handle"):
            pyv8.shutdown_v8()

        # 关闭失败不能破坏仍在使用的 Context。
        self.assertEqual(context.exec_js("20 + 22"), 42)

    def test_js_object_keeps_context_alive(self):
        """JSObject 存活期间应保留其所属 Context。"""

        class Window:
            pass

        context = pyv8.Context(Window())
        js_object = context.exec_js(
            "new (class Result { constructor() { this.answer = 42; } })()"
        )

        del context
        gc.collect()

        # JSObject 的操作依赖创建它的 Context，不能留下悬空 embedder data。
        self.assertEqual(js_object.answer, 42)

        del js_object
        gc.collect()
        pyv8.v8_gc()
        gc.collect()

    def test_shutdown_releases_runtime_in_subprocess(self):
        """独立进程应能完整释放 owner、isolate、allocator 和 platform。"""

        code = textwrap.dedent(
            """
            import gc
            import weakref

            import pyv8


            class Window:
                pass


            class Payload:
                pass


            context = pyv8.Context(Window())
            payload = Payload()
            payload_ref = weakref.ref(payload)
            context.payload = payload
            del payload

            # V8 internal field 的 owner 应在关闭前保留 Python 对象。
            assert payload_ref() is not None

            del context
            gc.collect()
            assert pyv8.shutdown_v8() is True
            gc.collect()
            assert payload_ref() is None

            # 关闭操作幂等，但 V8 官方不支持 Dispose 后重新初始化。
            assert pyv8.shutdown_v8() is False
            try:
                pyv8.Context(object)
            except RuntimeError as exc:
                assert "shut down" in str(exc)
            else:
                raise AssertionError("Context creation should fail after shutdown")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_subinterpreter_import_is_explicitly_rejected(self):
        """进程级 RuntimeManager 只能由主解释器加载。"""

        package_root = Path(pyv8.__file__).resolve().parent.parent.as_posix()
        code = textwrap.dedent(
            f"""
            import sys

            try:
                import _xxsubinterpreters as interpreters
            except ModuleNotFoundError:
                raise SystemExit(0)

            sys.path.insert(0, {package_root!r})
            import pyv8

            interpreter = interpreters.create()
            try:
                subinterpreter_code = (
                    "import sys; "
                    "sys.path.insert(0, {package_root!r}); "
                    "import pyv8"
                )
                try:
                    interpreters.run_string(interpreter, subinterpreter_code)
                except interpreters.RunFailedError as exc:
                    message = str(exc)
                    assert (
                        "main Python interpreter" in message
                        or "does not support loading in subinterpreters" in message
                    ), message
                else:
                    raise AssertionError(
                        "pyv8 import unexpectedly succeeded in a subinterpreter"
                    )
            finally:
                interpreters.destroy(interpreter)
            """
        )

        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repeated_context_creation_and_destruction_in_subprocess(self):
        """重复创建和销毁 Context 后应能完整关闭 V8 Runtime。"""

        code = textwrap.dedent(
            """
            import gc

            import pyv8


            class Window:
                pass


            for index in range(200):
                context = pyv8.Context(Window())
                assert context.exec_js(f"{index} + 1") == index + 1
                del context
                if index % 20 == 0:
                    gc.collect()
                    pyv8.v8_gc()

            gc.collect()
            pyv8.v8_gc()
            gc.collect()
            assert pyv8.shutdown_v8() is True
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
