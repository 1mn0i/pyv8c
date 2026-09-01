# pyv8 使用指南

本文档对应 pyv8 2.1.2。默认验证环境为 CPython 3.10 至 3.14、V8 15.2.20.1。

pyv8 的主要目标不是替代浏览器，而是提供一组足够底层的 Python/V8 桥接 API，
用于构造接近 Chromium 行为的原型对象、构造器、属性描述符和 property interceptor。

## 目录

- [安装与构建](#安装与构建)
- [Context 与脚本执行](#context-与脚本执行)
- [类型转换](#类型转换)
- [JavaScript 对象与函数](#javascript-对象与函数)
- [Chromium-like 类与原型](#chromium-like-类与原型)
- [Property interceptor](#property-interceptor)
- [Cross-origin 与 hook](#cross-origin-与-hook)
- [TypedArray 与 Promise](#typedarray-与-promise)
- [异常与超时](#异常与超时)
- [多 Isolate 与生命周期](#多-isolate-与生命周期)
- [DevTools 调试](#devtools-调试)
- [API 速查](#api-速查)
- [测试](#测试)
- [常见问题](#常见问题)

## 安装与构建

### Python 和平台要求

- CPython 3.10 至 3.14
- 使用 GIL 的标准 CPython 构建
- 支持 C++20 的编译器
- 与目标平台和 CPU 架构匹配的 V8 静态库
- 与该 V8 构建匹配的 `icudtl.dat`

Windows 是当前主要开发和完整测试平台。Windows 源码构建建议使用 Visual Studio 2022
和 Windows 10/11 SDK。Linux/macOS 构建分支已经包含在构建脚本中，但仍应在目标平台
验证编译器、V8 产物和 wheel。

### 安装预编译 wheel

```bash
python -m pip install path/to/pyv8-2.1.2-cp310-abi3-<platform>.whl
```

`cp310-abi3` 表示同一 wheel 可以被 CPython 3.10 至 3.14 加载。ABI3 不会让二进制跨
操作系统或 CPU 架构使用，例如 Windows 的 `.pyd` 不能在 Linux 中加载。

### 准备构建工具

```bash
python -m pip install --upgrade pip setuptools wheel build tox
```

Windows 下还需要：

- Visual Studio 的“使用 C++ 的桌面开发”工作负载
- Windows 10/11 SDK
- Git
- 足够的磁盘空间和内存用于 V8 编译

### 选择并编译 V8

查看每个发布分支最新的正式 tag：

```bash
python setup.py list_v8_versions --limit=15
```

交互选择版本：

```bash
python setup.py build_v8 --select
```

明确指定版本：

```bash
python setup.py build_v8 --v8-version=15.2.20.1 --v8-jobs=8
```

选择结果会写入本地 `.pyv8-version`。后续 `build_ext` 和 wheel 构建会复用该版本；
删除 `.pyv8-version` 后恢复使用仓库中的 `V8_VERSION`。

相同 V8 tag、CPU 和 GN 参数会复用 `.pyv8-build` 缓存。需要完整重建时使用：

```bash
python setup.py build_v8 --v8-version=15.2.20.1 --force
```

### 网络和代理

显式指定代理：

```bash
python setup.py build_v8 --select --proxy=http://127.0.0.1:7890
```

忽略进程环境和 Git 全局代理，强制直连：

```bash
python setup.py build_v8 --select --no-proxy
python setup.py list_v8_versions --no-proxy
```

也可以使用环境变量：

```powershell
$env:PYV8_PROXY = "http://127.0.0.1:7890"
python setup.py build_v8 --select
```

```powershell
$env:PYV8_NO_PROXY = "1"
python setup.py build_v8 --select
```

### 编译 Python 扩展

原地编译，生成 `pyv8/_pyv8.pyd` 或 `pyv8/_pyv8.abi3.so`：

```bash
python setup.py build_ext --inplace
```

V8 或 C++ 源码变化后强制重新编译：

```bash
python setup.py build_ext --inplace --force
```

构建可发布 wheel：

```bash
python310 -m build --wheel
```

使用 Python 3.10 构建可以得到 `cp310-abi3` wheel，供后续 Python 3.10 至 3.14 使用。

### 使用已有 V8 产物

已有自行编译的 V8 时，不需要重新下载：

```powershell
$env:PYV8_V8_VERSION = "15.2.20.1"
$env:PYV8_V8_HOME = "D:\v8-workspace\v8"
$env:PYV8_V8_OUT = "D:\v8-workspace\v8\out\x64.release"
$env:PYV8_ICU_DATA = "D:\v8-workspace\v8\out\x64.release\icudtl.dat"
python310 -m build --wheel
```

Linux/macOS 使用相同变量语义：

```bash
PYV8_V8_VERSION=15.2.20.1 \
PYV8_V8_HOME=/opt/v8 \
PYV8_V8_OUT=/opt/v8/out/x64.release \
PYV8_ICU_DATA=/opt/v8/out/x64.release/icudtl.dat \
python -m build --wheel
```

`PYV8_V8_OUT` 中必须存在：

- Windows：`obj/v8_monolith.lib`
- Linux/macOS：`obj/libv8_monolith.a`
- `icudtl.dat`

### 构建环境变量

| 变量 | 作用 |
| --- | --- |
| `PYV8_V8_VERSION` | V8 正式数字 tag |
| `PYV8_BUILD_V8` | 设置为 `1` 时下载并构建 V8 |
| `PYV8_FORCE_V8_BUILD` | 设置为 `1` 时忽略 V8 构建缓存 |
| `PYV8_V8_JOBS` | Ninja 并行任务数 |
| `PYV8_BUILD_ROOT` | depot_tools 和版本缓存根目录 |
| `PYV8_DEPOT_TOOLS` | 使用已有 depot_tools 目录 |
| `PYV8_PROXY` | Git、depot_tools 和 CIPD 使用的代理 |
| `PYV8_NO_PROXY` | 设置为 `1` 时强制直连 |
| `PYV8_VS_HOME` | Windows Visual Studio 安装目录 |
| `PYV8_VS_VERSION` | Visual Studio 年份，默认 `2022` |
| `PYV8_WINDOWS_SDK` | Windows SDK 根目录 |
| `PYV8_V8_HOME` | 已有 V8 源码目录 |
| `PYV8_V8_OUT` | 已有 V8 输出目录 |
| `PYV8_ICU_DATA` | 与 V8 静态库匹配的 ICU 数据文件 |
| `PYV8_GN_ARGS` | 追加自定义 GN 参数 |
| `PYV8_TARGET_CPU` | 目标 CPU，目前支持 `x64`、`arm64` |

#### Windows 2026 构建示例

项目的 V8 构建器会下载 `V8_VERSION` 指定的源码 tag，并使用 depot_tools
同步依赖、生成 GN 配置和执行 Ninja。Windows 上可以显式固定 Visual Studio
和 Windows SDK，避免机器上存在多个工具链时被自动探测到错误版本：

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

`PYV8_VS_HOME` 必须指向 Visual Studio 安装根目录（包含 `VC`、`Common7`），；`PYV8_WINDOWS_SDK` 必须指向包含 `Include`、`Lib`
的 Windows SDK 根目录。

如果网络受限，可设置 `PYV8_PROXY` 或 `PYV8_NO_PROXY=1`。构建完成后，V8
产物位于 `.pyv8-build\versions\15.2.20.1\v8\out\pyv8-x64.release`，其中
`obj\v8_monolith.lib` 和同目录的 `icudtl.dat` 必须来自同一次构建。

### ICU 数据约束

构建时会计算 `icudtl.dat` 的 SHA-256，并把结果编译进 `_pyv8`。导入 pyv8 时会在
V8 初始化前重新计算文件哈希。如果文件不是构建时使用的那一份，导入会直接抛出
`RuntimeError`，避免不匹配的 ICU 数据在 `Intl` 初始化中造成 native crash 或 OOM。

哈希相同只表示文件内容没有变化。仍应保证 `v8_monolith` 和 `icudtl.dat` 来自同一个
V8/ICU 构建，不要单独替换其中一个文件。

## Context 与脚本执行

### 创建 Context

```python
import pyv8


class Window:
    __v8_global_this__ = True


context = pyv8.Context(
    Window,
    hook=False,
    ctx_type="Top",
    timeout=1.0,
)
```

构造参数：

| 参数 | 说明 |
| --- | --- |
| `global_this` | Python 类或实例，用作 JavaScript 全局对象的底层实现 |
| `hook` | 是否安装补环境 hook/interceptor |
| `ctx_type` | 传给 hook 的上下文标签，例如 `Top`、`Iframe` |
| `timeout` | 默认脚本超时秒数，`0` 表示不限制 |
| `isolate` | 指定自定义 `pyv8.Isolate`，省略时使用默认 Isolate |

传入 Python 类时，Context 会无参数实例化该类，因此类构造函数不能要求必填参数。
需要提前准备状态时传入实例：

```python
class Window:
    def __init__(self, origin):
        self.origin = origin


window = Window("https://example.com")
context = pyv8.Context(window)
```

### 执行 JavaScript

```python
result = context.exec_js("20 + 22")
assert result == 42
```

指定脚本文件名有助于异常堆栈和 DevTools 定位：

```python
result = context.exec_js(
    "(new Error('trace')).stack",
    filename="https://example.com/assets/app.js",
)
```

单次执行可以覆盖 Context 默认超时：

```python
context.exec_js("runTask()", timeout=0.5)
```

### 预编译 Script

```python
script = pyv8.Script(context, "40 + 2", "answer.js")
result = context.exec_js(script)
assert result == 42
```

`Script` 属于创建它的 Isolate，不能交给另一个 Isolate 的 Context 执行。

### 访问 globalThis

`context.glob` 返回全局对象的 `JSObject` wrapper：

```python
global_object = context.glob
global_object.answer = 42
assert context.exec_js("globalThis.answer") == 42
```

Context 未定义的 Python 属性会自动转发到 JavaScript 全局对象：

```python
context.name = "pyv8"
assert context.exec_js("name") == "pyv8"

context.exec_js("globalThis.version = 2")
assert context.version == 2
```

读取不存在的 Context 属性会抛出 `AttributeError`，而 JavaScript 中仍然得到
`undefined`。

### 暴露 Python 值

位置参数使用对象的 `__name__`：

```python
def add(left, right):
    return left + right


context.expose(add)
assert context.exec_js("add(20, 22)") == 42
```

关键字参数显式指定 JavaScript 名称：

```python
class State:
    pass


state = State()
state.value = 42
context.expose(browserState=state, payload={"ready": True})
```

一次可以暴露多个值：

```python
context.expose(add, State, state=state, config={"debug": False})
```

暴露 Python 模块的全部非下划线成员：

```python
import math

context.expose_module(math)
assert context.exec_js("sqrt(81)") == 9
```

### 当前执行上下文

在由 JavaScript 调用的 Python callback 中，可以查询当前 Context：

```python
def inspect_context():
    assert pyv8.current_context() is context
    assert pyv8.incumbent_context() is context
    return 42


context.expose(inspect_context)
context.exec_js("inspect_context()")
```

使用自定义 Isolate 时也可以显式传入 Isolate 查询。

## 类型转换

### Python 到 JavaScript

| Python | JavaScript |
| --- | --- |
| `None` | `undefined` |
| `pyv8.Null` | `null` |
| `pyv8.Undefined` | `undefined` |
| `bool` | `boolean` |
| `int`、`float` | `number` |
| `str` | `string` |
| `bytes` | `ArrayBuffer` |
| `list`、`tuple` | `Array` |
| `dict` | 普通 `Object` |
| Python function/method | JavaScript function |
| Python class | JavaScript constructor/interface object |
| Python instance | 带内部 Python owner 的 JavaScript object |
| `JSObject`、`JSValue` | 原始 V8 值，必须属于当前 Isolate |

Python `None` 不表示 JavaScript `null`：

```python
context.expose(nothing=None, explicit_null=pyv8.Null)
assert context.exec_js("nothing === undefined") is True
assert context.exec_js("explicit_null === null") is True
```

Python 容器的循环引用和共享引用会被保留：

```python
root = []
child = {"root": root}
root.extend((child, child))
context.expose(root=root)

assert context.exec_js("root[0].root === root") is True
assert context.exec_js("root[0] === root[1]") is True
```

JavaScript `number` 无法无损表示任意大的 Python 整数。需要精确大整数时应在 JavaScript
侧使用 `BigInt`，或通过字符串传输。

### JavaScript 到 Python

| JavaScript | Python |
| --- | --- |
| `undefined` | `pyv8.Undefined` |
| `null` | `pyv8.Null` |
| `boolean` | `bool` |
| 32 位整数 | `int` |
| 其他 `number` | `float` |
| `string` | `str` |
| `Array` | `list` |
| 普通 object literal | `dict` |
| 自定义对象、Date、RegExp | `JSObject` |
| function | `JSFunction` |
| Promise | `JSPromise` |
| TypedArray | `JSTypeArray` |
| BigInt | `JSValue` |

JavaScript 数组和普通对象中的循环、共享引用同样会保留：

```python
value = context.exec_js(
    "const root = []; const child = {root}; root.push(child, child); root"
)

assert value[0]["root"] is value
assert value[0] is value[1]
```

稀疏数组中的 hole 会转换为 `pyv8.Undefined`。

## JavaScript 对象与函数

### JSObject

```python
person = context.exec_js(
    """
    new (class Person {
        constructor(name) {
            this.name = name;
        }
        greet() {
            return `Hello, ${this.name}`;
        }
    })("Alice")
    """
)

assert isinstance(person, pyv8.JSObject)
assert person.name == "Alice"
assert person.greet() == "Hello, Alice"

person.name = "Bob"
person["age"] = 18
del person["age"]
```

常用操作：

- `obj.name`、`obj[name]`：读取属性
- `obj.name = value`、`obj[name] = value`：设置属性
- `del obj.name`、`del obj[name]`：删除属性
- `dir(obj)`：枚举属性
- `obj1 == obj2`：比较 V8 对象身份

JSObject 会保留创建它的 Context。释放 Context 前，应先释放不再使用的 wrapper。

### JSFunction 和构造调用

```python
multiply = context.exec_js("(left, right) => left * right")
assert isinstance(multiply, pyv8.JSFunction)
assert multiply(6, 7) == 42
```

调用 JavaScript 构造函数：

```python
Uint8Array = context.exec_js("Uint8Array")
typed_array = pyv8.new(Uint8Array, [1, 2, 3])
```

`pyv8.new()` 的构造器和参数必须能转换到同一个 Isolate。

### Python 对象对应的 JS wrapper

JavaScript 把 Python owner 对象返回给 Python 时，默认恢复为原 Python 对象。需要访问该
对象在 JavaScript 一侧新增的属性时，可以取得 wrapper：

```python
class Payload:
    pass


context.expose(Payload)
payload = context.exec_js("const value = new Payload(); value.extra = 42; value")

wrapper = context.js_obj(payload)
assert wrapper.extra == 42
```

同一 Python 对象在同一个 Context 中保持相同 JavaScript 身份；在不同 Context 或 Isolate
中会创建相互隔离的 wrapper。

## Chromium-like 类与原型

### 构造器基础

```python
import pyv8


class DOMPoint:
    __v8_constructor__ = pyv8.FlagConstructor.kNew
    __v8_has_constructor__ = 1
    __v8_length__ = 2

    def __init__(self, x=0, y=0, win=None):
        self.x = x
        self.y = y


context.expose(DOMPoint)
point = context.exec_js("new DOMPoint(10, 20)")
```

Python 构造器会额外收到 `win=<当前 Context 的 Python global_this>` 关键字参数。
用于暴露为 JavaScript 构造器的类应接受 `win=None` 或 `**kwargs`。

### 类级元数据

| 属性 | 作用 |
| --- | --- |
| `__v8_length__` | JavaScript 构造器的 `length` |
| `__v8_constructor__` | 是否允许 `new Interface()` 和直接调用 |
| `__v8_has_constructor__` | 是否在 prototype 上安装 `constructor` |
| `__v8_global_this__` | 标记全局 Window-like 类并注册 Faker 支持 |
| `__v8_skip_inherit__` | 子类建立原型链时跳过该中间基类 |
| `__v8_undetectable__` | 使用 V8 undetectable object 行为 |
| `__v8_unconstructable__` | 构造时抛出“不是构造器”错误 |
| `__v8_array__` | 安装 Array 原型相关 intrinsic 方法 |
| `__v8_immutable__` | 将实例或 prototype 的原型标记为 immutable |
| `__call__` | 让实例在 JavaScript 中可以作为函数调用 |

`FlagConstructor`：

| 值 | 行为 |
| --- | --- |
| `kDontAll` | 禁止构造和直接调用 |
| `kNew` | 只允许 `new Interface()` |
| `kCall` | 允许函数调用；当前实现也接受 `new` |
| `kAll` | 允许构造和函数调用 |

### Attribute 配置

`__v8_attribute__` 必须是 tuple，每个 attribute 也是至少 10 项的 tuple：

```python
class NavigatorLike:
    __v8_attribute__ = (
        (
            "userAgent",                          # JavaScript 属性名
            "get_user_agent",                    # Python getter 名
            None,                                 # Python setter 名或 None
            pyv8.CallbackType.kPy,                # callback 类型
            pyv8.FlagLocation.kPrototype,         # 安装位置
            pyv8.V8Attribute.DontEnum,            # descriptor flags
            pyv8.FlagReceiverCheck.kCheck,        # receiver 检查
            pyv8.FlagCrossOriginCheck.kCheck,     # getter cross-origin
            pyv8.FlagCrossOriginCheck.kCheck,     # setter cross-origin
            pyv8.SideEffectType.kHasNoSideEffect, # side effect
        ),
    )

    def get_user_agent(self):
        return "pyv8-browser/1.0"
```

字段顺序：

| 序号 | 字段 |
| --- | --- |
| 1 | JavaScript 属性名 |
| 2 | getter callback 名 |
| 3 | setter callback 名或 `None` |
| 4 | `CallbackType` |
| 5 | `FlagLocation` |
| 6 | `V8Attribute` 位标志 |
| 7 | `FlagReceiverCheck` |
| 8 | getter `FlagCrossOriginCheck` |
| 9 | setter `FlagCrossOriginCheck` |
| 10 | `SideEffectType` |

### Operation 配置

`__v8_method__` 必须是 tuple，每个 operation 是至少 9 项的 tuple：

```python
class Calculator:
    __v8_method__ = (
        (
            "add",
            "fn_add",
            2,
            pyv8.CallbackType.kPy,
            pyv8.FlagLocation.kPrototype,
            pyv8.V8Attribute.kNone,
            pyv8.FlagReceiverCheck.kCheck,
            pyv8.FlagCrossOriginCheck.kCheck,
            pyv8.SideEffectType.kHasNoSideEffect,
        ),
    )

    def fn_add(self, left, right):
        return left + right
```

字段顺序：

| 序号 | 字段 |
| --- | --- |
| 1 | JavaScript operation 名 |
| 2 | Python callback 名 |
| 3 | JavaScript function `length`，范围 0 至 255 |
| 4 | `CallbackType` |
| 5 | `FlagLocation` |
| 6 | `V8Attribute` 位标志 |
| 7 | `FlagReceiverCheck` |
| 8 | `FlagCrossOriginCheck` |
| 9 | `SideEffectType` |

`CallbackType.kPy` 表示调用 Python 类方法。`CallbackType.kJs` 用于调用
`globalThis.faker` 中的 JavaScript patch callback，属于补环境高级接口。

### 安装位置和 descriptor flags

`FlagLocation`：

| 值 | 安装位置 |
| --- | --- |
| `kInstance` | 每个实例的 own property |
| `kPrototype` | `Interface.prototype` |
| `kInterface` | 构造器/interface object 本身 |

`V8Attribute` 可以按位组合：

| 值 | JavaScript descriptor 含义 |
| --- | --- |
| `kNone` | 默认属性 |
| `ReadOnly` | `writable: false` |
| `DontEnum` | `enumerable: false` |
| `DontDelete` | `configurable: false` |

例如：

```python
flags = (
    pyv8.V8Attribute.ReadOnly
    | pyv8.V8Attribute.DontEnum
    | pyv8.V8Attribute.DontDelete
)
```

### 常量

```python
class ReadyState:
    __v8_constants_value__ = (
        ("UNSENT", 0),
        ("OPENED", 1),
        ("DONE", 4),
    )
```

常量会安装在 interface object 和 prototype 上，并使用只读、不可删除的 descriptor。

`__v8_constants_callback__` 用于从 `faker` JavaScript callback 延迟获取常量，属于高级
补环境接口。

### 原型继承

Python 类继承会映射为 JavaScript 构造器和 prototype 继承：

```python
class EventTarget:
    pass


class Node(EventTarget):
    pass


class Element(Node):
    pass


context.expose(Element)
assert context.exec_js("Element.__proto__.name") == "Node"
```

大量生成浏览器接口时，模板会按 Context 和 Isolate 缓存。修改已经安装的 Python 类元数据
不会自动重建旧模板，应创建新 Context 或显式使用新的类对象。

## Property interceptor

### Named property handler

定义下列方法中的任意组合即可安装 named interceptor：

```python
class NamedCollection:
    def __init__(self):
        self.values = {"first": 1}

    def __v8_name_get__(self, name):
        return self.values.get(name)

    def __v8_name_set__(self, name, value):
        self.values[name] = value
        return True

    def __v8_name_query__(self, name):
        if name not in self.values:
            return None
        return pyv8.V8Attribute.kNone

    def __v8_name_del__(self, name):
        return self.values.pop(name, None) is not None

    def __v8_name_enum__(self):
        return list(self.values)
```

返回约定：

| Handler | 返回值 |
| --- | --- |
| `__v8_name_get__` | 属性值；`None` 表示未拦截 |
| `__v8_name_set__` | 非 `None` 表示已处理 |
| `__v8_name_query__` | `V8Attribute` 整数；`None` 表示不存在 |
| `__v8_name_del__` | `bool` 删除结果 |
| `__v8_name_enum__` | 名称序列；`None` 表示不提供枚举结果 |

### Indexed property handler

```python
class IndexedCollection:
    def __init__(self):
        self.values = [10, 20]

    def __v8_index_get__(self, index):
        if 0 <= index < len(self.values):
            return self.values[index]
        return None

    def __v8_index_set__(self, index, value):
        while len(self.values) <= index:
            self.values.append(None)
        self.values[index] = value
        return True

    def __v8_index_query__(self, index):
        if 0 <= index < len(self.values):
            return pyv8.V8Attribute.kNone
        return None

    def __v8_index_del__(self, index):
        if 0 <= index < len(self.values):
            self.values[index] = None
            return True
        return False

    def __v8_index_enum__(self):
        return len(self.values)
```

`__v8_index_enum__` 返回要枚举的连续索引数量，V8 将生成 `0` 到 `length - 1`。

Handler 抛出的 Python 异常会传播到 JavaScript；若未被 JavaScript 捕获，会恢复到 Python
调用方。一次 handler 失败不应破坏后续 Context 执行。

Setter/deleter handler 还支持内部失败标记 `__v8_interceptor_failure__`。当异常实例或其
类型将该属性设为真时，原生桥按照 `PropertyCallbackInfo::ShouldThrowOnError()` 处理：

- 非严格模式下清除异常，将操作视为已拦截的失败；
- 严格模式下将异常继续抛给 JavaScript；
- 该标记仅用于 named/indexed setter 和 deleter，不应标记普通业务异常。

```python
class LegacyPropertyError(Exception):
    __v8_error__ = True
    __v8_error_name__ = "TypeError"
    __v8_interceptor_failure__ = True


class LegacyCollection:
    def __v8_index_set__(self, index, value):
        raise LegacyPropertyError(
            f"Failed to set an indexed property [{index}] on "
            "'LegacyCollection': Indexed property setter is not supported."
        )
```

此时 `collection[0] = value` 在非严格模式静默失败，在严格模式产生 JavaScript
`TypeError`。单独使用 `__v8_error_name__` 不会启用这种严格模式分流。

## Cross-origin 与 hook

### Cross-origin access check

Window-like 对象可以定义访问检查：

```python
class Window:
    def __v8_cross_origin_check__(self, accessor):
        # self 是被访问对象，accessor 是发起访问的 Python global object。
        return self.origin == accessor.origin
```

返回 `True` 允许访问，返回 `False` 拒绝访问。

需要在拒绝普通访问时提供受限属性，可以实现独立 cross-origin handler：

- `__v8_cross_origin_name_get__`
- `__v8_cross_origin_name_set__`
- `__v8_cross_origin_name_query__`
- `__v8_cross_origin_name_del__`
- `__v8_cross_origin_name_enum__`
- `__v8_cross_origin_index_get__`
- `__v8_cross_origin_index_set__`
- `__v8_cross_origin_index_query__`
- `__v8_cross_origin_index_del__`
- `__v8_cross_origin_index_enum__`

这些方法的参数和返回约定与普通 named/indexed handler 相同。

Attribute 和 operation 配置中的 `FlagCrossOriginCheck` 决定对应 function template 是否接受
跨 origin receiver：

- `kCheck`：执行 receiver/cross-origin 检查
- `kDoNotCheck`：允许任意 receiver

### 补环境 hook

创建 Context 时启用 hook：

```python
context = pyv8.Context(Window(), hook=True, ctx_type="Top")
context.exec_js("faker.hook = true")
```

对象可以实现四类 hook：

```python
class HookTarget:
    def __v8_get_hook__(self, name, value, error, ctx_type):
        print("get", ctx_type, name, value, error)

    def __v8_set_hook__(self, name, value, error, ctx_type):
        print("set", ctx_type, name, value, error)

    def __v8_method_hook__(
        self, name, arguments, value, has_error, ctx_type
    ):
        print("method", ctx_type, name, list(arguments), value, has_error)

    @staticmethod
    def __v8_construct_hook__(
        name, arguments, value, has_error, is_construct_call, ctx_type
    ):
        print(
            "construct",
            ctx_type,
            name,
            list(arguments),
            value,
            has_error,
            is_construct_call,
        )
```

`pyv8.tools.ConstructorConfig` 可以写入常用构造器元数据，并自动安装默认打印 hook：

```python
from pyv8.tools import ConstructorConfig


@ConstructorConfig(
    constructor=pyv8.FlagConstructor.kNew,
    length=0,
    immutable=pyv8.FlagImmutable.instance | pyv8.FlagImmutable.prototype,
)
class Window:
    __v8_global_this__ = True
```

Hook 自身抛出的异常会传播给调用方，因此生产日志 hook 不应吞掉关键状态，也不应执行
容易递归触发同一 hook 的 JavaScript。

## TypedArray 与 Promise

### TypedArray

JavaScript TypedArray 会转换为 `pyv8.JSTypeArray`：

```python
array = context.exec_js("new Uint8Array([10, 20, 30])")
assert isinstance(array, pyv8.JSTypeArray)
assert array.length == 3
assert array[1] == 20

array[1] = 99
```

从 Python 创建 TypedArray：

```python
array = pyv8.JSTypeArray(
    [1, 2, 3],
    pyv8.ArrayType.Uint8Array,
)
context.expose(array=array)
assert context.exec_js("array instanceof Uint8Array") is True
```

可用 `ArrayType`：

- `Uint32Array`
- `Uint16Array`
- `Uint8Array`
- `Int32Array`
- `Int16Array`
- `Int8Array`
- `Float64Array`
- `Float32Array`
- `Uint8ClampedArray`

Python `bytes` 会转换为 `ArrayBuffer`：

```python
context.expose(payload=b"\x01\x02\xff")
assert context.exec_js("payload.byteLength") == 3
```

### Promise

```python
promise = context.exec_js("Promise.resolve('success')")
assert isinstance(promise, pyv8.JSPromise)

promise.then(lambda value: print(value))
```

`JSPromise` 当前是 JavaScript Promise wrapper，不是 Python `asyncio.Future`，不能直接
使用 `await promise`。需要 asyncio 集成时，应在 Python callback 中把 resolve/reject
结果写入事件循环所属的 Future。

## 异常与超时

### JavaScript 异常

语法错误、运行时错误、getter/proxy 错误会转换为 `pyv8.JSException`：

```python
try:
    context.exec_js("throw new TypeError('bad input')")
except pyv8.JSException as error:
    print(error)
```

### Python 异常进入 JavaScript

给异常类添加 `__v8_error__ = True`：

```python
class V8TypeError(TypeError):
    __v8_error__ = True


def fail():
    raise V8TypeError("invalid payload")


context.expose(fail)
result = context.exec_js(
    """
    try {
        fail();
    } catch (error) {
        [error.name, error.message, typeof error.stack];
    }
    """
)

assert result == ["TypeError", "invalid payload", "string"]
```

支持按 Python 异常类名映射到 `TypeError`、`RangeError`、`ReferenceError`、`SyntaxError`
和普通 `Error`。Python 异常经过 JavaScript catch/rethrow 后，pyv8 会尽量恢复原异常类型。

当 Python 类名不适合使用 `V8TypeError` 等约定名称时，可以通过字符串属性
`__v8_error_name__` 显式指定 JavaScript Error 类型：

```python
class ValidationFailure(Exception):
    __v8_error__ = True
    __v8_error_name__ = "TypeError"
```

`__v8_error__` 将异常标记为预期的 JavaScript-facing 错误，并阻止原生桥把它作为意外
Python 异常打印 traceback；`__v8_error_name__` 只覆盖 JavaScript Error 构造器的选择，
不改变异常的传播时机。目前支持的映射名称为 `Error`、`TypeError`、`RangeError`、
`ReferenceError` 和 `SyntaxError`。

### 执行超时

```python
context.timeout = 0.1

try:
    context.exec_js("while (true) {}")
except pyv8.JavaScriptTerminated:
    pass

# 超时终止状态会被清理，同一 Context 可以继续使用。
context.timeout = 0
assert context.exec_js("6 * 7") == 42
```

也可以只限制一次调用：

```python
context.exec_js("runTask()", timeout=0.25)
```

超时通过 V8 TerminateExecution 实现，不是操作系统级内存或进程限制。V8 native OOM、
断言失败和 fatal error 仍可能直接终止进程。

## 多 Isolate 与生命周期

### 创建独立 Isolate

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

del context_a, context_b
gc.collect()
isolate_a.close()
isolate_b.close()
```

每个 Isolate 有独立的：

- JavaScript heap
- Context 和全局对象
- FunctionTemplate 缓存
- V8 字符串缓存
- Python owner 集合

### 句柄不能跨 Isolate

以下对象不能从 Isolate A 传入 Isolate B：

- `JSObject`
- `JSFunction`
- `JSPromise`
- `JSTypeArray`
- `JSValue`
- `Script`

违反该约束会抛出 `RuntimeError: JavaScript value belongs to another isolate`。
跨 Isolate 传递数据应使用 JSON、普通 Python 容器或自行实现 structured clone。

### 线程

不同线程可以分别进入不同 Isolate，内部通过 V8 Locker 保护。Python callback 仍受 GIL
约束，因此多 Isolate 不等价于 Python 代码完全并行。

不要让同一个 Context 同时被多个线程无序操作。需要线程间调度时，由应用层明确管理
Context 所有权和消息队列。

### 关闭规则

关闭自定义 Isolate 前，必须释放它的所有持久句柄：

```python
del context
gc.collect()
isolate.close()
```

如果仍有活动 Context、Script 或 JS wrapper，`close()` 会抛出包含
`persistent handle` 的 `RuntimeError`，并保持 Isolate 可继续使用。

`pyv8.shutdown_v8()` 用于关闭默认 Isolate 和进程级 V8 Runtime：

```python
del context
gc.collect()
pyv8.shutdown_v8()
```

注意：

- 存在活动自定义 Isolate 时会拒绝 `shutdown_v8()`。
- 存在默认 Isolate 的活动句柄时会拒绝关闭。
- V8 Dispose 后不能在同一进程重新初始化。
- 第二次调用 `shutdown_v8()` 返回 `False`。
- subinterpreter 和 free-threaded CPython 当前被明确拒绝。

普通应用通常不需要主动调用 `shutdown_v8()`；只在需要确定性释放 V8 资源的独立进程
或测试中使用。

## DevTools 调试

### 启动调试服务

```python
import pyv8


class Window:
    __v8_global_this__ = True


def run_target(context):
    context.exec_js(
        """
        function decode(value) {
            const result = value.split('').reverse().join('');
            return result;
        }
        decode('payload');
        """,
        filename="reverse-target.js",
    )


context = pyv8.Context(Window())
pyv8.start_devtools(
    context,
    callback=run_target,
    host="127.0.0.1",
    port=9005,
)
```

程序会打印类似地址：

```text
devtools://devtools/bundled/inspector.html?experiments=true&v8only=true&ws=127.0.0.1:9005
```

将地址粘贴到 Chrome 地址栏。DevTools 发送
`Runtime.runIfWaitingForDebugger` 后，pyv8 才会调用 `callback` 执行目标脚本，因此可以在
脚本运行前启用 Debugger 和设置暂停行为。

### 使用注意事项

- Chrome 130+ 的 DevTools CSP 只允许该页面连接 `ws://127.0.0.1:*`，默认地址不要改成
  `localhost`。
- 一个 Context 同一时间只能挂载一个 Debugger。
- `start_devtools()` 会阻塞调用线程，WebSocket 服务运行在后台线程。
- 断开 WebSocket 或收到关闭信号后，服务会唤醒暂停消息循环并退出。
- `filename` 决定 Sources 面板和堆栈中显示的脚本名称。
- DevTools 可能发送嵌入式 V8 不支持的 `Network.*`、`Target.*` 方法，返回 `-32601`
  不代表调试连接失败。

### 自定义协议桥接

需要完全控制 CDP 时，可以继承 `pyv8.Debugger`，实现：

- `handle(message)`：接收 Inspector 返回的 JSON 消息
- `run_js()`：处理 `Runtime.runIfWaitingForDebugger`
- `run_loop()`：V8 暂停时运行嵌套消息循环
- `quit_loop()`：恢复执行时退出嵌套循环

普通使用者应优先使用 `pyv8.start_devtools()` 和 `pyv8.devtools.DevtoolsDebugger`。

## API 速查

### 模块级 API

| API | 说明 |
| --- | --- |
| `pyv8.Context(...)` | 创建 JavaScript Context |
| `pyv8.Isolate()` | 创建独立 V8 Isolate |
| `pyv8.Script(context, source, name=None)` | 编译可复用脚本 |
| `pyv8.new(constructor, *args)` | 调用 JavaScript 构造函数 |
| `pyv8.current_context(isolate=None)` | 获取当前执行 Context |
| `pyv8.incumbent_context(isolate=None)` | 获取 incumbent Context |
| `pyv8.v8_gc()` | 请求默认 Isolate 执行完整 GC |
| `pyv8.v8_version()` | 返回实际链接的 V8 版本 |
| `pyv8.shutdown_v8()` | 永久关闭进程级 V8 Runtime |
| `pyv8.start_devtools(...)` | 启动 Chrome DevTools bridge |
| `pyv8.Null` | JavaScript `null` 单例 |
| `pyv8.Undefined` | JavaScript `undefined` 单例 |

### Context

| API | 说明 |
| --- | --- |
| `exec_js(program, filename=None, timeout=None)` | 执行字符串或 Script |
| `expose(*objects, **named)` | 暴露 Python 值到 globalThis |
| `expose_module(module)` | 暴露模块全部公开成员 |
| `js_obj(py_object)` | 查找 Python 对象对应的 JS wrapper |
| `gc()` | 请求所属 Isolate 完整 GC |
| `glob` | globalThis 的 JSObject wrapper |
| `timeout` | Context 默认执行超时秒数 |

### Isolate

| API | 说明 |
| --- | --- |
| `context(global_this, ...)` | 在该 Isolate 创建 Context |
| `gc()` | 请求该 Isolate 完整 GC |
| `close()` | 无活动句柄时关闭 Isolate |
| `closed` | Isolate 是否已关闭 |

## 测试

### 完整 Python 版本矩阵

```bash
python314 -m pip install --upgrade tox
python314 -m tox
```

tox 使用 Python 3.10 构建一次 ABI3 wheel，再分别安装到 Python 3.10、3.11、3.12、
3.13、3.14 环境。这样可以避免错误地让所有解释器加载源码目录中的同一个临时文件。

### 指定版本

```bash
python314 -m tox -e py310
python314 -m tox -e py314
```

### 原地快速测试

```bash
python setup.py build_ext --inplace --force
python -m pytest
```

指定测试：

```bash
python -m pytest tests/test_context.py -q
python -m pytest tests/test_chromium_contracts.py -q
python -m pytest tests/test_devtools.py -q
```

## 常见问题

### `DLL load failed: %1 不是有效的 Win32 应用程序`

通常表示 `_pyv8.pyd` 与当前 Python 的架构或 ABI 不匹配：

- 32 位 Python 加载了 64 位 `.pyd`
- Windows ARM64/x64 产物混用
- 文件实际不是 Windows PE 扩展
- 构建过程中复制了其他平台产物

检查：

```powershell
python -c "import struct, sys; print(sys.version); print(struct.calcsize('P') * 8)"
```

重新使用目标平台的 Python 和编译器构建 `_pyv8`。

### `ICU data does not match this _pyv8 build`

当前 `pyv8/icudtl.dat` 与 `_pyv8` 编译时使用的文件不同。不要只复制其中一个文件；
重新构建扩展，或者把同一 wheel 中的 `_pyv8` 和 `icudtl.dat` 一起恢复。

### `Fatal process out of memory` 或 native crash

这是 V8 的进程级 fatal error，不是 Python 异常，loguru、`try/except` 和 `sys.excepthook`
无法记录崩溃后的 Python 日志。

建议：

- 确保 ICU 数据和 V8 静态库匹配
- 给不可信脚本设置 `timeout`
- 将高风险任务放入独立子进程
- 使用 V8 内存参数限制进程资源
- 在进程外采集退出码、stderr 和 crash dump

### DevTools 显示 `WebSocket disconnected`

确认 URL 使用：

```text
ws=127.0.0.1:9005
```

不要使用 `ws=localhost:9005`。同时检查端口占用、防火墙以及程序是否仍阻塞在
`start_devtools()`。

### `specified context already has a debugger attached`

同一个 Context 已存在 Debugger。关闭旧 `DevtoolsDebugger`，或创建新的 Context。

### `cannot close V8 isolate while ... persistent handle(s) are active`

仍有 Context、Script 或 JavaScript wrapper 引用该 Isolate。删除这些对象并执行
`gc.collect()` 后重试，不要强制销毁仍在使用的 Isolate。

### `JavaScript value belongs to another isolate`

正在跨 Isolate 使用 JSObject、JSFunction、Script 等 V8 句柄。将数据转换为普通 Python
容器、JSON 或 structured clone 后再传递。

### Windows 编译时无法覆盖 `_pyv8.pyd`

某个 Python 进程已经加载该文件，Windows 会锁定正在使用的 DLL。退出对应 Python、测试
或 DevTools 进程后，再执行 `build_ext --inplace`。
