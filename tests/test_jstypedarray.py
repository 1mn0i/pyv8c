from tests.values import snapshot

import unittest
import pyv8

class TestJSTypedArray(unittest.TestCase):
    """测试JavaScript类型化数组"""
    
    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())
    
    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context
    
    def test_is_typed_array(self):
        """测试是否为JSTypeArray对象"""
        typed_array = self.context.exec_js("new Uint8Array([1, 2, 3])")
        self.assertIsInstance(typed_array, pyv8.JSTypeArray)
        
    def test_create_typed_array(self):
        """测试创建JSTypeArray对象"""
        def get_typedarray():
            # 创建JSTypeArray
            return pyv8.JSTypeArray([1, 2, 3], pyv8.ArrayType.Uint8Array)
        
        self.context.expose(get_typedarray)
        result = self.context.exec_js("""
        pyobj = get_typedarray();
        pyobj instanceof Uint8Array && pyobj.toString() == "1,2,3" && pyobj[Symbol.toStringTag] == 'Uint8Array'
        """)
        self.assertEqual(result, True)
    
    def test_typed_array_repr(self):
        """测试JSTypeArray对象的repr"""
        typed_array = self.context.exec_js("new Int32Array([1, 2, 3, 4, 5])")
        self.assertEqual(str(typed_array), 'Int32Array([1,2,3,4,5])')

    
    def test_typed_array_length(self):
        """测试获取类型化数组的长度"""
        typed_array = self.context.exec_js("new Uint8Array([1, 2, 3, 4, 5])")
        self.assertEqual(typed_array.length, 5)
    
    def test_typed_array_access(self):
        """测试访问类型化数组的元素"""
        typed_array = self.context.exec_js("new Uint8Array([10, 20, 30, 40, 50])")
        self.assertEqual(typed_array[0], 10)
        self.assertEqual(typed_array[1], 20)
        self.assertEqual(typed_array[2], 30)
    
    def test_typed_array_set(self):
        """测试设置类型化数组的元素"""
        typed_array = self.context.exec_js("arr = new Uint8Array([10, 20, 30, 40, 50]); arr")
        typed_array[0] = 99

        # 验证元素已被修改
        value = self.context.exec_js("arr[0]")
        self.assertEqual(value, 99)
    
    def test_typed_array_methods(self):
        """测试类型化数组的方法"""
        typed_array = self.context.exec_js("""
            var arr = new Uint8Array([10, 20, 30, 40, 50]);
            arr
        """)
        
        # 测试subarray方法
        subarray = typed_array.subarray(1, 4)
        self.assertEqual(subarray.length, 3)
        
        # 测试元素值
        self.assertEqual(subarray[0], 20)
        self.assertEqual(subarray[1], 30)
        self.assertEqual(subarray[2], 40)

    def test_invalid_typed_array_item_reports_python_exception(self):
        """元素类型错误时不得返回没有设置异常的 NULL，也不得泄漏半初始化对象。"""

        def make_invalid_array():
            return pyv8.JSTypeArray(
                [1, "invalid"], pyv8.ArrayType.Uint8Array
            )

        self.context.expose(make_invalid_array)
        with self.assertRaisesRegex(TypeError, "item 1"):
            self.context.exec_js("make_invalid_array()")
        self.assertEqual(self.context.exec_js("40 + 2"), 42)

    def test_invalid_typed_array_type_is_rejected(self):
        """未知的 ArrayType 编号应转换为 ValueError。"""

        def make_invalid_array():
            return pyv8.JSTypeArray([1, 2, 3], 999)

        self.context.expose(make_invalid_array)
        with self.assertRaisesRegex(ValueError, "unsupported typed array type"):
            self.context.exec_js("make_invalid_array()")

    def test_plain_js_object_cannot_be_used_as_array_buffer(self):
        """普通 JSObject 不得被盲目转换成 v8::ArrayBuffer。"""

        plain_object = self.context.exec_js(
            "new (class PlainObject { constructor() { this.value = 1; } })()"
        )

        def make_invalid_array():
            return pyv8.JSTypeArray(
                plain_object, pyv8.ArrayType.Uint8Array
            )

        self.context.expose(make_invalid_array)
        with self.assertRaisesRegex(TypeError, "ArrayBuffer or ArrayBufferView"):
            self.context.exec_js("make_invalid_array()")
        self.assertEqual(self.context.exec_js("40 + 2"), 42)

    def test_array_buffer_view_can_create_uint8_view(self):
        """ArrayBufferView 输入应保留原视图的 byteOffset 和 byteLength。"""

        source_view = snapshot(self.context.exec_js(
            "new Uint16Array(new ArrayBuffer(8), 2, 2)"
        ))

        def make_byte_view():
            return pyv8.JSTypeArray(
                source_view, pyv8.ArrayType.Uint8Array
            )

        self.context.expose(make_byte_view)
        result = snapshot(self.context.exec_js(
            "const view = make_byte_view(); "
            "[view instanceof Uint8Array, view.byteOffset, view.byteLength]"
        ))
        self.assertEqual(result, [True, 2, 4])

    def test_array_buffer_view_preserves_requested_element_type(self):
        """验证从 ArrayBuffer 创建视图时使用请求的元素类型。"""
        source_view = snapshot(self.context.exec_js(
            "new Uint8Array(new ArrayBuffer(8), 0, 8)"
        ))

        def make_int32_view():
            return pyv8.JSTypeArray(source_view, pyv8.ArrayType.Int32Array)

        self.context.expose(make_int32_view)
        result = snapshot(self.context.exec_js(
            "const view = make_int32_view(); "
            "[view instanceof Int32Array, view.length, view.byteLength]"
        ))
        self.assertEqual(result, [True, 2, 8])

if __name__ == "__main__":
    unittest.main()
