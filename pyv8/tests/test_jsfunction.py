from tests.values import snapshot

import unittest
import pyv8

class TestJSFunction(unittest.TestCase):
    """测试JavaScript函数的调用"""
    
    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())
    
    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context
    
    def test_create_js_function(self):
        """测试创建JavaScript函数"""
        js_func = self.context.exec_js("function add(a, b) { return a + b; }; add")
        self.assertIsInstance(js_func, pyv8.JSFunction)
    
    def test_call_js_function(self):
        """测试调用JavaScript函数"""
        js_func = self.context.exec_js("function add(a, b) { return a + b; }; add")
        result = js_func(5, 3)
        self.assertEqual(result, 8)
    
    def test_js_function_with_exception(self):
        """测试抛出异常的JavaScript函数"""
        js_func = self.context.exec_js("""
            function throwError() { 
                throw new Error('Test error'); 
            }; 
            throwError
        """)
        
        with self.assertRaises(pyv8.JSException):
            js_func()
    
    def test_js_function_with_callback(self):
        """测试带有回调的JavaScript函数"""
        # 创建一个Python回调函数
        def callback(x):
            return x * 2
        
        # 暴露回调函数到JavaScript
        self.context.expose(callback=callback)
        
        # 创建一个使用回调的JavaScript函数
        js_func = self.context.exec_js("""
            function processWithCallback(value, cb) {
                return cb(value);
            };
            processWithCallback
        """)
        
        # 调用JavaScript函数，传入Python回调
        result = js_func(5, callback)
        self.assertEqual(result, 10)
    
    def test_js_function_constructor(self):
        """测试JavaScript构造函数"""
        # 定义一个构造函数
        self.context.exec_js("""
            function Person(name, age) {
                this.name = name;
                this.age = age;
                this.greet = function() {
                    return 'Hello, my name is ' + this.name;
                };
            }
        """)
        
        # 获取构造函数
        Person = self.context.exec_js("Person")
        
        # 使用构造函数创建对象
        person = pyv8.new(Person, "Alice", 30)
        
        # 验证对象属性
        self.assertEqual(person.name, "Alice")
        self.assertEqual(person.age, 30)
        self.assertEqual(person.greet(), "Hello, my name is Alice")
    
    def test_js_function_arguments(self):
        """测试JavaScript函数的参数处理"""
        # 定义一个处理不同类型参数的函数
        js_func = snapshot(self.context.exec_js("""
            function processArgs(a, b, c, d, e) {
                return {
                    number: a,
                    string: b,
                    boolean: c,
                    array: d,
                    object: e
                };
            };
            processArgs
        """))
        
        # 调用函数，传入不同类型的参数
        result = js_func(
            42,                     # 数字
            "hello",                # 字符串
            True,                   # 布尔值
            [1, 2, 3],              # 数组
            {"key": "value"}        # 字典
        )
        
        # 验证结果
        self.assertEqual(result['number'], 42)
        self.assertEqual(result['string'], "hello")
        self.assertEqual(result['boolean'], True)
        self.assertEqual(result['array'].to_python(), [1, 2, 3])
        self.assertEqual(result['object']['key'], "value")

    def test_js_function_inline_and_heap_arguments(self):
        """测试小参数栈存储和大参数堆存储都能正确传递。"""

        js_func = self.context.exec_js(
            "(function () { return Array.from(arguments).join(','); })"
        )

        # 16 个参数命中栈内存路径，17 个参数命中堆内存路径。
        self.assertEqual(js_func(*range(16)), ",".join(map(str, range(16))))
        self.assertEqual(js_func(*range(17)), ",".join(map(str, range(17))))

    def test_js_constructor_inline_and_heap_arguments(self):
        """测试构造函数的小参数和大参数调用路径。"""

        constructor = self.context.exec_js(
            "(function () { this.count = arguments.length; })"
        )

        self.assertEqual(pyv8.new(constructor, *range(16)).count, 16)
        self.assertEqual(pyv8.new(constructor, *range(17)).count, 17)

if __name__ == "__main__":
    unittest.main()
