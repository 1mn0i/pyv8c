# pyv8c 当前状态

本文是后续开发的事实基线，只描述当前架构、可用能力、约束和真实遗留问题。每轮工作的历史记录放在 [WORKLOG.md](WORKLOG.md)。实现变化时同步更新两个文件，但不要在本文追加开发过程。

## 1. 项目定位

`pyv8c` 是基于 `_pyv8` 和 V8 `15.2.20.1` 的 Chromium-like BOM/DOM 运行时。目标是在 JavaScript 侧提供接近 Chromium 的接口继承、构造器、事件、DOM、异步队列和网络 API；不实现布局、绘制或 Chromium 多进程架构。

```python
from pyv8c import Browser

browser = Browser()
context = browser.new_context()
page = context.new_page()
page.set_content("<body><div id='app'>hello</div></body>")
print(page.evaluate("document.querySelector('#app').textContent"))
browser.close()
```

目录职责：

| 目录 | 职责 |
| --- | --- |
| `pyv8c/` | Browser、Page、DOM/BOM、事件、网络和接口运行时 |
| `pyv8c/baselines/` | 随 wheel 发布的 Chromium 接口快照 |
| `tests/` | 单元、集成和 native subprocess 测试 |
| `tools/` | renderer binding 提取和基线审核工具 |
| `../_pyv8/` | V8 Python 扩展，是 pyv8c 的核心依赖，允许按需修改 |
| `../_pyv8env/` | 旧实现及 renderer binding 快照参考 |

`_pyv8` 和 `pyv8c` 是独立 Git 仓库。底层修改必须同时验证 `_pyv8` 与 `pyv8c`，不能绕过接口注册、Context 隔离、wrapper identity 或 Page 任务队列。

## 2. 运行时和生命周期

- `Browser` 管理 `BrowserContext`，并持有该 Browser 专属的 `InterfaceUniverse`。
- `BrowserContext` 隔离 Page、Cookie、Blob URL、Cronet session、iframe 和网络资源。
- 每个 `Page` 拥有独立 V8 Context、Window、Document、任务队列和 navigation generation。
- `Page.post_task()` 是网络及 Worker 后台线程进入 V8 的唯一入口。
- `Page.run_until_idle(timeout=0)` 默认只消费已就绪任务；正数 timeout 可等待网络、Worker 和定时器。
- 定时器以 `time.monotonic()` 的绝对到期时间调度，页面关闭后未执行任务被丢弃。
- Page/Context/Browser 关闭后继续使用会抛出 `InvalidStateError`；`Page.close()` 显式释放 `_pyv8.Context`。
- navigation generation 改变后，旧文档资源和异步回调不能污染新文档。
- `Page.goto(url)` 通过 `resource_loader` 或可选 cyCronet 后端下载主文档 HTML，并可等待 `DOMContentLoaded` 或 `load`。
- `BrowserContext.new_page(url)` 自动导航；JavaScript 设置 `location.href` 或 `window.location` 时通过 Page 任务队列执行同一导航流程。

文档生命周期顺序：

```text
loading
  -> parser 完成
  -> defer/module 脚本完成
  -> interactive + readystatechange
  -> DOMContentLoaded
  -> 文档资源完成
  -> complete + readystatechange
  -> window load
  -> pageshow
```

导航替换和关闭会派发 `beforeunload -> pagehide -> unload`。普通导航允许 `beforeunload` 取消；`Page.close()` 忽略取消结果。

## 3. DOM、HTML 解析和脚本

Python DOM 节点是唯一真相源。JS wrapper 只负责暴露，同一 Page 中相同节点必须保持 JS identity；不同 Page/Context 之间不得共享 DOM wrapper。

已实现的核心能力：

- `Document`、`DocumentType`、`DocumentFragment`、`Node`、`Element`、`HTMLElement`、Text、Comment 和常见 HTML 元素。
- `NodeList`、`HTMLCollection`、`DOMTokenList`、`NamedNodeMap`、`Attr`、template content 和未知元素映射。
- append/insert/remove/replace、fragment 插入、clone、`textContent`、`innerHTML`、`outerHTML` 和 parentNode 一致性。
- 标签、ID、class、属性、后代/子代/兄弟组合器、分组及常用结构/逻辑伪类选择器；非法 selector 抛 `SyntaxError`。
- HTML 标签、属性、文本、注释、doctype、void 元素、常见自动闭合以及 script/style/textarea/title 原始文本。

页面解析采用可恢复的增量 parser：

- `set_content()` 同步解析到第一个 parser-blocking 外部经典脚本，然后立即返回。
- 外部经典脚本完成下载和执行后，从脚本之后继续解析，因此后续 DOM 不会提前出现。
- 内联经典脚本按文档顺序执行；`async` 独立执行且不阻塞 DOMContentLoaded；`defer` 保持顺序并阻塞 DOMContentLoaded。
- `type="module"` 当前支持静态相对 import 的递归加载和依赖优先执行，并按 defer 语义等待。
- 动态插入的经典脚本默认异步；显式 `async=false` 使用有序队列。
- detached script 设置 `src` 或文本不会加载，连接到当前 Document 后才开始。
- 脚本、样式、图片、媒体和 iframe 资源成功/失败会派发元素级 `load/error`。

## 4. 接口基线、原型链和动态桩

默认基线位于 `pyv8c/baselines/interface-baseline.json`，覆盖本地 renderer 快照中的全部接口。生成命令：

```powershell
python tools/extract_interfaces.py ..\_pyv8env\renderer pyv8c\baselines\interface-baseline.json --report
```

当前 baseline schema 为 3。包内 `_webidl.report` 记录：1173 个接口、10362 个成员、1081 个含成员接口、612 个含签名接口、3458 个自动签名、220 个审核签名、2900 个 unresolved 签名、26 个重复来源接口、0 个解析失败。

核心规则：

- 所有真实接口通过 `interfaces.py` 显式登记，JS 原型链不从 Python MRO 推断。
- `Child.prototype.__proto__`、constructor、name、length、toStringTag、成员 location 和 descriptor 来自 baseline/显式元数据。
- `Browser(interface_baseline_path=...)` 可选择外部完整 schema 3 快照；创建后内容冻结。
- 每个 Browser 使用独立 `InterfaceUniverse`；多个版本的动态类、BindingPlan 和 Context overlay 不串扰。
- `_pyv8.Context(class_configs=...)` 提供 Context-local 构造器、父模板、成员和 callback overlay。
- 已实现类和成员优先；未实现公共接口及缺失成员由 `interface_runtime.py` 动态生成桩。
- 桩仅在实际调用/读取时使用 `loguru.logger.info` 输出待实现并返回 `undefined`。
- `__v8_idl_stub__` 只标记动态桩；`__v8_direct_callback__` 只控制 overlay receiver 调用约定。

Web IDL 参数校验完全由当前 Browser 的 baseline 编译为 `BindingPlan`。`webidl.py` 只提供通用类型转换、optional/nullable/variadic、dictionary、sequence、enum、union、callback、overload 和异常生成，不维护接口名注册表。真实成员的签名必须为 `generated` 或 `manual-reviewed`；unresolved 只允许用于未实现动态桩。

## 5. 全局暴露和浏览器对象

只有 baseline 标记为当前 Window/Worker execution context 公共接口的对象可以进入全局。禁止暴露 Page、Browser、Context、logger、注册表、网络后端或任何 `__pyv8c_*` 名称。

- `window`、`self` 和 `globalThis` 由对应全局接口/V8 Context 提供，不通过临时 `context.expose()` 别名注入。
- Promise resolver 使用闭包记录，不创建全局临时变量。
- Window、Worker 和 iframe 都使用所属 Browser 的 InterfaceUniverse。
- 已实现 `window`、document、location、navigator、screen、history、Storage、console、定时器、URL 和 URLSearchParams。

`URL` 是正式注册接口，支持构造、组成属性、`searchParams`、`toString()`、`toJSON()`、`canParse()`、`parse()`、`createObjectURL()` 和 `revokeObjectURL()`。Blob URL 格式为：

```text
blob:<serialized-origin>/<uuid-v4>
```

例如 `blob:https://github.com/2a405ce7-7b00-474b-8419-43372167ad40`。opaque origin 使用 `null`。

## 6. 事件和错误体系

事件支持 capture、target、bubble、once、passive、`on<event>`、stopPropagation、stopImmediatePropagation、preventDefault 和基础 composedPath。Document 的事件路径可到达 Window。

`error.py` 提供 V8 Error 桥接、`DOMException`、`DOMError`、标准 DOMException 工厂及 `as_js_error()`。JS API 不得直接泄漏 Python `RuntimeError`、`ValueError`、字符串异常或后台线程异常。

```text
参数/receiver 错误          -> TypeError / RangeError
非法 selector/格式         -> SyntaxError
DOM 关系或状态错误          -> NotFoundError / HierarchyRequestError / InvalidStateError
跨源、CORS、History         -> SecurityError
网络和无效 Blob URL         -> NetworkError
同步 XHR                   -> NotSupportedError
structured clone 失败       -> DataCloneError
```

## 7. 网络、iframe、Worker 和 WebSocket

- 默认不联网；`resource_loader` 优先于所有后端。
- `Browser(real_network=True)` 可选用 cyCronet；每个 BrowserContext 一个 session。
- 顶层 HTML 导航使用 `mode=navigate`，不套用 fetch CORS 限制；响应 URL、HTTP charset、解析、子资源和页面生命周期均接入现有 Page 流程。
- fetch、Request、Response、Headers、异步 XMLHttpRequest、redirect、基础 Cookie 和 CORS 已接入 Page 队列。
- 同步 fixture loader 与 Cronet 结果使用相同异步边界；fetch/XHR 不阻塞文档 load。
- iframe 使用独立 Page/Document/Context，父页面等待子页面 load；提供基础同源访问和父子关系。
- Worker 使用独立线程、Isolate、Context 和消息队列，可加载 HTTP/fixture/Blob URL 脚本并 terminate。
- structured clone 采用严格白名单，支持 primitive、数组、普通对象、ArrayBuffer/TypedArray 和 Blob；函数、DOM wrapper、循环引用及不支持对象抛 `DataCloneError`。
- WebSocket 提供 readyState、文本/二进制消息、Blob/ArrayBuffer 基础映射、事件、send/close 和 Page/Context 清理。
- History 支持 state、pushState、replaceState、back、forward、go、popstate 及 Location 同步。

## 8. 构建和验证

Windows V8 构建环境：

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

`PYV8_VS_HOME` 指向 Visual Studio 安装根目录，不是 `Common7`。Windows 构建器优先使用 depot_tools 的 `.cipd_bin\vpython3.exe` 执行 GN/autoninja。

项目验证：

```powershell
$env:PYTHONPATH = "D:\code\pyv8env\_pyv8;D:\code\pyv8env\pyv8c"
python -m pytest -q tests
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

最近一次验证（2026-08-28）：

- `_pyv8`：`164 passed`，V8 `15.2.20.1`，`build_ext --inplace` 成功。
- `pyv8c`：`70 passed`。
- wheel：`pyv8-2.1.2-cp310-abi3-win_amd64.whl`、`pyv8c-0.1.0-py3-none-any.whl` 构建成功。

## 9. 真实遗留问题和后续方向

以下能力尚未完成，除非有代码和测试证据，不得在文档中描述为已实现：

1. baseline 仍有大量 unresolved 签名和动态桩；复杂 Web IDL overload、union、dictionary、mixin 成员、descriptor 和精确 receiver 错误仍需补齐。
2. 当前 module script 是静态 import 适配层，不具备完整 V8 ES Module record、独立 lexical environment、live binding、dynamic import、import maps、top-level await 和 module Worker。
3. HTML parser 尚无完整 WHATWG insertion mode、`document.write()`、speculative preload scanner 和 Chromium 级错误恢复。
4. CORS 预检、完整 Cookie/SameSite/过期策略、缓存、redirect 和 credentials 仍是基础实现；cyCronet 不同原生 wheel/DLL 的真实 HTTP/WebSocket 兼容性仍需端到端验证。
5. Worker 尚缺完整定时器/fetch/importScripts、MessagePort、transfer list 所有权转移、Map/Set 和规范允许的循环 structured clone。
6. WebSocket 的 Blob/ArrayBuffer、bufferedAmount、协议协商和关闭错误细节仍未完全对齐浏览器。
7. iframe 的完整同源/跨源 WindowProxy、安全策略、frame tree 导航和沙箱属性未完成。
8. Blob URL 的完整跨 iframe/Worker 共享、MediaSource 支持和所有生命周期边界未完成。
9. 全量 Chromium interface template 仍在每个 Context 批量创建，需评估懒加载；native persistent handle 和故障注入覆盖仍需加强。
10. 布局、渲染、CSSOM、bfcache、Service Worker 和完整 Chromium 进程/调度模型不在当前范围。

后续优先级：先补齐已实现公共接口的 Web IDL/descriptor 测试，再完善真实 cyCronet 与跨源语义，然后扩展 iframe、Worker 和 native 生命周期，最后处理非核心接口及性能。
## Browser CDP baseline collection

`tools/extract_browser_interfaces.py` is the low-cost alternative to compiling
Chromium renderer bindings. It requires an explicit Chrome or Edge executable,
opens a visible browser window, creates an isolated temporary profile, reflects
Window and Dedicated Worker globals through CDP, and always closes the process
and removes the profile on success, failure, or Ctrl+C. It never uses
`--headless` and does not require Playwright or Selenium.

```powershell
python -m pip install -e ".[browser]"
python tools/extract_browser_interfaces.py --browser "C:\Program Files\Google\Chrome\Application\chrome.exe" --output build\interface-baseline.browser.json
```

`tools/runtime-signatures.json` contains the reviewed signatures for current
Python implementations. Keep the bundled baseline as `--reference-baseline`
until the generated file has passed `BaselineRepository.load()` and
`InterfaceUniverse.validate()`.
