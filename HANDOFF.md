# pyv8c 当前架构说明

本文是后续开发的事实基线。它描述当前项目的目标、目录职责、运行时模型、已支持范围和真实限制，不记录每轮开发过程。每次工作的详细过程、修改内容和验证结果记录在 [WORKLOG.md](WORKLOG.md)；实现发生变化时，只更新这里的当前状态和限制，避免保留过时的历史叙述。

文档职责固定为：`HANDOFF.md` 只描述当前最新状态，`WORKLOG.md` 按时间追加每次工作记录。阅读当前架构时先看本文，需要追溯历史时再看工作记录。

## 1. 项目定位

`pyv8c` 是基于 pyv8 的 Chromium-like BOM/DOM 运行时，目标是让 JavaScript 观察到接近浏览器的对象、原型链、事件、异步队列和页面生命周期。

基本用法：

```python
from pyv8c import Browser

browser = Browser()
context = browser.new_context()
page = context.new_page()
page.set_content("<body><div id='app'>hello</div></body>")
print(page.evaluate("document.querySelector('#app').textContent"))
```

项目不追求完整渲染器，不实现布局、绘制或多进程 Chromium。首要目标是稳定、可测试的 DOM/BOM/事件/异步 JavaScript 语义。

## 2. 目录和依赖边界

| 目录 | 职责 |
| --- | --- |
| `pyv8c/` | 浏览器运行时、DOM/BOM、网络对象、事件、页面生命周期 |
| `tests/` | pyv8c 的单元、集成和 native smoke 测试 |
| `tools/` | 接口基线提取和校验工具 |
| `_pyv8/` | pyv8/V8 核心运行时和 C++ binding，是 pyv8c 的核心依赖 |
| `_pyv8env/` | 旧项目和 renderer binding 快照参考，默认不修改 |

`_pyv8/` 允许为 pyv8c 增加底层能力，例如 Context、异常桥接、线程安全、生命周期和构建支持。修改 `_pyv8/` 时，应在其独立 Git 仓库提交，并验证 `_pyv8` 自身和 pyv8c；不得提交 `.pyv8-build/`、V8 编译产物、临时文件或凭据。

pyv8c 的 DOM/BOM 语义仍由 `pyv8c` 层负责。底层修改不能绕过接口注册表、原型链、wrapper cache 或 Page 任务队列约束。

## 3. 核心运行时模型

### Browser、Context、Page

- `Browser` 管理多个 `BrowserContext`，并控制全局关闭状态和可选网络后端。
- `BrowserContext` 隔离 Cookie、Blob URL、网络 session、Page 和 iframe 资源。
- `Page` 拥有独立 pyv8 Context、Window、Document、任务队列、wrapper cache 和生命周期状态。
- `Page.set_content(html, url)` 创建一代新的 navigation generation；旧代网络或资源回调必须丢弃。
- `Page.evaluate(script, filename=None)` 在该 Page 的 V8 Context 执行脚本。
- `Page.run_until_idle(timeout=0)` 执行已投递任务；默认不等待网络，正数 timeout 才允许等待异步资源。
- `Page.post_task(callback)` 是后台线程进入 V8 的唯一入口。
- Page、Context、Browser 关闭后，相关 DOM、网络、Worker、iframe 和 WebSocket 操作必须抛出 `InvalidStateError` 或被安全丢弃。

### DOM 真相源和 wrapper

DOM 树由 Python 节点对象维护，JS wrapper 只是暴露层。相同底层节点在同一 Page 内必须复用同一 JS wrapper；`cloneNode()` 创建新节点和新 wrapper。不同 Page/Context 之间不得共享 DOM wrapper。

HTML 解析使用标准库实现，支持标签、属性、文本、注释、doctype、void 元素、常见自动闭合、template 内容和 script/style/textarea/title 原始文本。

## 4. JavaScript 接口和原型链

所有接口必须在 `pyv8c/interfaces.py` 的显式注册表登记。JavaScript 原型链不能从 Python MRO 自动推断。

运行时必须保证：

- `Child.prototype.__proto__ === Parent.prototype`。
- `Interface.prototype.constructor === Interface`。
- 实例 prototype 严格指向对应接口 prototype。
- 构造器的 name、length、可调用性和 `new` 行为来自接口元数据。
- `Symbol.toStringTag`、`Object.prototype.toString()`、`instanceof` 和 `isPrototypeOf` 与登记值一致。
- mixin 成员安装到正确的 prototype/instance，不创建错误的 JavaScript 父类。
- 属性、方法、常量安装到正确的 instance、prototype 或 interface 层，并校验描述符。

继承基线来自本地 renderer 快照。提取工具：

```powershell
python tools/extract_interfaces.py _pyv8env/renderer interface-baseline.json
```

当前核心链：

```text
EventTarget
  └── Node
      ├── Document / DocumentType / DocumentFragment
      ├── CharacterData
      │   ├── Text
      │   └── Comment
      └── Element
          └── HTMLElement
              └── HTML*Element

Event
  ├── UIEvent -> MouseEvent / KeyboardEvent
  └── CustomEvent

Window -> WindowProperties -> EventTarget
Document -> Node -> EventTarget
Location / Navigator / Screen / Storage / URL / URLSearchParams -> Object
```

已登记的集合和资源接口包括 `NodeList`、`HTMLCollection`、`DOMTokenList`、`NamedNodeMap`、`Attr`、`HTMLUnknownElement`、`HTMLTemplateElement`、`HTMLIFrameElement`、`HTMLCanvasElement`、`HTMLMediaElement`、`HTMLImageElement`、`HTMLScriptElement`、`HTMLLinkElement` 和 `HTMLStyleElement`。

## 5. 已支持的浏览器能力

### DOM、BOM 和选择器

- 节点增删改、属性反射、`textContent`、`innerHTML`、`outerHTML`、`cloneNode`。
- `DocumentFragment`、template、实时 `NodeList` 和 `HTMLCollection`。
- `DOMTokenList`、`NamedNodeMap`、表单和常见表格元素。
- 标签、ID、class、通配符、属性选择器、后代/子代/兄弟组合器、分组选择器和常见结构伪类。
- `window`、`self`、`globalThis`、`document`、`location`、`navigator`、`screen`、`history`、Storage、URL、URLSearchParams、console。
- 定时器、微任务和 Page task queue。

### 事件和页面生命周期

- `EventTarget`、`Event`、`CustomEvent`、监听器 capture/once/passive、冒泡和 `on<event>` 属性。
- `stopPropagation`、`stopImmediatePropagation`、`preventDefault`、`composedPath`。
- `Document.readyState`：`loading -> interactive -> complete`。
- `readystatechange`、`DOMContentLoaded`、资源 `load/error`、Window `load`、`pageshow`。
- 导航或关闭时的 `beforeunload -> pagehide -> unload`。
- 资源回调按 navigation generation 校验，并通过 Page 队列进入 V8。

### 网络、iframe、Worker 和 WebSocket

- 默认禁止隐式联网；`resource_loader` 优先于网络后端。
- 可选 `real_network=True` 使用 cyCronet；每个 Context 独立 session，后台线程不能直接进入 V8。
- `fetch`、`Request`、`Response`、`Headers`、异步 XHR、Cookie、基础 CORS、redirect 和 no-cors opaque Response。
- `History` 的 state、push/replaceState、back/forward/go 和 popstate 基础行为。
- iframe 具备独立 Page/Document 基础对象、contentDocument/contentWindow 和父子关系。
- Worker 具备独立线程、Context、脚本源、Blob URL、消息和 terminate 基础能力；DOM wrapper 不跨 Worker 共享。
- WebSocket 具备构造器、状态常量、文本/基础二进制消息、事件、send/close 和 Context 生命周期清理。

## 6. 错误体系

`pyv8c/error.py` 提供 `V8Error`、`V8TypeError`、`V8RangeError`、`V8ReferenceError`、`V8SyntaxError`、`V8URIError`、`V8EvalError`、`DOMException`、`DOMError` 及 DOMException 工厂。

JS 可调用 API 不得直接暴露裸 Python `RuntimeError`、`ValueError`、后台线程异常或纯字符串异常。主要映射：

```text
非法参数/receiver       -> TypeError
非法 selector            -> SyntaxError
非法节点关系/状态        -> NotFoundError / HierarchyRequestError / InvalidStateError
跨源、CORS、History      -> SecurityError
网络失败、Blob URL 失败  -> NetworkError
同步 XHR                -> NotSupportedError
WebSocket 非法状态       -> InvalidStateError
结构化克隆失败           -> DataCloneError
```

所有异步边界统一使用 `as_js_error()` 转换异常；Cronet、Worker 和 Page 队列线程不得把 Python 异常直接抛入 V8。

## 7. 动态接口桩和全局暴露

`interface_runtime.py` 直接读取 `interface-baseline.json`，不为每个 Chromium 接口生成独立 Python 文件。运行时采用三层合并：baseline 描述接口表面，`interfaces.py` 中的真实实现覆盖同名接口和成员，剩余成员由统一桩生成器补齐。

- 桩方法、getter、setter 和构造器通过 `loguru.logger.info` 输出“待实现”，方法和 getter 默认返回 `undefined`。
- 真实类及其成员优先级最高；部分实现类只补充 baseline 中缺失的成员。
- `prototype`、`interface`、`instance` 安装位置、构造器长度、标签和父接口来自 baseline 或真实装饰器元数据；已实现类的装饰器元数据优先于不完整的快照构造器字段。
- 只向 Window/Worker 暴露 baseline 明确标记的公共接口；baseline 中的 renderer 内部接口、注册表、logger、Page/Browser/Context 和任何 `__pyv8c_*` 名称不会进入 JS 全局。
- Window 和 Worker 的 `globalThis` 均由 V8 Context 提供；`window`、`self` 通过对应全局接口的只读属性返回全局对象，不使用 `context.expose()` 注入别名数据属性。
- `Browser(generate_interface_stubs=True, expose_unimplemented=True)` 启用公共未实现接口桩；将 `expose_unimplemented=False` 可只暴露真实实现。
- `loguru>=0.7` 是运行时依赖。复杂返回值、精确常量值和手工审核的 Web IDL 字段仍由真实实现逐步覆盖，桩不会伪造数值或 DOM 对象。

## 8. 构建和验证

当前目标 V8 版本为 `15.2.20.1`。Windows 构建环境：

```powershell
$env:PYV8_V8_VERSION = "15.2.20.1"
$env:PYV8_VS_HOME = "D:\soft\Microsoft\vs2026"
$env:PYV8_VS_VERSION = "2026"
$env:PYV8_WINDOWS_SDK = "D:\Windows Kits\10"
$env:PYV8_BUILD_ROOT = "D:\code\pyv8env\_pyv8\.pyv8-build"
$env:DEPOT_TOOLS_WIN_TOOLCHAIN = "0"

cd D:\code\pyv8env\_pyv8
python setup.py build_v8
python setup.py build_ext --inplace
```

`PYV8_VS_HOME` 指向 Visual Studio 安装根目录，不是 `Common7` 子目录。构建器在 Windows 上优先使用 depot_tools 的 `.cipd_bin\vpython3.exe` 执行 GN/autoninja，外层 Python 只负责 setup.py 和扩展构建。

pyv8c 回归测试：

```powershell
$env:PYTHONPATH = "D:\code\pyv8env\_pyv8;D:\code\pyv8env\pyv8env"
python -m pytest -q tests
```

wheel：

```powershell
cd D:\code\pyv8env\pyv8c
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

测试必须覆盖 native 生命周期、原型链、DOM identity、事件时序、错误映射、网络队列、iframe、Worker 和 WebSocket。真实网络测试使用本地 fixture server，不依赖公网。

## 8. 全局暴露规则与 URL 状态

标准浏览器 Web API 仍按浏览器语义暴露，包括 `URL`、`Blob`、`fetch`、`Request`、`Response`、`WebSocket` 和 `Worker`。`window`、`self`、`globalThis` 只允许包含浏览器公共对象；Page、Browser、Context、网络后端和其他 Python 实现类不得暴露。

内部 `__pyv8c_*` 名称严禁出现在任何 JS 全局对象中。Promise resolver 使用 IIFE 闭包返回，Worker structured clone 使用上下文内函数调用，Blob URL 通过 `URL` 的接口级静态方法访问，不再向全局写入临时桥接函数。

`URL` 已按本地 renderer 快照登记到 `interface-baseline.json`，实现了构造器、URL 组成部分属性、`searchParams`、`toString()`、`toJSON()`、`canParse()`、`parse()`、`createObjectURL()` 和 `revokeObjectURL()`。`URL` 构造器长度、`new`/直接调用行为、prototype 成员位置和 `Symbol.toStringTag` 由接口注册表校验。

Blob URL 当前严格使用 `blob:<serialized-origin>/<uuid-v4>` 格式，例如 `blob:https://github.com/2a405ce7-7b00-474b-8419-43372167ad40`；默认端口省略，凭据不进入 origin，opaque origin 使用 `null`。

接口提取工具现在会合并 core/modules 生成 binding，记录 Window/Worker 暴露范围以及属性、prototype/interface 方法成员。重新生成基线时使用：

```powershell
python tools/extract_interfaces.py ..\_pyv8env\renderer interface-baseline.json
```

## 10. Web IDL 参数校验（当前状态）

JS 可见构造器、方法和属性 setter 统一经过 `pyv8c/webidl.py` 校验层。签名由显式 `ParamSpec`/`MethodSpec` 登记，不从 Python 函数签名或 Python MRO 推断；接口继承只用于沿登记的 IDL 父链查找成员签名。

当前已覆盖：必需参数数量、普通多余参数忽略、可选参数默认值、`undefined`/`null` 区分、DOMString/USVString、布尔、long/unsigned long/double、接口 receiver、dictionary、sequence、enum、callback、union、variadic 参数，以及构造器和 setter。`fetch()` 的参数错误通过 Page 任务队列 reject Promise，DOM 同步方法抛出标准 `V8TypeError`/`DOMException`。

`tools/extract_interfaces.py` 现在从 renderer callback 的 `NotEnoughArguments` 和 `NativeValueTraits<...>::ArgumentValue` 提取操作签名；无法完整推导的签名标记为 `manual-review-required`。`interface-baseline.json` 顶层 `_webidl` 节点记录生成器、快照和运行时签名表来源。

本轮验证：`30 passed`。剩余限制是复杂 overload/union、完整字典字段校验、全部 Web IDL 枚举和精确 Chromium 异常文本仍需按 renderer 逐项人工复核。

## 9. 真实遗留问题和后续方向

以下是当前仍未完成的能力。除非有代码和测试证据，不得将其描述为已实现：

1. renderer 快照仍有大量接口未登记或未实现；完整 Web IDL 成员、描述符、mixin 来源、overload/union 校验和非法 receiver 文本还不完整。
2. cyCronet 原生 wheel、DLL、实际版本的 HTTP/WebSocket callback 签名和真实端到端行为仍需验证；fake backend 不能替代真实集成测试。
3. CORS、Cookie、SameSite、缓存、预检请求、复杂 credentials 和 redirect 仍是基础实现。
4. WebSocket 的 Blob/ArrayBuffer 仍是基础映射，没有完整 Blob 对象和二进制 structured clone。
5. Worker 尚不是完整浏览器调度器；定时器、fetch、`importScripts`、模块 Worker、Map/Set/循环引用 structured clone、MessagePort 等未完整实现。
6. Blob URL 的完整 origin、生命周期和跨 iframe 共享策略未实现；isolate persistent handle 的严格回收仍需加强。
7. iframe 的完整同源/跨源安全策略、frame tree 导航和真实文档导航未完成。
8. `async`/`defer`/模块脚本、preload scanner、完整 HTML parser insertion mode 尚未达到 Chromium 细节。
9. 布局、渲染、CSSOM、bfcache、Service Worker、浏览器确认对话框和完整 Chromium 进程调度不在当前范围。

后续优先级：先完善接口基线/descriptor/错误测试，再验证 cyCronet 真实后端和异步资源队列，然后补齐 iframe 同源策略、Worker 调度和 structured clone，最后扩展更多 Web IDL 接口及非核心渲染能力。

## 10. Chromium 接口基线提取（当前状态）

`tools/extract_interfaces.py` 已重写为基于平衡括号扫描的 C++ binding 提取器。它扫描 `_pyv8env/renderer/core` 和 `modules` 下全部生成文件，解析 `WrapperTypeInfo`、父接口、暴露范围、`AttributeConfig`、`OperationConfig`、`ConstantValueConfig` 以及构造器/操作 callback 中的 `NotEnoughArguments` 和 `NativeValueTraits`。

当前 `interface-baseline.json` 覆盖 renderer 快照中的全部接口，而不是只覆盖 pyv8c 已实现接口。最新生成报告为：1173 个接口、10362 个成员、1081 个含成员接口、602 个含签名接口、2351 个需人工审核字段、26 个重复来源、0 个解析失败。URL 与其他接口使用同一套 core/modules 合并逻辑。

重新生成命令：

```powershell
python tools/extract_interfaces.py ..\_pyv8env/renderer interface-baseline.json --report
```

运行时仍只遍历 `INTERFACES` 中显式登记并实现的接口，不会因为接口存在于完整 baseline 就自动暴露。`validate_registry()` 会校验已实现接口的 baseline 存在性、父接口、tag、暴露范围交集、继承成员和 Web IDL 签名；未实现 renderer 接口只作为快照记录。

提取器无法完整推导的复杂 overload、union、dictionary 字段和构造器细节会显式标记 `manual-review-required`，不能把空字段当作已确认结果。
