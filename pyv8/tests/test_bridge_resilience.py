from tests.values import snapshot
from tests.error_family import declared

from pyv8c.pyv8 import IdlSpec
import math
import unittest

from pyv8c import pyv8


class TestBridgeResilience(unittest.TestCase):
    """测试 Python/V8 数据与异常桥接的边界和故障恢复。"""

    def setUp(self):
        class Window:
            pass

        self.context = pyv8.Context(Window())

    def tearDown(self):
        del self.context

    def test_declared_classes_arrive_as_the_errors_they_name(self):
        """每一类声明到的 JavaScript 错误，都是表里写的那一个。"""

        callbacks = {}
        expected_names = {
            "type": "TypeError",
            "range": "RangeError",
            "reference": "ReferenceError",
            "syntax": "SyntaxError",
            "error": "Error",
        }

        for key, javascript in expected_names.items():
            exception_type = declared(key.title() + "Failure", javascript=javascript)

            def callback(exception_type=exception_type, key=key):
                raise exception_type(f"{key} boom")

            callbacks[f"raise_{key}"] = callback

        self.context.expose(**callbacks)
        for key, expected_name in expected_names.items():
            result = snapshot(self.context.exec_js(
                f"""
                try {{
                    raise_{key}();
                }} catch (error) {{
                    [error.name, error.message];
                }}
                """
            ))
            self.assertEqual(result, [expected_name, f"{key} boom"])

    def test_the_declaration_names_the_error_not_the_python_class(self):
        """A class arrives as its declaration says, whatever it is called here."""

        for error_name in (
            "Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError"
        ):
            with self.subTest(error_name=error_name):
                exception_type = declared("DomainFailure", javascript=error_name)

                def fail(exception_type=exception_type):
                    raise exception_type("declared mapping")

                self.context.expose(fail=fail)
                result = snapshot(self.context.exec_js(
                    "try { fail(); } catch (error) { [error.name, error.message]; }"
                ))
                self.assertEqual(result, [error_name, "declared mapping"])

    def test_a_declared_interceptor_failure_is_silent_only_in_sloppy_mode(self):
        """A declaration says a failed write or delete may go unraised where V8 allows it."""

        class LegacyWriteFailure(pyv8.JsTypeError, pyv8.JsInterceptorFailure):
            """A failed legacy write or delete, which may go unraised where V8 allows it."""

        class LegacyTarget:
            __v8_idl_spec__ = IdlSpec(
                name_get="__v8_name_get__",
                name_set="__v8_name_set__",
                name_del="__v8_name_del__",
            )
            def __v8_name_get__(self, name):
                return None

            def __v8_name_set__(self, name, value):
                raise LegacyWriteFailure("legacy write rejected")

            def __v8_name_del__(self, name):
                raise LegacyWriteFailure("legacy delete rejected")

        self.context.expose(target=LegacyTarget())
        self.assertEqual(snapshot(self.context.exec_js("target.value = 1")), 1)
        self.assertFalse(snapshot(self.context.exec_js("delete target.value")))
        result = snapshot(self.context.exec_js(
            """
            try {
                (function () { "use strict"; target.value = 1; })();
            } catch (error) {
                [error.name, error.message];
            }
            """
        ))
        self.assertEqual(result, ["TypeError", "legacy write rejected"])

    def test_a_declared_error_rethrown_arrives_as_that_error(self):
        """JavaScript catches what a realm was given, so that is what comes back."""

        BridgeFailure = declared("BridgeFailure", javascript="TypeError", base=RuntimeError)

        def fail():
            raise BridgeFailure("round trip boom")

        self.context.expose(fail=fail)
        with self.assertRaises(pyv8.JSException) as raised:
            self.context.exec_js("try { fail(); } catch (error) { throw error; }")
        self.assertIn("round trip boom", str(raised.exception))
        self.assertEqual(self.context.exec_js("40 + 2"), 42)

    def test_exception_with_failing_string_conversion(self):
        """异常自身无法转成字符串时应使用安全消息并保持上下文可用。"""

        class BrokenStringError(pyv8.JsError):
            def __str__(self):
                raise RuntimeError("broken __str__")

        def fail():
            raise BrokenStringError()

        self.context.expose(fail=fail)
        result = snapshot(self.context.exec_js(
            """
            try {
                fail();
            } catch (error) {
                [error.message, typeof error.stack];
            }
            """
        ))
        self.assertEqual(result, ["Python exception", "string"])
        self.assertEqual(snapshot(self.context.exec_js("21 * 2")), 42)

    def test_number_boundaries_and_special_values(self):
        """数值桥接应保留安全整数、NaN、Infinity 和负零语义。"""

        self.context.expose(
            max_safe_integer=2**53 - 1,
            positive_infinity=math.inf,
            negative_infinity=-math.inf,
            not_a_number=math.nan,
        )
        result = snapshot(self.context.exec_js(
            """
            [
                max_safe_integer === Number.MAX_SAFE_INTEGER,
                positive_infinity === Infinity,
                negative_infinity === -Infinity,
                Number.isNaN(not_a_number)
            ]
            """
        ))
        self.assertEqual(result, [True, True, True, True])

        self.assertTrue(math.isnan(snapshot(self.context.exec_js("NaN"))))
        self.assertEqual(snapshot(self.context.exec_js("Infinity")), math.inf)
        negative_zero = snapshot(self.context.exec_js("-0"))
        self.assertEqual(math.copysign(1.0, negative_zero), -1.0)

    def test_unicode_nul_and_sparse_array_conversion(self):
        """Unicode、内嵌 NUL 和稀疏数组 hole 应安全穿过转换层。"""

        text = "prefix\x00中文😀suffix"
        self.context.expose(text=text)
        self.assertEqual(snapshot(self.context.exec_js("text")), text)
        self.assertTrue(snapshot(self.context.exec_js("text.includes('中文')")))

        sparse = snapshot(self.context.exec_js(
            "const value = []; value.length = 3; value[1] = 42; value"
        ))
        self.assertIs(sparse[0], pyv8.Undefined)
        self.assertEqual(sparse[1], 42)
        self.assertIs(sparse[2], pyv8.Undefined)

    def test_interceptor_exceptions_round_trip_and_recover(self):
        """named/indexed 的五类 handler 抛错后均应恢复原异常和 Context。"""

        class InterceptorFailure(RuntimeError):
            pass

        class ThrowingInterceptor:
            active_handler = None

            def _raise_if_active(self, handler):
                if self.active_handler == handler:
                    raise InterceptorFailure(f"{handler} boom")

            __v8_idl_spec__ = IdlSpec(
                name_get="__v8_name_get__",
                name_set="__v8_name_set__",
                name_query="__v8_name_query__",
                name_del="__v8_name_del__",
                name_enum="__v8_name_enum__",
                index_get="__v8_index_get__",
                index_set="__v8_index_set__",
                index_query="__v8_index_query__",
                index_del="__v8_index_del__",
                index_enum="__v8_index_enum__",
            )
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
