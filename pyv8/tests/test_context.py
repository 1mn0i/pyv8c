from pyv8 import IdlSpec, MethodSpec
import gc
import sys
import unittest
import pyv8

class TestContext(unittest.TestCase):
    """测试JavaScript上下文的创建和基本功能"""

    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())

    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context

    def test_context_creation(self):
        """测试上下文创建"""
        self.assertIsInstance(self.context, pyv8.Context)

    def test_close_releases_context_and_is_idempotent(self):
        """Explicit closure rejects further V8 entry and is idempotent."""
        self.context.close()
        self.context.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.context.exec_js("1 + 1")

    def test_hide_global_removes_an_exposed_name(self):
        """hide_global 让一个全局名消失得和从未装上一样，再撤一次则回报未删除。"""

        self.context.exec_js("globalThis.Thing = 1")
        self.assertTrue(self.context.exec_js("'Thing' in globalThis"))
        self.assertTrue(self.context.hide_global("Thing"))
        self.assertEqual(self.context.exec_js("typeof Thing"), "undefined")
        self.assertFalse(self.context.exec_js("'Thing' in globalThis"))
        self.assertFalse(self.context.hide_global("Thing"))

    def test_exec_js(self):
        """测试执行简单的JavaScript代码"""
        result = self.context.exec_js("1 + 1")
        self.assertEqual(result, 2)

        result = self.context.exec_js("(new Error).stack", "https://www.xxx.com/test.js")
        self.assertIn("https://www.xxx.com/test.js", result)

    def test_exec_js_does_not_retain_transient_scripts(self):
        """验证重复执行源码不会让 Context 永久持有临时 Script。"""

        source = "40 + 2"
        references_before = sys.getrefcount(source)
        for _ in range(100):
            self.assertEqual(self.context.exec_js(source), 42)
        gc.collect()

        self.assertEqual(sys.getrefcount(source), references_before)

    def test_glob(self):
        """测试glob属性"""
        # context.glob就是浏览器的globalThis
        self.assertIsInstance(self.context.glob, pyv8.JSObject)

    def test_global_access(self):
        """测试访问全局对象"""

        self.context.exec_js("var globalVar = 'hello world';")
        # 测试下面代码是否 等价于浏览器中执行 globalThis.globalVar
        result = self.context.globalVar
        self.assertEqual(result, "hello world")

        # 测试下面代码是否 等价于浏览器中执行 globalThis.global_var2 = 1
        self.context.global_var2 = 1
        self.assertEqual(self.context.exec_js("global_var2"), 1)

        # globalThis.non_existent返回undefined, 但self.context.non_existent抛属性异常
        with self.assertRaises(AttributeError):
            self.context.non_existent

    def test_js_obj(self):
        """测试获取通过python object获取js object"""
        class A:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kAll,
            )

            def __init__(self, win=None):
                pass

        self.context.expose(A)
        # py_obj是python对象 直接访问name属性 相当于python代码 a=A(); a.name
        py_obj = self.context.exec_js("a = new A; a.name= 'test'; a")
        with self.assertRaises(AttributeError):
            py_obj.name

        # 通过context.js_obj方法可获得py_obj对象对应的js对象，便可调用的是js对象的方法
        js_obj = self.context.js_obj(py_obj)
        self.assertEqual(js_obj.name, 'test')

    def test_expose(self):
        """测试context.expose方法"""

        def add(a, b):
            return a + b

        class Calculator:
            __v8_idl_spec__ = IdlSpec(
                methods=(
                    MethodSpec(
                        name="add",
                        callback="fn_add",
                        length=0,
                        location=0,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                    MethodSpec(
                        name="subtract",
                        callback="fn_subtract",
                        length=0,
                        location=0,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_check=0,
                        side_effect=0,
                    ),
                ),
            )
            def fn_add(self, a, b):
                return a + b

            def fn_subtract(self, a, b):
                return a - b

        calc = Calculator()
        self.context.expose(add, calc=calc)

        result = self.context.exec_js("add(5, 3)")
        self.assertEqual(result, 8)

        add_result = self.context.exec_js("calc.add(10, 5)")
        self.assertEqual(add_result, 15)

        subtract_result = self.context.exec_js("calc.subtract(10, 5)")
        self.assertEqual(subtract_result, 5)

    def test_timeout(self):
        """测试JavaScript执行超时"""
        # 设置一个非常短的超时时间
        self.context.timeout = 0.1

        # 尝试执行一个无限循环
        with self.assertRaises(pyv8.JavaScriptTerminated):
            self.context.exec_js("while(true) {}")

    def test_invalid_timeout_preserves_previous_value(self):
        """timeout setter 转换失败时应返回 -1，并保留原来的超时值。"""

        self.context.timeout = 1.5
        with self.assertRaises(TypeError):
            self.context.timeout = object()
        self.assertEqual(self.context.timeout, 1.5)

    def test_new(self):
        """测试js的new关键字"""
        JsUint8Array = self.context.exec_js("Uint8Array")
        js_obj = pyv8.new(JsUint8Array, [1, 2, 3])
        self.context.test = js_obj
        self.assertEqual(self.context.exec_js("test instanceof Uint8Array"), True)

        # new Promise((resolve, reject)=>{ resolve( "resolve result" )}).then(console.debug)
        def fn_promise(fn_resolve, fn_reject, *arg):
            fn_resolve("resolve result")
        Promise = self.context.Promise
        self.context.p = pyv8.new(Promise, fn_promise)
        self.context.exec_js("var ret; p.then((v)=>{ret=v})")
        self.assertEqual(self.context.ret, "resolve result")

    def test_gc(self):
        """测试垃圾回收"""
        # 创建一些JavaScript对象
        self.context.exec_js("var a = {}; for(var i=0; i<1000; i++) { a[i] = 'test'; }")

        # 调用垃圾回收
        self.context.gc()

        # 这个测试主要是确保gc()方法不会崩溃

if __name__ == "__main__":
    unittest.main()
