import gc
import threading
import unittest

import pyv8


class TestMultipleIsolate(unittest.TestCase):
    """测试多个 V8 Isolate 的隔离、所有权和关闭契约。"""

    @staticmethod
    def _close_isolates(*isolates):
        gc.collect()
        for isolate in isolates:
            if not isolate.closed:
                isolate.close()

    def test_contexts_use_independent_javascript_heaps(self):
        """不同 Isolate 的全局变量和内建原型修改必须互不影响。"""

        class Window:
            pass

        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        context_a = pyv8.Context(Window, isolate=isolate_a)
        context_b = isolate_b.context(Window)
        try:
            context_a.exec_js(
                "globalThis.marker = 'A'; Array.prototype.isolateMarker = 'A'"
            )
            context_b.exec_js(
                "globalThis.marker = 'B'; Array.prototype.isolateMarker = 'B'"
            )

            self.assertEqual(context_a.exec_js("marker"), "A")
            self.assertEqual(context_b.exec_js("marker"), "B")
            self.assertEqual(context_a.exec_js("[].isolateMarker"), "A")
            self.assertEqual(context_b.exec_js("[].isolateMarker"), "B")
        finally:
            del context_a, context_b
            self._close_isolates(isolate_a, isolate_b)

    def test_javascript_handles_cannot_cross_isolates(self):
        """JSObject、JSFunction 和 Script 在进入 V8 前必须校验所属 Isolate。"""

        class Window:
            pass

        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        context_a = isolate_a.context(Window)
        context_b = isolate_b.context(Window)
        foreign_object = context_a.exec_js(
            "new (class ForeignObject { constructor() { this.value = 42; } })()"
        )
        foreign_function = context_a.exec_js("value => value")
        foreign_script = pyv8.Script(context_a, "40 + 2")
        local_function = context_b.exec_js("value => value")
        try:
            with self.assertRaisesRegex(RuntimeError, "another isolate"):
                local_function(foreign_object)
            with self.assertRaisesRegex(RuntimeError, "another isolate"):
                local_function(foreign_function)
            with self.assertRaisesRegex(RuntimeError, "another isolate"):
                context_b.expose(foreign=foreign_object)
            with self.assertRaisesRegex(RuntimeError, "another isolate"):
                context_b.exec_js(foreign_script)
        finally:
            del local_function, foreign_script, foreign_function, foreign_object
            del context_a, context_b
            self._close_isolates(isolate_a, isolate_b)

    def test_same_python_object_gets_one_wrapper_per_isolate(self):
        """同一 Python 对象在不同 Isolate 中必须生成相互独立的 JS wrapper。"""

        class Window:
            pass

        class Payload:
            pass

        payload = Payload()
        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        context_a = isolate_a.context(Window)
        context_b = isolate_b.context(Window)
        try:
            context_a.expose(payload=payload)
            context_b.expose(payload=payload)
            context_a.exec_js("payload.isolateOnly = 'A'")

            self.assertEqual(context_a.exec_js("payload.isolateOnly"), "A")
            self.assertIs(context_b.exec_js("payload.isolateOnly"), pyv8.Undefined)
        finally:
            del context_a, context_b
            self._close_isolates(isolate_a, isolate_b)

    def test_timeout_only_terminates_target_isolate(self):
        """一个 Isolate 的执行超时不能污染另一个 Isolate。"""

        class Window:
            pass

        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        context_a = isolate_a.context(Window, timeout=0.01)
        context_b = isolate_b.context(Window)
        try:
            with self.assertRaises(pyv8.JavaScriptTerminated) as terminated:
                context_a.exec_js("while (true) {}")
            del terminated

            self.assertEqual(context_b.exec_js("6 * 7"), 42)
            self.assertEqual(context_a.exec_js("20 + 22"), 42)
        finally:
            del context_a, context_b
            self._close_isolates(isolate_a, isolate_b)

    def test_close_rejects_live_handles_and_shutdown_rejects_custom_isolate(self):
        """Isolate 有活动句柄时不得关闭，进程关闭前也必须先关闭自定义 Isolate。"""

        class Window:
            pass

        isolate = pyv8.Isolate()
        context = isolate.context(Window)
        try:
            with self.assertRaisesRegex(RuntimeError, "persistent handle"):
                isolate.close()
            with self.assertRaisesRegex(RuntimeError, "custom isolate"):
                pyv8.shutdown_v8()

            # 关闭失败后，原有 Context 必须仍然可以继续执行。
            self.assertEqual(context.exec_js("21 * 2"), 42)
        finally:
            del context
            gc.collect()

        self.assertTrue(isolate.close())
        self.assertTrue(isolate.closed)
        self.assertFalse(isolate.close())

    def test_explicit_current_context_is_scoped_to_isolate(self):
        """current_context 和 incumbent_context 应按显式 Isolate 查询。"""

        class Window:
            pass

        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        context_a = isolate_a.context(Window)
        context_b = isolate_b.context(Window)
        observations = []

        def inspect_context():
            observations.append(
                (
                    pyv8.current_context(isolate_a) is context_a,
                    pyv8.incumbent_context(isolate_a) is context_a,
                    pyv8.current_context(isolate_b) is None,
                )
            )
            return 42

        try:
            context_a.expose(inspect_context=inspect_context)
            self.assertEqual(context_a.exec_js("inspect_context()"), 42)
            self.assertEqual(observations, [(True, True, True)])
        finally:
            del context_a, context_b
            self._close_isolates(isolate_a, isolate_b)

    def test_separate_threads_can_enter_separate_isolates(self):
        """不同线程应能通过 Locker 分别进入各自的 Isolate。"""

        class Window:
            pass

        isolate_a = pyv8.Isolate()
        isolate_b = pyv8.Isolate()
        results = {}
        errors = []

        def run(isolate, name, value):
            try:
                context = isolate.context(Window)
                results[name] = context.exec_js(f"{value} * 2")
                del context
            except BaseException as exc:
                errors.append(exc)

        thread_a = threading.Thread(target=run, args=(isolate_a, "a", 20))
        thread_b = threading.Thread(target=run, args=(isolate_b, "b", 21))
        thread_a.start()
        thread_b.start()
        thread_a.join()
        thread_b.join()

        try:
            self.assertEqual(errors, [])
            self.assertEqual(results, {"a": 40, "b": 42})
        finally:
            self._close_isolates(isolate_a, isolate_b)


if __name__ == "__main__":
    unittest.main()
