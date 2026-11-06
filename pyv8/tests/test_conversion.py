from tests.values import snapshot

import gc
import sys
import unittest

from pyv8c import pyv8

class TestConversion(unittest.TestCase):
    """测试Python和JavaScript类型之间的转换"""
    
    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())
    
    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context
    
    def test_number_conversion(self):
        """测试数字类型转换"""
        # Python -> JavaScript
        self.context.expose(py_int=42, py_float=3.14)
        result = self.context.exec_js("py_int + py_float")
        self.assertAlmostEqual(result, 45.14)
        
        # JavaScript -> Python
        js_int = self.context.exec_js("42")
        js_float = self.context.exec_js("3.14")
        self.assertEqual(js_int, 42)
        self.assertAlmostEqual(js_float, 3.14)

    def test_strings_survive_both_directions_byte_for_byte(self):
        """字符串在两个方向上都必须是同一串字符。

        转换对短字符串走的是字节级快路(ASCII 原样,高位字节按 Latin-1),所以
        这里覆盖的正是"字节级捷径可能出错"的那些用例:高位字节、超出一个字节
        的字符、内嵌 NUL、以及内联缓冲两侧的长度。
        """
        cases = [
            "",
            "abc",
            "a" * 127,
            "a" * 128,
            "a" * 129,
            "\xe9",
            "caf\xe9",
            "\xe9" * 130,
            "h\u00e9llo w\u00f6rld",
            "\u4e16\u754c",
            "a\x00b",
            "\xff\xfe",
            "\U0001f600",
        ]

        def echo(value):
            self.assertIsInstance(value, str)
            return value

        self.context.expose(echo=echo)
        for text in cases:
            with self.subTest(text=repr(text)):
                self.context.expose(value=text)
                self.assertEqual(self.context.exec_js("typeof value"), "string")
                # JavaScript counts UTF-16 code units, not code points.
                self.assertEqual(
                    self.context.exec_js("value.length"),
                    len(text.encode("utf-16-le")) // 2,
                )
                self.assertEqual(self.context.exec_js("echo(value)"), text)
                self.assertEqual(self.context.exec_js("echo(echo(value))"), text)

    def test_strings_made_in_javascript_convert_unchanged(self):
        """JavaScript 造出来的字符串也要按同样的规则落到 Python。"""
        expected = {
            "''": "",
            "'abc'": "abc",
            "'\\u00e9'": "\xe9",
            "'\\u00e9'.repeat(200)": "\xe9" * 200,
            "'a'.repeat(300)": "a" * 300,
            "'\\u4e16\\u754c'": "\u4e16\u754c",
            "'\\ud83d\\ude00'": "\U0001f600",
            "'a\\u0000b'": "a\x00b",
        }
        for script, text in expected.items():
            with self.subTest(script=script):
                self.assertEqual(self.context.exec_js(script), text)

        # A lone surrogate has no UTF-8 encoding.  The conversion may replace it,
        # but it must not crash and must not lose the text around it.
        lone = self.context.exec_js("'a\\ud800b'")
        self.assertIsInstance(lone, str)
        self.assertTrue(lone.startswith("a") and lone.endswith("b"), repr(lone))

    def test_large_integer_conversion_does_not_truncate_on_lp64(self):
        """验证超过 Int32 的 Python 整数不会在 C++ 层被截断。"""
        self.context.expose(large_positive=3_000_000_000,
                            large_negative=-3_000_000_000)
        self.assertEqual(self.context.exec_js("large_positive"), 3_000_000_000)
        self.assertEqual(self.context.exec_js("large_negative"), -3_000_000_000)

    def test_unrepresentable_integer_conversion_reports_overflow(self):
        """验证无法转换为 JavaScript Number 的超大整数会立即报错。"""
        with self.assertRaises(OverflowError):
            self.context.expose(too_large=10 ** 400)
    
    def test_string_conversion(self):
        """测试字符串类型转换"""
        # Python -> JavaScript
        self.context.expose(py_str="Hello, World!")
        result = self.context.exec_js("py_str + '!'")
        self.assertEqual(result, "Hello, World!!")
        
        # JavaScript -> Python
        js_str = self.context.exec_js("'Hello, World!'")
        self.assertEqual(js_str, "Hello, World!")
        
        # Unicode字符
        unicode_str = "你好，世界！"
        self.context.expose(py_unicode=unicode_str)
        result = self.context.exec_js("py_unicode")
        self.assertEqual(result, unicode_str)
    
    def test_boolean_conversion(self):
        """测试布尔类型转换"""
        # Python -> JavaScript
        self.context.expose(py_true=True, py_false=False)
        result1 = self.context.exec_js("py_true === true")
        result2 = self.context.exec_js("py_false === false")
        self.assertTrue(result1)
        self.assertTrue(result2)
        
        # JavaScript -> Python
        js_true = self.context.exec_js("true")
        js_false = self.context.exec_js("false")
        self.assertEqual(js_true, True)
        self.assertEqual(js_false, False)

    def test_boolean_conversion_reference_stability(self):
        """测试重复转换布尔值不会泄漏 True 和 False 的引用。"""

        echo = self.context.exec_js("(value => value)")
        gc.collect()
        true_refcount = sys.getrefcount(True)
        false_refcount = sys.getrefcount(False)

        for _ in range(5000):
            true_value = echo(True)
            false_value = echo(False)
            if true_value is not True or false_value is not False:
                self.fail("JavaScript 布尔值没有转换回 Python 单例")
            del true_value
            del false_value

        gc.collect()
        is_immortal = getattr(sys, "_is_immortal", lambda value: False)
        if is_immortal(True) and is_immortal(False):
            # CPython 3.14 不再保证 immortal object 的可观察引用计数恒定。
            self.assertIs(echo(True), True)
            self.assertIs(echo(False), False)
        else:
            self.assertEqual(sys.getrefcount(True), true_refcount)
            self.assertEqual(sys.getrefcount(False), false_refcount)
    
    def test_none_null_undefined_conversion(self):
        """测试None/null/undefined类型转换"""
        # Python -> JavaScript
        self.context.expose(py_none=None)
        result1 = self.context.exec_js("py_none === undefined")
        self.assertTrue(result1)
        
        # JavaScript -> Python
        js_null = self.context.exec_js("null")
        js_undefined = self.context.exec_js("undefined")
        self.assertEqual(js_null, pyv8.Null)
        self.assertEqual(js_undefined, pyv8.Undefined)
    
    def test_list_array_conversion(self):
        """测试列表/数组类型转换"""
        # Python -> JavaScript
        py_list = [1, 2, 3, "four", True]
        self.context.py_list = py_list

        # 验证数组长度
        length = snapshot(self.context.exec_js("py_list.length"))
        self.assertEqual(length, 5)
        
        # 验证数组元素
        result0 = snapshot(self.context.exec_js("py_list[0]"))
        result3 = snapshot(self.context.exec_js("py_list[3]"))
        result4 = snapshot(self.context.exec_js("py_list[4]"))
        self.assertEqual(result0, 1)
        self.assertEqual(result3, "four")
        self.assertEqual(result4, True)
        
        # JavaScript -> Python
        js_array = snapshot(self.context.exec_js("[1, 2, 3, 'four', true]"))
        self.assertEqual(list(js_array), [1, 2, 3, "four", True])

    def test_js_self_referential_array_conversion(self):
        """测试 JavaScript 自引用数组转换为 Python 列表。"""

        # 创建一个首个元素指向数组自身的 JavaScript 数组。
        js_array = snapshot(self.context.exec_js("const a = []; a[0] = a; a"))

        # 转换后应保留自引用关系，而不是创建新的列表副本。
        self.assertIs(js_array[0], js_array)

    def test_js_indirect_cycle_and_shared_reference_conversion(self):
        """测试 JavaScript 间接循环和共享子对象转换。"""

        js_array = snapshot(self.context.exec_js(
            "const root = []; const child = {root}; "
            "root.push(child, child); root"
        ))

        # child.root 应回到根列表，两个数组元素也应指向同一个 Python 字典。
        self.assertIs(js_array[0]["root"], js_array)
        self.assertIs(js_array[0], js_array[1])

    def test_python_indirect_cycle_and_shared_reference_conversion(self):
        """测试 Python 间接循环和共享子对象转换。"""

        py_root = []
        py_child = {"root": py_root}
        py_root.extend((py_child, py_child))
        self.context.py_root = py_root

        # 转换后应保留循环关系和共享引用，而不是重复创建 JS 对象。
        self.assertTrue(self.context.exec_js("py_root[0].root === py_root"))
        self.assertTrue(self.context.exec_js("py_root[0] === py_root[1]"))

    def test_large_shared_reference_conversion(self):
        """测试超过栈内 visited 容量后的共享引用转换。"""

        py_items = [{"value": index} for index in range(20)]
        self.context.py_graph = {"items": py_items, "last": py_items[-1]}
        self.assertTrue(
            snapshot(self.context.exec_js("py_graph.items[19] === py_graph.last"))
        )

        js_graph = snapshot(self.context.exec_js(
            "const items = Array.from({length: 20}, (_, value) => ({value})); "
            "({items, last: items[19]})"
        ))
        self.assertIs(js_graph["items"][19], js_graph["last"])

    def test_bytes_arraybuffer_conversion(self):
        """测试 Python bytes 转换为 JavaScript ArrayBuffer。"""

        # 将包含边界字节值的 bytes 暴露到 JavaScript 上下文。
        self.context.py_bytes = b"\x01\x02\xff"

        # 验证 ArrayBuffer 长度和每个字节的内容保持不变。
        self.assertEqual(self.context.exec_js("py_bytes.byteLength"), 3)
        self.assertEqual(
            self.context.exec_js("Array.from(new Uint8Array(py_bytes)).join(',')"),
            "1,2,255",
        )
    
    def test_dict_object_conversion(self):
        """测试字典/对象类型转换"""
        # Python -> JavaScript
        py_dict = {"a": 1, "b": "two", "c": True}
        self.context.py_dict=py_dict
        
        # 验证对象属性
        result_a = self.context.exec_js("py_dict.a")
        result_b = self.context.exec_js("py_dict.b")
        result_c = self.context.exec_js("py_dict.c")
        self.assertEqual(result_a, 1)
        self.assertEqual(result_b, "two")
        self.assertEqual(result_c, True)
        
        # JavaScript -> Python
        js_object = self.context.exec_js("({a: 1, b: 'two', c: true})")
        self.assertEqual(js_object["a"], 1)
        self.assertEqual(js_object["b"], "two")
        self.assertEqual(js_object["c"], True)
    
    def test_nested_structure_conversion(self):
        """测试嵌套结构类型转换"""
        # Python -> JavaScript
        py_nested = {
            "array": [1, 2, 3],
            "object": {"a": 1, "b": 2},
            "mixed": [{"x": 1}, [2, 3], 4]
        }
        self.context.py_nested=py_nested
        
        # 验证嵌套结构
        array_length = snapshot(self.context.exec_js("py_nested.array.length"))
        object_a = snapshot(self.context.exec_js("py_nested.object.a"))
        mixed_0_x = snapshot(self.context.exec_js("py_nested.mixed[0].x"))
        mixed_1_1 = snapshot(self.context.exec_js("py_nested.mixed[1][1]"))
        
        self.assertEqual(array_length, 3)
        self.assertEqual(object_a, 1)
        self.assertEqual(mixed_0_x, 1)
        self.assertEqual(mixed_1_1, 3)
        
        # JavaScript -> Python
        js_nested = snapshot(self.context.exec_js("""
            ({
                array: [1, 2, 3],
                object: {a: 1, b: 2},
                mixed: [{x: 1}, [2, 3], 4]
            })
        """))
        
        self.assertEqual(list(js_nested["array"]), [1, 2, 3])
        self.assertEqual(js_nested["object"]["a"], 1)
        self.assertEqual(js_nested["mixed"][0]["x"], 1)
        self.assertEqual(list(js_nested["mixed"][1]), [2, 3])
        self.assertEqual(js_nested["mixed"][2], 4)
    
    def test_function_conversion(self):
        """测试函数类型转换"""
        # Python -> JavaScript
        def py_func(a, b):
            return a + b
        
        self.context.expose(py_func=py_func)
        result = self.context.exec_js("py_func(5, 3)")
        self.assertEqual(result, 8)
        
        # JavaScript -> Python
        js_func = self.context.exec_js("function test (a, b) { return a * b; }; test;")
        result = js_func(5, 3)
        self.assertEqual(result, 15)
    
    def test_date_conversion(self):
        """测试日期类型转换"""
        # JavaScript -> Python
        js_date = self.context.exec_js("new Date('2023-01-01T00:00:00Z')")
        
        # 验证日期对象
        self.assertIsInstance(js_date, pyv8.JSObject)
        
        # 获取日期的年份
        year = js_date.getUTCFullYear()
        self.assertEqual(year, 2023)
    
    def test_regexp_conversion(self):
        """测试正则表达式类型转换"""
        # JavaScript -> Python
        js_regexp = self.context.exec_js("/^hello/i")
        
        # 验证正则表达式对象
        self.assertIsInstance(js_regexp, pyv8.JSObject)
        
        # 测试正则表达式匹配
        test_result = self.context.exec_js("(function(re) { return re.test('Hello World'); })")(js_regexp)
        self.assertTrue(test_result)

    def test_bigint_conversion(self):
        """测试Bigint类型转换"""
        code = """
        get_type = function(x){
            return typeof x;
        }
        bigint_val = 666n;
        """
        self.context.exec_js(code)
        self.assertIsInstance(self.context.bigint_val, pyv8.JSValue)
        self.assertEqual(self.context.get_type(self.context.bigint_val), 'bigint')
        self.assertEqual(self.context.get_type(self.context.BigInt(11)), 'bigint')

    def test_a_proto_key_becomes_an_own_property(self):
        """字典的 __proto__ 键必须是自有属性，不能改写对象原型。

        JSON.parse 把 "__proto__" 当自有属性，Python 字典过桥也该如此：交给
        Object.prototype 上的 __proto__ 访问器处理，数据就能替换掉原型。
        """
        self.context.expose(give_dict=lambda: {"__proto__": {"polluted": 1}, "a": 2})
        keys, prototype_is_object, leaked = snapshot(self.context.exec_js(
            "(() => { const o = give_dict(); return [Object.keys(o),"
            " Object.getPrototypeOf(o) === Object.prototype, typeof o.polluted]; })()"
        ))
        self.assertEqual(keys, ["__proto__", "a"])
        self.assertIs(prototype_is_object, True)
        self.assertEqual(leaked, "undefined")

    def test_a_key_that_is_not_a_string_keeps_its_property_key_form(self):
        """非字符串键按 JavaScript 的属性键规则成键：1 与 False 各自可读。"""
        self.context.expose(give_dict=lambda: {1: "one", False: "no"})
        keys, one, no = snapshot(self.context.exec_js(
            "(() => { const o = give_dict(); return [Object.keys(o), o[1], o.false]; })()"
        ))
        self.assertEqual(keys, ["1", "false"])
        self.assertEqual(one, "one")
        self.assertEqual(no, "no")

    def test_building_an_object_does_not_run_a_setter_from_the_prototype(self):
        """转换 Python 字典不该触发页面上 Object.prototype 的 setter。"""
        self.context.exec_js(
            "Object.defineProperty(Object.prototype, 'trap', {"
            " set(value) { this.__trapped = value; }, configurable: true });"
        )
        self.context.expose(give_dict=lambda: {"trap": 1})
        value, setter_skipped = snapshot(self.context.exec_js(
            "(() => { const o = give_dict();"
            " return [o.trap, typeof o.__trapped === 'undefined']; })()"
        ))
        self.assertEqual(value, 1)
        self.assertIs(setter_skipped, True)

    def test_building_an_array_does_not_run_a_setter_from_the_prototype(self):
        """转换 Python 列表同理：不该触发 Array.prototype 的 setter。"""
        self.context.exec_js(
            "Object.defineProperty(Array.prototype, '0', {"
            " set(value) { this.__trapped = value; }, configurable: true });"
        )
        self.context.expose(give_list=lambda: [7, 8])
        first, length, setter_skipped = snapshot(self.context.exec_js(
            "(() => { const a = give_list();"
            " return [a[0], a.length, typeof a.__trapped === 'undefined']; })()"
        ))
        self.assertEqual(first, 7)
        self.assertEqual(length, 2)
        self.assertIs(setter_skipped, True)

if __name__ == "__main__":
    unittest.main()
