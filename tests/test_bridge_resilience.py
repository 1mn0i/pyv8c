import math
import unittest

import pyv8


class TestBridgeResilience(unittest.TestCase):
    """测试 Python/V8 数据与异常桥接的边界和故障恢复。"""

    def setUp(self):
        class Window:
            pass

        self.context = pyv8.Context(Window())

    def tearDown(self):
        del self.context

    def test_python_exception_type_mapping(self):
        """带 V8 类型名称的 Python 异常应映射为对应 JavaScript Error。"""

        callbacks = {}
        expected_names = {
            "type": "TypeError",
            "range": "RangeError",
            "reference": "ReferenceError",
            "syntax": "SyntaxError",
            "error": "Error",
        }
        python_names = {
            "type": "V8TypeError",
            "range": "V8RangeError",
            "reference": "V8ReferenceError",
            "syntax": "V8SyntaxError",
            "error": "V8Error",
        }

        for key, class_name in python_names.items():
            exception_type = type(class_name, (Exception,), {"__v8_error__": True})

            def callback(exception_type=exception_type, key=key):
                raise exception_type(f"{key} boom")

            callbacks[f"raise_{key}"] = callback

        self.context.expose(**callbacks)
        for key, expected_name in expected_names.items():
            result = self.context.exec_js(
                f"""
                try {{
                    raise_{key}();
                }} catch (error) {{
                    [error.name, error.message];
                }}
                """
            )
            self.assertEqual(result, [expected_name, f"{key} boom"])

    def test_python_exception_rethrow_restores_original_type(self):
        """Python 异常经过 JavaScript catch/rethrow 后应恢复原异常。"""

        class BridgeFailure(RuntimeError):
            __v8_error__ = True

        def fail():
            raise BridgeFailure("round trip boom")

        self.context.expose(fail=fail)
        with self.assertRaisesRegex(BridgeFailure, "round trip boom"):
            self.context.exec_js(
                "try { fail(); } catch (error) { throw error; }"
            )
        self.assertEqual(self.context.exec_js("40 + 2"), 42)

    def test_exception_with_failing_string_conversion(self):
        """异常自身无法转成字符串时应使用安全消息并保持上下文可用。"""

        class BrokenStringError(Exception):
            __v8_error__ = True

            def __str__(self):
                raise RuntimeError("broken __str__")

        def fail():
            raise BrokenStringError()

        self.context.expose(fail=fail)
        result = self.context.exec_js(
            """
            try {
                fail();
            } catch (error) {
                [error.message, typeof error.stack];
            }
            """
        )
        self.assertEqual(result, ["Python exception", "string"])
        self.assertEqual(self.context.exec_js("21 * 2"), 42)

    def test_number_boundaries_and_special_values(self):
        """数值桥接应保留安全整数、NaN、Infinity 和负零语义。"""

        self.context.expose(
            max_safe_integer=2**53 - 1,
            positive_infinity=math.inf,
            negative_infinity=-math.inf,
            not_a_number=math.nan,
        )
        result = self.context.exec_js(
            """
            [
                max_safe_integer === Number.MAX_SAFE_INTEGER,
                positive_infinity === Infinity,
                negative_infinity === -Infinity,
                Number.isNaN(not_a_number)
            ]
            """
        )
        self.assertEqual(result, [True, True, True, True])

        self.assertTrue(math.isnan(self.context.exec_js("NaN")))
        self.assertEqual(self.context.exec_js("Infinity"), math.inf)
        negative_zero = self.context.exec_js("-0")
        self.assertEqual(math.copysign(1.0, negative_zero), -1.0)

    def test_unicode_nul_and_sparse_array_conversion(self):
        """Unicode、内嵌 NUL 和稀疏数组 hole 应安全穿过转换层。"""

        text = "prefix\x00中文😀suffix"
        self.context.expose(text=text)
        self.assertEqual(self.context.exec_js("text"), text)
        self.assertTrue(self.context.exec_js("text.includes('中文')"))

        sparse = self.context.exec_js(
            "const value = []; value.length = 3; value[1] = 42; value"
        )
        self.assertIs(sparse[0], pyv8.Undefined)
        self.assertEqual(sparse[1], 42)
        self.assertIs(sparse[2], pyv8.Undefined)

    def test_interceptor_exceptions_round_trip_and_recover(self):
        """named/indexed 的五类 handler 抛错后均应恢复原异常和 Context。"""

        class InterceptorFailure(RuntimeError):
            __v8_error__ = True

        class ThrowingInterceptor:
            active_handler = None

            def _raise_if_active(self, handler):
                if self.active_handler == handler:
                    raise InterceptorFailure(f"{handler} boom")

            def __v8_name_get__(self, name):
                self._raise_if_active("name_get")
                return None

            def __v8_name_set__(self, name, value):
                self._raise_if_active("name_set")
                return True

            def __v8_name_query__(self, name):
                self._raise_if_active("name_query")
                return None

            def __v8_name_del__(self, name):
                self._raise_if_active("name_del")
                return True

            def __v8_name_enum__(self):
                self._raise_if_active("name_enum")
                return []

            def __v8_index_get__(self, index):
                self._raise_if_active("index_get")
                return None

            def __v8_index_set__(self, index, value):
                self._raise_if_active("index_set")
                return True

            def __v8_index_query__(self, index):
                self._raise_if_active("index_query")
                return None

            def __v8_index_del__(self, index):
                self._raise_if_active("index_del")
                return True

            def __v8_index_enum__(self):
                self._raise_if_active("index_enum")
                return 0

        operations = {
            "name_get": "target.missing",
            "name_set": "target.value = 1",
            "name_query": "'missing' in target",
            "name_del": "delete target.missing",
            "name_enum": "Object.keys(target)",
            "index_get": "target[0]",
            "index_set": "target[0] = 1",
            "index_query": "0 in target",
            "index_del": "delete target[0]",
            "index_enum": "Object.keys(target)",
        }

        target = ThrowingInterceptor()
        self.context.expose(target=target)
        for handler, source in operations.items():
            with self.subTest(handler=handler):
                target.active_handler = handler
                with self.assertRaisesRegex(
                    InterceptorFailure, f"{handler} boom"
                ):
                    self.context.exec_js(source)
                target.active_handler = None
                self.assertEqual(self.context.exec_js("6 * 7"), 42)


if __name__ == "__main__":
    unittest.main()
