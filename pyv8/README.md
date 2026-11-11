# pyv8

桥接内核重构后的接口和关闭语义见 [BRIDGE_KERNEL](../docs/pyv8/bridge-kernel.md)，
实测结果和待验收事项见 [重构报告](../docs/pyv8/bridge-refactor-results.md)。

pyv8 是一个基于 Google V8 的 CPython C++ 扩展，用于在 Python 中执行 JavaScript，
并在 Python 对象与 V8 对象之间建立双向桥接。

本项目主要面向 JavaScript 逆向和浏览器补环境场景。相比只提供 `eval()` 的运行器，
pyv8 更关注 Chromium-like 构造器、原型链、属性描述符、interceptor、cross-origin
检查和调用追踪（trace），为批量模拟浏览器内置对象提供底层 API。

内置桥接模块与发行包的版本均为 **0.1.0**。默认 V8 版本记录在 `V8_VERSION`，当前为
**15.4.68**，对应 Chromium 154.0.8030.0 使用的 V8 修订。

## 核心能力

- 在 Python 中创建 V8 Context 并执行 JavaScript
- 在 Python 和 JavaScript 之间转换基础类型、容器、函数和对象
- 从 Python 类生成 Chromium-like 构造器和原型对象
- 配置 Web IDL 风格的 attribute、operation 和 constant
- 实现 named/indexed property interceptor
- 支持 cross-origin access check 和独立 cross-origin handler
- 记录 getter、setter、method 和 constructor 的 trace 事件
- 操作 JavaScript 对象、函数、Promise 和 TypedArray
- 创建多个相互隔离的 V8 Isolate
- 在 Python 与 JavaScript 之间可靠传播异常
- 通过 Chrome DevTools 调试嵌入式 V8 Context
- 使用 CPython Limited API 构建 `cp312-abi3` 扩展

## 支持范围

| 项目 | 当前状态 |
| --- | --- |
| CPython | 3.12 至 3.15（3.15 已在 Windows 进行部分验证） |
| Python ABI | `cp312-abi3` |
| 默认 V8 | `15.4.68`（Windows 构建及相关回归已验证） |
| Temporal | 随 V8 构建启用（`v8_enable_temporal_support=true`） |
| Windows | 主要开发和完整测试平台 |
| Linux/macOS | CI 分别构建 Linux x64 和 macOS arm64 wheel；具体结果以工作流运行记录为准 |
| 多 Isolate | 支持，JS 句柄不能跨 Isolate 使用 |
| subinterpreter | 不支持 |
| free-threaded CPython | 不支持 |

V8 的 C++ API 会随版本变化。绑定要求 V8 15.4 或更新版本；构建器可以下载正式数字 tag，
但更换版本后需要重新验证构建和绑定代码。若存在本地
`.pyv8-version`，它会覆盖 `V8_VERSION` 中的默认值。

Windows 上启用 Temporal 时，扩展用 V8 自带的 `clang-cl`/`lld-link` 构建，并按
`mksnapshot.ninja` 链接 `temporal_capi`/`temporal_rs` 的 Rust rlib；MSVC `link.exe`
无法读取这些 LLVM bitcode。

## 快速开始

```python
from pyv8c import pyv8


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
from pyv8c import pyv8


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

IDL 映射统一使用 C++ 定义的 `pyv8.IdlSpec`，完整字段与 realm 装配契约见 [idl-spec.md](../docs/pyv8/idl-spec.md)。

## 安装

仓库的 `Build wheels` GitHub Actions 工作流分别构建 Windows x64、Linux x64 和 macOS arm64 wheel，并在 CPython 3.12–3.15 上测试各平台的 ABI3 wheel；各次运行结果以 Actions 日志为准。wheel 包含原生引擎和框架，引擎的公开入口为 `from pyv8c import pyv8`。

维护者可在 GitHub Actions 手动运行该工作流，并从运行记录下载各平台 artifact。

仓库不跟踪 `_pyv8` 二进制和 `icudtl.dat`；后者仍随每个 wheel 提供，并须与构建所用 V8 匹配。Windows 工作流缓存 V8 头文件、构建输出及配套 LLVM 工具链；首次运行、缓存失效或缓存被 GitHub 清理后仍需下载并编译 V8。缓存键包含 V8 版本、构建脚本、运行器镜像、CPU 架构和 Windows SDK 版本，改变这些输入会重新构建。缓存受 GitHub 的存储配额及保留策略限制，保存失败不会让已经完成的 wheel 构建失败。

### 安装 wheel

下载与目标操作系统和 CPU 架构匹配的 wheel，然后执行：

```bash
python -m pip install path/to/pyv8c-0.1.0-cp312-abi3-win_amd64.whl
```

`cp312-abi3` 扩展可供启用 GIL 的 CPython 3.12 至 3.15 使用，但 Windows、Linux、macOS
以及 x64、arm64 仍然需要各自对应的 wheel。

### 从源码构建

以下命令从仓库根目录执行。构建原生扩展和 wheel 需要 **CPython 3.12**；生成的 `cp312-abi3` wheel 可在同平台、同架构的启用 GIL 的 CPython 3.12–3.15 上运行。先使用 Python 3.12 安装构建工具：

```bash
python pyv8/bootstrap.py
python -m pip install --upgrade pip setuptools wheel build tox
```

前置检查验证 Python 和 Git，不下载任何文件；各平台的编译器、SDK 和 V8 依赖仍由后续构建步骤检查。构建完整发行包时，根目录构建流程会单独校验随包 IDL 的版本。常用配置示例见 [build.env.example](build.env.example)。

选择并准备 V8：

```bash
# 查看可选版本
python setup.py list_v8_versions --limit=15

# 交互选择、下载并编译 V8
python setup.py build_v8 --select

# 编译扩展到 pyv8c.pyv8 包目录
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
python312 -m build --wheel
```

Windows 需要安装 Visual Studio C++ 构建工具和 Windows SDK。自动发现不符合本机环境时，可以按实际安装位置设置路径：

```powershell
$env:PYV8_VS_HOME = "<Visual Studio 安装目录>"
$env:PYV8_WINDOWS_SDK = "<Windows SDK 安装目录>"
$env:DEPOT_TOOLS_WIN_TOOLCHAIN = "0"
python setup.py build_v8
python setup.py build_ext --inplace
```

详细的编译器要求、环境变量、自定义 V8 路径和 ICU 数据约束见
[完整使用指南](../docs/pyv8/user-guide.md#安装与构建)。

## Chrome DevTools

```python
from pyv8c import pyv8


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
try:
    with pyv8.start_devtools(context, options=pyv8.DevToolsOptions(port=9005)) as handle:
        print(handle.url)
        handle.run(run_script)
finally:
    context.close()
```

将打印的 `devtools://` URL 粘贴到 Chrome 地址栏。`start_devtools()` 只启动服务；
`handle.run()` 在调用线程处理调试握手，然后执行回调。默认在下一段宿主脚本的首条语句暂停一次，
不需要插入 `debugger;`。使用默认 `127.0.0.1`，不要向不可信网络暴露无认证的调试端口。
配置、线程约束和旧接口迁移见 [DevTools 架构与迁移](../docs/pyv8/devtools.md)。

## 文档

- [完整使用指南](../docs/pyv8/user-guide.md)
- [Context 与脚本执行](../docs/pyv8/user-guide.md#context-与脚本执行)
- [Python/JavaScript 类型转换](../docs/pyv8/user-guide.md#类型转换)
- [Chromium-like 类与原型](../docs/pyv8/user-guide.md#chromium-like-类与原型)
- [Property interceptor](../docs/pyv8/user-guide.md#property-interceptor)
- [Cross-origin 与 trace](../docs/pyv8/user-guide.md#cross-origin-与-trace)
- [多 Isolate 和生命周期](../docs/pyv8/user-guide.md#多-isolate-与生命周期)
- [DevTools 调试](../docs/pyv8/user-guide.md#devtools-调试)
- [常见问题](../docs/pyv8/user-guide.md#常见问题)

## 测试

项目使用 tox 4 构建一次 `cp312-abi3` wheel，并在 Python 3.12 至 3.15 的隔离环境中
安装和测试同一个 wheel：

```bash
python314 -m tox
```

只测试指定版本：

```bash
python314 -m tox -e py312
python314 -m tox -e py314
python314 -m tox -e py315
```

Windows 下已使用启用 GIL 的 CPython 3.15.0 验证原生 ABI3 wheel 和原生测试，以及
浏览器 WebAssembly 测试。其他平台仍需分别验证。
free-threaded CPython 3.15 不在支持范围内。

快速原地编译并运行 pytest：

```bash
python setup.py build_ext --inplace --force
python -m pytest -q -n auto
```

（`pytest-xdist` 在 dev extras 里。本套件很小，并行与串行无实质差别——403 passed 实测 5.42 s vs 5.55 s——
按仓库统一习惯带上 `-n auto` 即可。）

`tests/run_tests.py` 也统一使用 pytest，避免 unittest discovery 漏掉函数式测试。

## 重要限制

- `from pyv8c import pyv8` 不启动 V8：进程级 V8 Runtime 和默认 Isolate 在首次使用时才创建，启动内容
  由仅在首次使用前有效的 `pyv8.configure(...)` 决定，`pyv8.is_initialized()` 可查询状态。
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
- GitHub：https://github.com/ConlinH/pyv8c
