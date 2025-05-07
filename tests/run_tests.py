#!/usr/bin/env python
import unittest
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# 导入所有测试模块
from tests.test_context import TestContext
from tests.test_script import TestScript
from tests.test_jsobject import TestJSObject
from tests.test_jsfunction import TestJSFunction
from tests.test_jspromise import TestJSPromise
from tests.test_jstypedarray import TestJSTypedArray
from tests.test_pyclass import TestPyClass
from tests.test_exception import TestException
from tests.test_conversion import TestConversion
from tests.test_faker import TestFaker
from tests.test_multiple_context import TestMultipleContext

def run_tests():
    """运行所有测试"""
    # 创建测试套件
    test_suite = unittest.TestSuite()
    
    # 添加测试类
    test_classes = [
        TestContext,
        TestScript,
        TestJSObject,
        TestJSFunction,
        TestJSPromise,
        TestJSTypedArray,
        TestPyClass,
        TestException,
        TestConversion,
        TestFaker,
        TestMultipleContext
    ]
    
    for test_class in test_classes:
        tests = unittest.defaultTestLoader.loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    return result.wasSuccessful()

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
