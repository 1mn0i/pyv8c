#!/usr/bin/env python
"""
运行pyv8测试的脚本
支持unittest和pytest两种方式
"""
import argparse
import os
import sys
import unittest

# 直接运行该脚本时，允许从当前源码树导入 pyv8。
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")
sys.path.insert(0, PROJECT_ROOT)


def run_unittest():
    """使用unittest运行所有测试"""
    test_suite = unittest.defaultTestLoader.discover(
        start_dir=TESTS_DIR,
        pattern="test_*.py",
        top_level_dir=PROJECT_ROOT,
    )

    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)

    return result.wasSuccessful()

def run_pytest():
    """使用pytest运行所有测试"""
    try:
        import pytest
    except ImportError:
        print("pytest未安装，请先执行：python -m pip install pytest")
        return False

    # 运行pytest
    return pytest.main(["-v", TESTS_DIR]) == 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行pyv8测试")
    parser.add_argument("--pytest", action="store_true", help="使用pytest运行测试")
    args = parser.parse_args()

    if args.pytest:
        success = run_pytest()
    else:
        success = run_unittest()

    sys.exit(0 if success else 1)
