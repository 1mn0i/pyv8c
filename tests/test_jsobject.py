import unittest
import pyv8

class TestJSObject(unittest.TestCase):
    """测试JavaScript对象的操作"""
    
    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())
    
    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context
    
    def test_create_js_object(self):
        """测试创建JavaScript对象"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        self.assertIsInstance(js_obj, pyv8.JSObject)
    
    def test_access_js_object_property(self):
        """测试访问JavaScript对象的属性"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        self.assertEqual(js_obj.name, "xiaoming")
        self.assertEqual(js_obj.age, 18)
    
    def test_set_js_object_property(self):
        """测试设置JavaScript对象的属性"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
                get_name() {
                    return this.name;
                }
                get_age() {
                    return this.age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        
        # 修改现有属性
        js_obj.name = "modified"
        self.assertEqual(js_obj.name, "modified")
        
        # 添加新属性
        js_obj.new_prop = "new value"
        self.assertEqual(js_obj.new_prop, "new value")
    
    def test_delete_js_object_property(self):
        """测试删除JavaScript对象的属性"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
                get_name() {
                    return this.name;
                }
                get_age() {
                    return this.age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        
        # 删除属性
        del js_obj.name
        
        # 验证属性已被删除
        with self.assertRaises(AttributeError):
            _ = js_obj.name
    
    def test_js_object_dict_access(self):
        """测试通过字典方式访问JavaScript对象"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        
        # 使用字典方式访问
        self.assertEqual(js_obj["name"], "xiaoming")
        self.assertEqual(js_obj["age"], 18)
        
        # 使用字典方式设置
        js_obj["name"] = "modified"
        self.assertEqual(js_obj["name"], "modified")
        
        # 使用字典方式添加
        js_obj["new_prop"] = "new value"
        self.assertEqual(js_obj["new_prop"], "new value")
        
        # 使用字典方式删除
        del js_obj["name"]
        with self.assertRaises(Exception):
            _ = js_obj["name"]
    
    def test_js_object_dir(self):
        """测试使用dir()获取JavaScript对象的属性"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
            }
            new MyClass('xiaoming', 18);
        """)
        
        # 获取属性列表
        props = dir(js_obj)
        
        # 验证属性存在
        self.assertIn("name", props)
        self.assertIn("age", props)
    
    def test_js_object_equality(self):
        """测试JavaScript对象的相等性"""
        # 创建两个不同的对象引用，但内容相同
        obj1 = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
            }
            var obj = new MyClass('xiaoming', 18);
            obj
        """)
        obj2 = self.context.exec_js("obj")  # 引用同一个对象
        
        # 它们应该相等
        self.assertEqual(obj1, obj2)
        self.assertFalse(obj1 != obj2)
        with self.assertRaises(TypeError):
            _ = obj1 < obj2

        # Rich-comparison slots return Py_NotImplemented for unsupported
        # operations.  Exercise the path repeatedly so a borrowed singleton
        # returned by a newer CPython header cannot silently corrupt its
        # reference count on older runtimes.
        for _ in range(1000):
            with self.assertRaises(TypeError):
                _ = obj1 < obj2

    def test_uninitialized_js_object_reports_runtime_error(self):
        """验证手工创建的包装对象不会以无异常 NULL 返回给 Python。"""

        class DetachedJSObject(pyv8.JSObject):
            pass

        detached = DetachedJSObject()
        with self.assertRaisesRegex(RuntimeError, "no active V8 isolate"):
            _ = detached.missing

    def test_repr_honors_context_timeout(self):
        """验证自定义 toString 不会让 Python repr 永久阻塞。"""
        self.context.timeout = 0.05
        value = self.context.glob
        self.context.exec_js(
            "globalThis.toString = function () { while (true) {} }"
        )
        with self.assertRaises(pyv8.JavaScriptTerminated):
            repr(value)
    
    def test_js_object_function(self):
        """测试JavaScript对象函数调用"""
        js_obj = self.context.exec_js("""
            class MyClass {
                constructor(name, age) {
                    this.name = name;
                    this.age = age;
                }
                get_name() {
                    return this.name;
                }

                get_age() {
                    return this.age;
                }
            }
            new MyClass('xiaoming', 18);
        """)

        # 它们应该相等
        self.assertEqual(js_obj.get_name(), "xiaoming")
        self.assertEqual(js_obj.get_age(), 18)

    def test_js_object_private_style_property(self):
        """测试以下划线开头的 JS 属性仍会在 Python 属性查找后回退到 V8。"""

        js_obj = self.context.exec_js(
            "new (class PrivateValue { constructor() { this._value = 42; } })()"
        )

        self.assertEqual(js_obj._value, 42)
        js_obj._value = 84
        self.assertEqual(js_obj._value, 84)

if __name__ == "__main__":
    unittest.main()
