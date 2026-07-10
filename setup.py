#!/usr/bin/env python
"""
Setup script for pyv8 - Python bindings for the V8 JavaScript engine.
This is a simplified setup.py that works with pyproject.toml.

项目元数据在pyproject.toml中定义，
此文件仅包含扩展模块的配置。
"""
import os
import sys
from setuptools import setup, Extension

# 获取源文件列表
sources = \
    list(map(lambda path: os.path.join('src', path), filter(lambda path: path.endswith('.cpp'), os.listdir('src')))) + \
    list(map(lambda path: os.path.join('src/faker', path), filter(lambda path: path.endswith('.cpp'), os.listdir('src/faker'))))

# 库配置
v8_libraries = ['v8_monolith']
libraries = list(v8_libraries)

# 平台特定配置
if os.name == 'nt':
    library_dirs = ['v8/out/win/x64.release/obj']
    include_dirs = ['src', 'v8/include']
    extra_compile_args = ["/O2", "/GL", "/MT", "/EHsc", "/Gy", "/Zi", "/std:c++17", "/Zc:__cplusplus"]
    extra_link_args = []
    libraries.extend(['Dbghelp', 'Shlwapi', 'Winmm', 'Advapi32'])
else:
    library_dirs = ['v8/out/linux/x64.release/obj']
    include_dirs = ['src', 'v8/include']
    extra_compile_args = ['-fpermissive', '-Wunused-variable']
    extra_link_args = []

if sys.platform.startswith('linux'):
    libraries.append('rt')

if sys.platform.startswith('darwin'):
    extra_compile_args.append('-stdlib=libc++')

# 设置扩展模块
ext_modules = [
    Extension(
        'pyv8._pyv8',
        sources=sources,
        include_dirs=include_dirs,
        library_dirs=library_dirs,
        libraries=libraries,
        extra_compile_args=extra_compile_args,
        extra_link_args=extra_link_args,
        define_macros=[('Py_LIMITED_API', '0x030A0000')],
        py_limited_api=True,
        language='c++'
    )
]

# 使用setuptools的setup函数
setup(
    ext_modules=ext_modules,
    options={'bdist_wheel': {'py_limited_api': 'cp310'}},
)
