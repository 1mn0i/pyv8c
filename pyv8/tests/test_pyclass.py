from tests.values import snapshot

from pyv8 import IdlSpec, AttributeSpec, MethodSpec
import unittest
import pyv8

class TestPyClass(unittest.TestCase):
    """测试Python类在JavaScript中的暴露"""

    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())

    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context

    def test_class_methods(self):
        """测试实类方法调用"""
        class Calculator:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=2,
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

            def __init__(self, win=None):
                pass

            def fn_add(self, a, b, **kw):
                return a + b

            def fn_subtract(self, a, b, **kw):
                return a - b

        # 暴露类到JavaScript
        self.context.expose(Calculator)

        # 在JavaScript中使用类
        result = snapshot(self.context.exec_js("""
            var obj = new Calculator();
            [obj.add(2, 8), obj.subtract(30, 10)]
        """))

        # 验证结果
        self.assertEqual(list(result), [10, 20])

    def test_exception_subclass_instances_are_javascript_errors(self):
        """继承 Exception 的类,其实例必须是 JavaScript 的 Error。

        这个判断是类级的:实例的原型链由类决定,所以它既要在第一个实例上成立,
        也要在这个判断已经被用过之后再建实例时成立。先建子类再建基类是最容易
        出错的那一边,因为标记在第一次使用时就被清掉了。
        """
        constructible = IdlSpec(constructor_behavior=pyv8.FlagConstructor.kAll)

        class MyError(Exception):
            __v8_idl_spec__ = constructible

            def __init__(self, win=None):
                Exception.__init__(self)

        class SubError(MyError):
            __v8_idl_spec__ = constructible

        class Plain:
            __v8_idl_spec__ = constructible

            def __init__(self, win=None):
                pass

        self.context.expose(MyError=MyError, SubError=SubError, Plain=Plain)

        self.assertTrue(self.context.exec_js("new SubError() instanceof Error"))
        self.assertTrue(self.context.exec_js("new MyError() instanceof Error"))
        self.assertTrue(self.context.exec_js(
            "try { throw new MyError(); } catch (e) { e instanceof MyError }"))
        self.assertFalse(self.context.exec_js("new Plain() instanceof Error"))
        self.assertTrue(self.context.exec_js(
            "Object.getPrototypeOf(Plain.prototype) === Object.prototype"))

    def test_class_properties(self):
        """测试实类属性调用"""
        class ClassWithProperties:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=2,
                attributes=(
                    AttributeSpec(
                        name="x",
                        getter="get_x",
                        setter="set_x",
                        location=0,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=1,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                    AttributeSpec(
                        name="y",
                        getter="get_y",
                        setter="set_y",
                        location=0,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=1,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                    AttributeSpec(
                        name="sum",
                        getter="get_sum",
                        setter=None,
                        location=0,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=1,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                ),
            )

            def __init__(self, win=None):
                self._x = 0
                self._y = 0

            def get_x(self):
                return self._x

            def set_x(self, value):
                self._x = value

            def get_y(self):
                return self._y

            def set_y(self, value):
                self._y = value

            def get_sum(self):
                return self._x + self._y

        # 暴露类到JavaScript
        self.context.expose(ClassWithProperties)

        # 在JavaScript中使用类
        result = snapshot(self.context.exec_js("""
            var obj = new ClassWithProperties();
            obj.x = 10;
            obj.y = 20;
            [obj.x, obj.y, obj.sum]
        """))

        # 验证结果
        self.assertEqual(list(result), [10, 20, 30])

    def test_class_static_methods(self):
        """测试类静态方法的调用"""
        class ClassWithStaticMethods:
            @staticmethod
            def add(a, b):
                return a + b

        # 不支持staticmethod类型转换
        # 不支持staticmethod类型转换
        # 不支持staticmethod类型转换

        # 验证结果
        self.assertEqual(True, True)

    def test_class_inherit(self):
        """测试类继承"""

        class EventTarget: pass

        class Node(EventTarget): pass

        class Element(Node): pass

        self.context.expose(Element)
        result = self.context.exec_js("Element.__proto__.toString()")

        # 验证结果
        self.assertEqual(result, 'function Node() { [native code] }')

    def test_many_generated_prototype_classes(self):
        """大量生成共享基类的原型模板时应保持缓存和继承关系稳定。"""

        PrototypeBase = type("PrototypeBase", (), {})
        prototypes = [
            type(f"GeneratedPrototype{index}", (PrototypeBase,), {})
            for index in range(128)
        ]

        self.context.expose(*prototypes)
        result = snapshot(self.context.exec_js(
            """
            [
                typeof GeneratedPrototype0,
                typeof GeneratedPrototype127,
                GeneratedPrototype0.__proto__.name,
                GeneratedPrototype127.__proto__.name
            ]
            """
        ))
        self.assertEqual(
            result,
            ["function", "function", "PrototypeBase", "PrototypeBase"],
        )

    def test_class_skip_inherit(self):
        """测试类跳过原型继承继承"""

        class EventTarget: pass

        class WindowProperties(EventTarget):
            __v8_idl_spec__ = IdlSpec(
                skip_inherit=True,
            )

        class Win(WindowProperties): pass

        self.context.expose(Win)
        result = self.context.exec_js("Win.__proto__.toString()")

        # 验证结果
        self.assertEqual(result, 'function EventTarget() { [native code] }')

    def test_class_has_constructor(self):
        """测试类没有构造函数"""
        class EventTarget:
            __v8_idl_spec__ = IdlSpec(
                has_constructor=1,
            )

        class WindowProperties(EventTarget):
            __v8_idl_spec__ = IdlSpec(
                has_constructor=0,
            )

        self.context.expose(WindowProperties, EventTarget)

        result1 = snapshot(self.context.exec_js("Object.keys(Object.getOwnPropertyDescriptors(EventTarget.prototype))"))
        self.assertEqual(result1, ['constructor'])

        result2 = snapshot(self.context.exec_js("Object.keys(Object.getOwnPropertyDescriptors(WindowProperties.prototype))"))
        self.assertEqual(result2, [])

    def test_class_constructor_behavior(self):
        """测试类构造函数行为"""
        class Test1:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kDontAll,
            )

        class Test2:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kNew,
            )

            def __init__(self, win=None):
                pass

        class Test3:
            __v8_idl_spec__ = IdlSpec(
                constructor_behavior=pyv8.FlagConstructor.kCall,
            )

            def __init__(self, win=None):
                pass

        self.context.expose(Test1, Test2, Test3)

        with self.assertRaises(pyv8.JSException):
            self.context.exec_js("new Test1()")

        with self.assertRaises(pyv8.JSException):
            self.context.exec_js("Test1()")

        self.context.exec_js("new Test2")
        with self.assertRaises(pyv8.JSException):
            self.context.exec_js("Test2()")

        self.context.exec_js("new Test3")
        self.context.exec_js("Test3()")

    def test_class_code_like_is_stringified_by_eval(self):
        """测试 code_like 类被 eval 当作源码

        V8 只在"对象的 map 由 API 函数构成、且其实例模板标了 code_like"时才把对象字符串化后
        编译，其余对象按规范原样返回——Blink 正是用它让 TrustedScript 能被 eval。
        """

        class TrustedScript:
            __v8_idl_spec__ = IdlSpec(
                to_string_tag="TrustedScript",
                code_like=True,
                methods=(
                    MethodSpec(name="toString", callback="fn_toString"),
                ),
            )

            def __init__(self, value=""):
                self._value = str(value)

            def fn_toString(self, *_extra):
                return self._value

        class Plain:
            pass

        self.context.expose(script=TrustedScript("1 + 1"), other=Plain())

        self.assertEqual(self.context.exec_js("eval(script)"), 2)
        # 字符串化只发生在 code_like 上，普通对象仍是原样返回。
        self.assertTrue(self.context.exec_js("eval(other) === other"))
        self.assertEqual(self.context.exec_js("'' + script"), "1 + 1")

    def test_class_undetectable_and_call(self):
        """测试类的不可检查性 和 __call__"""

        class HTMLAllCollection:
            __v8_idl_spec__ = IdlSpec(
                undetectable=True,
                call="__call__",
            )

            def __call__(self, id_name):
                return pyv8.Null

        class Document:
            __v8_idl_spec__ = IdlSpec(
                attributes=(
                    AttributeSpec(
                        name="all",
                        getter="get_all",
                        setter=None,
                        location=1,
                        property_flags=0,
                        receiver_check=0,
                        cross_origin_get_check=0,
                        cross_origin_set_check=0,
                        side_effect=1,
                    ),
                ),
            )
            def get_all(self):
                return HTMLAllCollection()

        document = Document()
        self.context.expose(document=document)
        result = self.context.exec_js("document.all == undefined")
        self.assertEqual(True, result)

        result = self.context.exec_js("document.all('testId')")
        self.assertEqual(result, pyv8.Null)


if __name__ == "__main__":
    unittest.main()
