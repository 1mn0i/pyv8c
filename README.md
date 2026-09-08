# pyv8

pyv8 是一个基于 Google V8 的 CPython C++ 扩展，用于在 Python 中执行 JavaScript，
并在 Python 对象与 V8 对象之间建立双向桥接。

本项目主要面向 JavaScript 逆向和浏览器补环境场景。相比只提供 `eval()` 的运行器，
pyv8 更关注 Chromium-like 构造器、原型链、属性描述符、interceptor、cross-origin
检查和调用 hook，为批量模拟浏览器内置对象提供底层 API。

当前项目版本为 **2.1.2**，默认验证的 V8 版本记录在 `V8_VERSION`，当前为
**15.2.20.1**。

## 核心能力

- 在 Python 中创建 V8 Context 并执行 JavaScript
- 在 Python 和 JavaScript 之间转换基础类型、容器、函数和对象
- 从 Python 类生成 Chromium-like 构造器和原型对象
- 配置 Web IDL 风格的 attribute、operation 和 constant
- 实现 named/indexed property interceptor
- 支持 cross-origin access check 和独立 cross-origin handler
- 记录 getter、setter、method 和 constructor hook
- 操作 JavaScript 对象、函数、Promise 和 TypedArray
- 创建多个相互隔离的 V8 Isolate
- 在 Python 与 JavaScript 之间可靠传播异常
- 通过 Chrome DevTools 调试嵌入式 V8 Context
- 使用 CPython Limited API 构建 `cp310-abi3` 扩展

## 支持范围

| 项目 | 当前状态 |
| --- | --- |
| CPython | 3.10 至 3.14 |
| Python ABI | `cp310-abi3` |
| 默认 V8 | `15.2.20.1` |
| Windows | 主要开发和完整测试平台 |
| Linux/macOS | 包含构建分支，需要在目标平台编译并验证 V8 产物 |
| 多 Isolate | 支持，JS 句柄不能跨 Isolate 使用 |
| subinterpreter | 不支持 |
| free-threaded CPython | 不支持 |

V8 的 C++ API 会随版本变化。构建器可以下载任意正式数字 tag，但只有
`V8_VERSION` 中的版本经过当前源码验证。升级 V8 后若出现编译错误，需要同步适配绑定代码。

## 快速开始

```python
import pyv8


class Window:
    __v8_idl_spec__ = pyv8.IdlSpec(
        global_this=True,
    )


context = pyv8.Context(Window, ctx_type="Top")

result = context.exec_js(
    """
    function add(left, right) {
        return left + right;
    }
    add(20, 22);
    """,
    filename="example.js",
)

print(result)  # 42
```

暴露 Python 函数和对象：

```python
import pyv8


class Window:
    pass


class Navigator:
    __v8_idl_spec__ = pyv8.IdlSpec(
        attributes=(
            pyv8.AttributeSpec(
                name="userAgent", getter="get_user_agent",
                property_flags=pyv8.V8Attribute.DontEnum,
                side_effect=pyv8.SideEffectType.kHasNoSideEffect,
            ),
        ),
    )

    def get_user_agent(self):
        return "pyv8-browser/1.0"


def sha256(text):
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


context = pyv8.Context(Window())
context.expose(sha256, navigator=Navigator())

print(context.exec_js("navigator.userAgent"))
print(context.exec_js("sha256('payload')"))
```

IDL 映射统一使用 C++ 定义的 `pyv8.IdlSpec`，完整字段与 realm 装配契约见 [IDL_SPEC.md](doc/IDL_SPEC.md)。

## 安装

### 安装 wheel

下载与目标操作系统和 CPU 架构匹配的 wheel，然后执行：

```bash
python -m pip install path/to/pyv8-2.1.2-cp310-abi3-<platform>.whl
```

`cp310-abi3` wheel 可以由 CPython 3.10 至 3.14 加载，但 Windows、Linux、macOS
以及 x64、arm64 仍然需要各自对应的 wheel。

### 从源码构建

先安装 Python 构建工具：

```bash
python -m pip install --upgrade pip setuptools wheel build tox
```

选择并准备 V8：

```bash
# 查看可选版本
python setup.py list_v8_versions --limit=15

# 交互选择、下载并编译 V8
python setup.py build_v8 --select

# 编译扩展到 pyv8 包目录
python setup.py build_ext --inplace
```

在无法使用代理的网络中，可以强制直连：

```bash
python setup.py build_v8 --select --no-proxy
```

使用显式代理：

```bash
python setup.py build_v8 --select --proxy=http://127.0.0.1:7890
```

构建 ABI3 wheel：

```bash
python310 -m build --wheel
```

Windows 构建 V8 `15.2.20.1` 时，建议显式设置工具链路径：

```powershell
$env:PYV8_V8_VERSION = "15.2.20.1"
$env:PYV8_VS_HOME = "D:\soft\Microsoft\vs2026"
$env:PYV8_VS_VERSION = "2026"
$env:PYV8_WINDOWS_SDK = "D:\Windows Kits\10"
$env:PYV8_BUILD_ROOT = "D:\code\pyv8env\_pyv8\.pyv8-build"
$env:DEPOT_TOOLS_WIN_TOOLCHAIN = "0"
python setup.py build_v8
python setup.py build_ext --inplace
```

详细的编译器要求、环境变量、自定义 V8 路径和 ICU 数据约束见
[完整使用指南](doc/USER_GUIDE.md#安装与构建)。

## Chrome DevTools

```python
import pyv8


class Window:
    __v8_idl_spec__ = pyv8.IdlSpec(
        global_this=True,
    )


def run_script(context):
    context.exec_js(
        """
        function inspectMe(value) {
            return value * 2;
        }
        inspectMe(21);
        """,
        filename="reverse-target.js",
    )


context = pyv8.Context(Window())
pyv8.start_devtools(context, callback=run_script, port=9005)
```

程序会输出一个 `devtools://` URL。将它粘贴到 Chrome 地址栏即可连接。
Chrome 130 及以上版本的 CSP 要求 WebSocket 使用 `127.0.0.1`，因此不要把 URL
中的地址改回 `localhost`。

## 文档

- [完整使用指南](doc/USER_GUIDE.md)
- [Context 与脚本执行](doc/USER_GUIDE.md#context-与脚本执行)
- [Python/JavaScript 类型转换](doc/USER_GUIDE.md#类型转换)
- [Chromium-like 类与原型](doc/USER_GUIDE.md#chromium-like-类与原型)
- [Property interceptor](doc/USER_GUIDE.md#property-interceptor)
- [Cross-origin 与 hook](doc/USER_GUIDE.md#cross-origin-与-hook)
- [多 Isolate 和生命周期](doc/USER_GUIDE.md#多-isolate-与生命周期)
- [DevTools 调试](doc/USER_GUIDE.md#devtools-调试)
- [常见问题](doc/USER_GUIDE.md#常见问题)

## 测试

项目使用 tox 4 构建一次 `cp310-abi3` wheel，并在 Python 3.10 至 3.14 的隔离环境中
安装和测试同一个 wheel：

```bash
python314 -m tox
```

只测试指定版本：

```bash
python314 -m tox -e py310
python314 -m tox -e py314
```

快速原地编译并运行 pytest：

```bash
python setup.py build_ext --inplace --force
python -m pytest
```

## 重要限制

- `import pyv8` 会初始化进程级 V8 Runtime 和默认 Isolate。
- `shutdown_v8()` 是不可逆操作，关闭后不能在同一进程重新初始化 V8。
- 关闭 Isolate 前必须释放它创建的 Context、Script、JSObject 和 JSFunction。
- JSObject、JSFunction、JSPromise、JSTypeArray 和 Script 不能跨 Isolate 使用。
- Python `None` 映射为 JavaScript `undefined`；需要 `null` 时使用 `pyv8.Null`。
- `icudtl.dat` 必须与编译 `_pyv8` 时选择的 V8/ICU 构建匹配。
- V8 的 native fatal error 无法由 Python 异常或 loguru 捕获，应通过资源限制、超时和
  ICU 指纹校验提前防止。

## 致谢

- [Google V8](https://v8.dev/)
- [Chromium](https://www.chromium.org/)

## 联系方式

- 作者：conlin
- 邮箱：995018884@qq.com
- GitHub：https://github.com/ConlinH/_pyv8
