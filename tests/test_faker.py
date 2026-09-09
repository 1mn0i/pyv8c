from pyv8 import IdlSpec
import unittest
import pyv8
from pyv8.flag import *

class TestFaker(unittest.TestCase):
    """测试faker模块的功能"""

    def setUp(self):
        """每个测试前创建一个新的上下文"""


        class Window:
            # 只为有__v8_global_this__标记的全局对象设置faker对象
            __v8_idl_spec__ = IdlSpec(
                name_get="__v8_name_get__",
                global_this=True,
                constructor_behavior=FlagConstructor.kDontAll,
                length=0,
                array=0,
                immutable=FlagImmutable.instance | FlagImmutable.prototype,
                has_constructor=1,
            )

            def __v8_name_get__(self, name):
                return None


        self.context = pyv8.Context(Window(), trace=True)

    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context

    # def test_faker(self):
    #     """测试faker对象的是否被创建"""
    #     faker = self.context.exec_js("globalThis.faker;")
    #     self.assertIsInstance(faker, pyv8.JSObject)

    # def test_v8_version(self):
    #     """测试window对象"""
    #     v8_version = self.context.exec_js("faker.v8_version")
    #     self.assertEqual(v8_version, '12.5.99')

    # def test_print(self):
    #     """测试print函数"""
    #     self.context.exec_js("faker.print('Hello, World!')")

    #     # 测试print函数不会调用对象的toString方法
    #     self.context.exec_js("a = {i:0, toString: function toString(){this.i+=1; return 'a'}}; faker.print(a, a);")
    #     self.assertEqual(self.context.exec_js("a.i"), 0)

    # def test_native_wrapper(self):
    #     """测试native_wrapper函数"""
    #     result = self.context.exec_js("""
    #         fn =faker.native_wrapper({
    #             name: "test",
    #             length: 0,
    #             cb: function () {
    #                 return "this is native wrapper function";
    #             }
    #         });
    #         fn.toString();
    #     """)
    #     self.assertEqual(result, "function test() { [native code] }")

    # def test_hook(self):
    #     """测试hook对象的属性访问"""
    #     self.context.exec_js("faker.hook=true;")
    #     self.context.exec_js("a=1;")
    #     self.context.exec_js("Window")

    def test_structuredClone(self):
        """测试StructuredClone"""
        code = r"""
        fn1 = function(){
            let a = 611n;
            let b = faker.structuredClone(a);
            return a===b;
        }

        test = function(x){
            return typeof x
        }

        fn2 = function(){
            let a = /\u006d\u0069\u0067\u0075\u0065\u006c\u0077\u0061\u0073\u0068\u0065\u0072\u0065/;
            let b = faker.structuredClone(a);
            return a===b;
        }

        fn3 = function(){
            let a = {a: 1, b: {c: 1}};
            let b = faker.structuredClone(a);
            return a===b;
        }

        fn4 = function(){
            let a = Object(611n);
            let b = faker.structuredClone(a);
            return a===b;
        }

        fn5 = function(){
            structuredClone(document);
        }
        """
        self.context.exec_js(code)
        print(self.context.test(self.context.fn1()))
        self.assertEqual(self.context.fn1(), True)
        self.assertEqual(self.context.fn2(), False)
        self.assertEqual(self.context.fn3(), False)
        self.assertEqual(self.context.fn4(), False)
        with self.assertRaises(pyv8.JSException):
            self.context.fn5()

    def test_callbacks_forward_more_than_ten_arguments(self):
        """验证 Faker 回调不会因参数超过内置缓存大小而越界。"""
        result = self.context.exec_js(r"""
            const args = Array.from({length: 32}, (_, index) => index);
            const documentAll = faker.new_document_all(function () {
                return arguments.length;
            });
            const nativeWrapper = faker.native_wrapper({
                cb: function () {
                    return arguments.length;
                }
            });
            [documentAll(...args), nativeWrapper(...args)];
        """)
        self.assertEqual(result, [32, 32])

    def test_run_js_requires_string_source(self):
        """验证 faker.run_js 对非字符串源码返回 JavaScript 类型错误。"""
        with self.assertRaises(pyv8.JSException):
            self.context.exec_js("faker.run_js(1, 'test.js')")

if __name__ == "__main__":
    unittest.main()
