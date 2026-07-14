import gc
import unittest
import weakref

import pyv8


class TestLifecycleContracts(unittest.TestCase):
    """测试 Context、对象缓存和当前执行上下文的运行时契约。"""

    def setUp(self):
        class Window:
            pass

        self.context = pyv8.Context(Window())

    def tearDown(self):
        del self.context

    def test_same_python_object_keeps_javascript_identity(self):
        """同一 Python 对象在一个 Context 内应始终对应同一 JS 对象。"""

        class Payload:
            pass

        payload = Payload()

        def get_payload():
            return payload

        self.context.expose(first=payload, second=payload, get_payload=get_payload)
        self.assertTrue(self.context.exec_js("first === second"))
        self.assertTrue(self.context.exec_js("first === get_payload()"))
        self.assertTrue(self.context.exec_js("get_payload() === get_payload()"))

    def test_current_and_incumbent_context_inside_callback(self):
        """Python callback 中应能取得当前和 incumbent Context。"""

        observed = []

        def inspect_context():
            observed.append(
                (
                    pyv8.current_context() is self.context,
                    pyv8.incumbent_context() is self.context,
                )
            )
            return 42

        self.context.expose(inspect_context=inspect_context)
        self.assertEqual(self.context.exec_js("inspect_context()"), 42)
        self.assertEqual(observed, [(True, True)])

    def test_same_python_object_is_isolated_between_contexts(self):
        """同一 Python 对象在不同 Context 中应使用相互隔离的 JS wrapper。"""

        class OtherWindow:
            pass

        class Payload:
            pass

        payload = Payload()
        other_context = pyv8.Context(OtherWindow())
        try:
            self.context.expose(payload=payload)
            other_context.expose(payload=payload)

            self.context.exec_js("payload.contextOnly = 'top'")
            self.assertEqual(self.context.exec_js("payload.contextOnly"), "top")
            self.assertTrue(
                other_context.exec_js("payload.contextOnly === undefined")
            )

            other_context.exec_js("payload.contextOnly = 'iframe'")
            self.assertEqual(
                other_context.exec_js("payload.contextOnly"), "iframe"
            )
            self.assertEqual(self.context.exec_js("payload.contextOnly"), "top")
        finally:
            del other_context

    def test_javascript_wrapper_and_context_release_cycle(self):
        """JSObject 释放后 Context 和 Python payload 不应被缓存永久持有。"""

        class Payload:
            pass

        payload = Payload()
        payload_ref = weakref.ref(payload)
        self.context.expose(payload=payload)
        wrapper = self.context.exec_js("payload")

        del payload
        gc.collect()
        self.assertIsNotNone(payload_ref())

        del wrapper
        self.context.exec_js("delete globalThis.payload")
        self.context.gc()
        gc.collect()

        # Context 的 global template 仍拥有 Python global，但不应永久拥有 payload。
        self.assertIsNone(payload_ref())


if __name__ == "__main__":
    unittest.main()
