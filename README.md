# pyv8

pyv8是一个强大的Python扩展，提供了Python与Google V8 JavaScript引擎之间的桥梁。它允许您在Python中执行JavaScript代码，并实现Python与JavaScript之间的无缝交互。

pyv8主要是为了js逆向时补环境提供更多底层支持，为一比一模拟chromium内置原型对象提供基础api。

## 特性

- 在Python中执行JavaScript代码
- 将Python对象暴露给JavaScript环境
- 访问和操作JavaScript对象
- 支持JavaScript Promise
- 支持JavaScript TypedArray
- 内置调试工具（DevTools）
- 支持JavaScript异常处理

## 安装

### 系统要求

- Python 3.10 或更高版本
- 支持的平台：Windows, Linux, macOS

### 通过pip安装

```bash
pip install pyv8
```

### 从源码构建

1. 克隆仓库：

```bash
git clone https://github.com/ConlinH/_pyv8.git
cd pyv8
```

2. 使用pyproject.toml构建和安装（推荐）：

```bash
# 开发模式安装
python -m pip install -e .

# 或者直接安装
python -m pip install .
```

3. 构建可发布的 ABI3 wheel：

```bash
python310 -m pip install --upgrade build
python310 -m build --wheel
```

生成的 `cp310-abi3` wheel 可安装到 CPython 3.10 及以上版本。

### 选择并编译 V8 版本

仓库默认复用 `V8_VERSION` 中记录的 V8 版本以及本地 `v8/` 目录，不会在普通
`build_ext` 或 wheel 构建时访问网络。需要使用其他 V8 正式版本时，可以在打包命令中
显式指定版本并启用下载：

不知道具体 V8 tag 时，可以先查看精简后的版本列表，或直接使用交互菜单：

```powershell
# 列出各发布分支最新的 patch tag
python setup.py list_v8_versions --limit=15

# 选择版本，然后下载或复用对应的 V8
python setup.py build_v8 --select

# build_v8 会记住选择，后续打包不需要再次输入版本
python310 -m build --wheel
```

列表中的 `default, tested` 表示当前项目默认验证版本；其他版本会标记为
`unverified`，表示该 tag 可以下载和编译 V8，但绑定代码可能仍需适配对应版本的 C++
API。交互选择保存在本地 `.pyv8-version` 中且不会提交 Git，删除该文件即可恢复
`V8_VERSION` 中的项目默认值。

CI 或非交互环境仍建议使用环境变量：

```powershell
# Windows PowerShell
$env:PYV8_V8_VERSION = "13.1.201.22"
$env:PYV8_BUILD_V8 = "1"
$env:PYV8_V8_JOBS = "8"
python310 -m build --wheel
```

```bash
# Linux / macOS
PYV8_V8_VERSION=13.1.201.22 \
PYV8_BUILD_V8=1 \
PYV8_V8_JOBS=8 \
python3.10 -m build --wheel
```

构建器会自动安装 depot_tools、下载对应 V8 tag、执行 `gclient sync`，再通过 GN 和
autoninja 编译 `v8_monolith`。不同版本保存在
`.pyv8-build/versions/<version>/v8`，后续使用相同版本和 GN 参数时会直接复用缓存。

也可以先单独准备 V8，再执行扩展构建：

```powershell
python setup.py build_v8 --v8-version=13.1.201.22 --v8-jobs=8
$env:PYV8_V8_VERSION = "13.1.201.22"
python setup.py build_ext --inplace
```

已有自行编译的 V8 时，不需要下载：

```powershell
$env:PYV8_V8_VERSION = "13.1.201.22"
$env:PYV8_V8_HOME = "D:\v8-workspace\v8"
$env:PYV8_V8_OUT = "D:\v8-workspace\v8\out\x64.release"
$env:PYV8_ICU_DATA = "D:\v8-workspace\v8\out\x64.release\icudtl.dat"
python310 -m build --wheel
```

可用环境变量：

| 变量 | 作用 |
| --- | --- |
| `PYV8_V8_VERSION` | V8 正式版本 tag，例如 `12.5.99`。 |
| `PYV8_BUILD_V8` | 设置为 `1` 时下载并编译所选版本。 |
| `PYV8_FORCE_V8_BUILD` | 忽略匹配的构建缓存，强制重新编译。 |
| `PYV8_V8_JOBS` | GN/Ninja 并行任务数。 |
| `PYV8_BUILD_ROOT` | depot_tools 和版本缓存的根目录。 |
| `PYV8_DEPOT_TOOLS` | 复用指定的 depot_tools。 |
| `PYV8_PROXY` | 为 Git、depot_tools 和 CIPD 指定统一的 HTTP/HTTPS 代理。 |
| `PYV8_NO_PROXY` | 设置为 `1` 时忽略环境变量和 Git 全局代理，强制直连。 |
| `PYV8_VS_HOME` | Windows 下显式指定 Visual Studio 安装目录。 |
| `PYV8_VS_VERSION` | 与 `PYV8_VS_HOME` 配套的版本年份，默认 `2022`。 |
| `PYV8_WINDOWS_SDK` | Windows 下显式指定 Windows 10/11 SDK 根目录。 |
| `PYV8_V8_HOME` | 使用指定的 V8 源码或预编译目录。 |
| `PYV8_V8_OUT` | 指定包含 `obj/v8_monolith` 的输出目录。 |
| `PYV8_ICU_DATA` | 指定与所选 V8 匹配的 `icudtl.dat`。 |
| `PYV8_GN_ARGS` | 追加自定义 GN 参数。 |

构建器会自动把 Git 的 `http.proxy` / `https.proxy` 配置传给 depot_tools 和 CIPD。
如果 Git 与其他下载工具需要使用不同代理，可以显式设置：

```powershell
$env:PYV8_PROXY = "http://127.0.0.1:7890"
python setup.py build_v8 --select
```

不需要代理时，可以通过命令参数强制直连。该参数会忽略当前进程代理和 Git
全局 `http.proxy` / `https.proxy` 配置：

```powershell
python314 setup.py build_v8 --select --no-proxy
python314 setup.py list_v8_versions --no-proxy
```

也可以显式指定代理，而不修改系统或 Git 配置：

```powershell
python314 setup.py build_v8 --select --proxy=http://127.0.0.1:7890
```

CI 或持续使用直连模式时可以设置环境变量：

```powershell
$env:PYV8_NO_PROXY = "1"
python314 setup.py build_v8 --select
```

Windows 首次运行会安装 depot_tools 所需的 CIPD/Python 工具；成功后会禁用重复的
depot_tools 自更新，避免每次构建都产生额外网络请求。

V8 的 C++ API 会随版本变化。构建器可以选择和编译任意正式数字 tag，但当前源码默认
验证的是 `V8_VERSION` 中的版本；升级较大的 V8 版本时，编译错误通常表示绑定代码也需要
同步适配。

### 编译选项

pyv8 的扩展编译配置在 `setup.py` 和 `v8_build.py` 中定义。通常应优先使用上面的环境
变量，不需要直接修改构建脚本：

- 修改包含目录：`include_dirs`
- 修改库目录：`library_dirs`
- 修改链接的库：`libraries`
- 修改编译参数：`extra_compile_args`
- 修改链接参数：`extra_link_args`

额外的编译器参数仍可在 `setup.py` 的 `extra_compile_args` 中维护。

## 开发与测试

### 使用tox进行测试

项目使用 tox 4 测试 Python 3.10 至 3.14。tox 会使用 Python 3.10 构建一次
`cp310-abi3` wheel，然后在五个隔离环境中安装并测试同一个 wheel。

```bash
# 安装构建和测试工具
python314 -m pip install --upgrade build tox

# 运行Python 3.10至3.14的全部测试环境
python314 -m tox

# 运行特定Python版本的测试
python314 -m tox -e py310
python314 -m tox -e py314

# 运行代码风格检查
python314 -m tox -e lint
```

Windows 环境需要能够通过 `python310`、`python311`、`python312`、`python313`、
`python314` 命令启动对应解释器。测试命令使用隔离模式运行，确保加载的是 tox
安装的 wheel，而不是源码目录中的 `pyv8/_pyv8.pyd`。

### 使用pytest直接运行测试

```bash
# 安装pytest
python -m pip install pytest pytest-cov

# 快速原地编译扩展，不需要手工复制_pyv8.pyd
python setup.py build_ext --inplace --force

# 运行所有测试
python -m pytest

# 运行特定测试文件
python -m pytest tests/test_context.py

# 生成测试覆盖率报告
python -m pytest --cov=pyv8 tests/
```

### 使用传统的unittest运行测试

```bash
# 运行所有测试
python tests/run_tests.py

# 使用pytest运行
python tests/run_tests.py --pytest
```

## 快速入门

### 基本用法

```python
import pyv8

class Window: pass

# 创建一个JavaScript执行上下文
context = pyv8.Context(Window)

# 执行JavaScript代码
result = context.exec_js("1 + 2")
print(result)  # 输出: 3

# 执行更复杂的JavaScript代码
result = context.exec_js("""
    function add(a, b) {
        return a + b;
    }
    add(5, 7);
""")
print(result)  # 输出: 12
```

### 使用多个 Isolate

PyV8 2.0 支持在同一进程中创建多个相互隔离的 V8 Isolate。每个 Isolate 都有独立的
JavaScript heap、模板缓存、字符串缓存和 Python 对象 owner。

```python
import gc
import pyv8

class Window:
    pass

isolate_a = pyv8.Isolate()
isolate_b = pyv8.Isolate()

context_a = isolate_a.context(Window)
context_b = pyv8.Context(Window, isolate=isolate_b)

context_a.exec_js("globalThis.name = 'A'")
context_b.exec_js("globalThis.name = 'B'")

assert context_a.exec_js("name") == "A"
assert context_b.exec_js("name") == "B"

# 必须先释放该 Isolate 的 Context、Script 和 JS wrapper。
del context_a, context_b
gc.collect()
isolate_a.close()
isolate_b.close()
```

V8 句柄不能跨 Isolate 使用。将一个 Isolate 的 `JSObject`、`JSFunction` 或 `Script`
传给另一个 Isolate 时会抛出 `RuntimeError`，需要传输数据时应显式使用 JSON 或
structured clone。

### 将Python函数暴露给JavaScript

```python
import pyv8

class Window: pass

def hello(name):
    return f"Hello, {name} from Python!"

context = pyv8.Context(Window())
context.expose(hello)

result = context.exec_js("hello('World')")
print(result)  # 输出: Hello, World from Python!
```

### 使用DevTools调试

```python
import pyv8
from pyv8 import start_devtools

class Window:
    __v8_global_this__ = True


# 执行JavaScript代码
def callback(ctx):
    ctx.exec_js("""
        function test() {
            console.log('Testing DevTools');
            debugger;  // 这将在DevTools中触发断点
            return 'Done';
        }
        test();
    """)

context = pyv8.Context(Window())
# 启动DevTools调试服务器
start_devtools(context, callback=callback, port=9005)

# 控制台会输出DevTools URL，可以在Chrome浏览器中打开进行调试
```

## 高级用法

查看测试用例


## 致谢

- [Google V8 Engine](https://v8.dev/)

## 联系方式

- 作者：conlin
- 邮箱：995018884@qq.com
- GitHub：[https://github.com/ConlinH/_pyv8](https://github.com/ConlinH/_pyv8)
