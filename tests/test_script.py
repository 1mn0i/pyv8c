import unittest
import pyv8

class TestScript(unittest.TestCase):
    """测试JavaScript脚本的编译和执行"""
    
    def setUp(self):
        """每个测试前创建一个新的上下文"""
        class Window: pass
        self.context = pyv8.Context(Window())
    
    def tearDown(self):
        """测试结束后清理上下文"""
        del self.context
    
    def test_script_creation(self):
        """测试脚本创建"""
        script = pyv8.Script(self.context, "1 + 1")
        self.assertIsInstance(script, pyv8.Script)
    
    def test_script_execution(self):
        """测试脚本执行"""
        script = pyv8.Script(self.context, "1 + 1")
        result = self.context.exec_js(script)
        self.assertEqual(result, 2)
    
    def test_script_with_filename(self):
        """测试带有文件名的脚本"""
        script = pyv8.Script(self.context, "1 + 1", "test.js")
        result = self.context.exec_js(script)
        self.assertEqual(result, 2)
    
    def test_script_syntax_error(self):
        """测试脚本语法错误"""
        with self.assertRaises(pyv8.JSException):
            pyv8.Script(self.context, "1 + ")
    
    def test_script_runtime_error(self):
        """测试脚本运行时错误"""
        script = pyv8.Script(self.context, "throw new Error('test error')")
        with self.assertRaises(pyv8.JSException):
            self.context.exec_js(script)
    
    def test_script_multiple_execution(self):
        """测试多次执行同一个脚本"""
        script = pyv8.Script(self.context, "var count = (typeof count === 'undefined' ? 0 : count) + 1; count")
        
        # 第一次执行
        result1 = self.context.exec_js(script)
        self.assertEqual(result1, 1)
        
        # 第二次执行
        result2 = self.context.exec_js(script)
        self.assertEqual(result2, 2)
        
        # 第三次执行
        result3 = self.context.exec_js(script)
        self.assertEqual(result3, 3)
    
    def test_script_with_context_variables(self):
        """测试使用上下文中的变量"""
        # 在上下文中设置变量
        self.context.exec_js("var x = 10; var y = 20;")
        
        # 创建使用这些变量的脚本
        script = pyv8.Script(self.context, "x + y")
        result = self.context.exec_js(script)
        self.assertEqual(result, 30)

if __name__ == "__main__":
    unittest.main()
