# pyv8c

Baseline note: `pyv8c/baselines/interface-baseline.zip` uses schema 5. The runtime extracts its JSON payload beside the archive on first use and reuses that local cache thereafter.

## 内部结构

顶层 `pyv8c` 是唯一稳定的 Python 导入面。实现按单向领域层次组织：

```text
core -> bindings -> dom -> bom/network/workers -> runtime
```

- `core`：错误类型和 realm 协议。
- `bindings`：schema 5 baseline、Web IDL、接口 registry、动态 V8 overlay 和提取器。
- `dom`、`bom`、`network`、`workers`：浏览器领域 API。
- `runtime`：Browser、Context、Page 及任务、导航、脚本和资源编排。

内部子模块路径不是兼容 API。每个 V8 Context 使用独立 `BindingSession`；页面、Worker 和网络对象通过显式 realm 注入取得运行时服务，不依赖线程局部“当前页面”。

`pyv8c` 是建立在本地 [`pyv8`](../_pyv8) 扩展之上的、可重复运行的
Chromium-like BOM/DOM 运行时。它用 Python 对象保存 DOM 树，并通过显式的
Web IDL 元数据把 Python 实现映射到 JavaScript 原型链，适合页面脚本测试、
协议适配和离线自动化。

启动性能可使用独立进程基准测量：

```powershell
python tools/benchmark_startup.py --runs 20
```

默认 Page 只预热核心接口，其余 Window 接口保留完整名称表并在首次访问时物化。
开发或 CI 需要全量 Web IDL 校验时设置 `PYV8C_STRICT_VALIDATION=1`；设置
`PYV8C_STARTUP_STATS=0` 可关闭启动统计。

它不是完整浏览器：当前不包含布局、绘制、CSSOM、浏览器进程模型或完整的
WHATWG/Chromium 行为。

## 特性概览

- `Browser -> BrowserContext -> Page` 三级生命周期和资源隔离；每个 Browser
  可以使用独立的 Web IDL 基线。
- 原生 Python DOM：HTML 解析、DOM 增删改、节点移动/替换、属性、
  `classList`、`children`、`querySelector/querySelectorAll` 等选择器操作。
- 事件目标和事件传播：捕获、冒泡、`once`/`passive`/`signal` 选项、事件处理器
  属性，以及 `DOMContentLoaded`、`load`、`beforeunload`、`pagehide`、
  `unload`、`pageshow` 等页面生命周期事件。
- BOM 基础对象：`window`、`location`、`navigator`、`screen`、Storage、
  URL/URLSearchParams 和 History；支持定时器、微任务和页面任务队列。
- 网络 API：`fetch`、`Request`、`Response`、`Headers`、异步
  `XMLHttpRequest`、Cookie/CORS 基础策略、Blob/Blob URL、iframe、Worker
  和 WebSocket。
- 从 Chromium 生成的基线动态暴露公共接口。尚未实现的接口以 stub 形式存在，
  调用时记录 `loguru` 日志并返回 JavaScript `undefined`；已有 Python 实现
  始终优先于 stub。

## 安装与前置条件

- Python 3.10 或更高版本。
- 可导入的 `pyv8` 扩展。开发环境通常先在同一工作区构建
  [`_pyv8`](../_pyv8)，或安装对应的 `pyv8` wheel。

在 `pyv8c` 目录安装：

```powershell
python -m pip install -e .
```

如需真实 HTTP/WebSocket 后端，再安装可选依赖：

```powershell
python -m pip install -e ".[network]"
```

不启用真实网络时无需 `cycronet`；可以使用下文的 `resource_loader` 完全离线运行。

## 快速开始

默认情况下网络访问被禁止，`set_content` 直接解析 HTML：

```python
from pyv8c import Browser

browser = Browser()
page = browser.new_context().new_page()
page.set_content("<html><body><div id='app'>ready</div></body></html>")

assert page.evaluate("document.querySelector('#app').textContent") == "ready"
print(page.evaluate("document.readyState"))  # complete

browser.close()
```

## Browser environment

`BrowserEnvironment` provides an immutable browser-profile snapshot. Pass it to
`Browser(environment=...)` for the default profile, or to
`BrowserContext.new_page(environment=...)` for a page-specific overlay. Worker
and iframe realms inherit their parent page's final snapshot.

```python
from pyv8c import Browser, BrowserEnvironment, ViewportConfig, GeolocationConfig

environment = BrowserEnvironment(
    user_agent="ExampleBrowser/1.0",
    platform="Linux x86_64",
    language="zh-CN",
    languages=("zh-CN", "en"),
    viewport=ViewportConfig(width=390, height=844, device_scale_factor=3),
    geolocation=GeolocationConfig(
        permission="granted", latitude=31.2304, longitude=121.4737, accuracy=10,
    ),
)
browser = Browser(environment=environment)
page = browser.new_context().new_page()
```

The environment controls Navigator values, viewport/screen properties and the
Window-only `navigator.geolocation` API. Geolocation callbacks are asynchronous
and are delivered by `page.run_until_idle()`. A mapping with snake_case keys is
also accepted; page mappings overlay the Browser default. Default `User-Agent`
and `Accept-Language` headers are added to navigation, fetch, XHR, and document
resource requests only when the caller did not explicitly provide those headers.
`timezone` is stored in `Browser.environment` and `Page.environment`; it does
not yet change V8/ICU `Intl` formatting.

`environment.plugins` defaults to the current Chrome PDF Viewer snapshot. It
can be replaced with immutable `PluginConfig`/`MimeTypeConfig` objects or the
equivalent snake_case mappings; `navigator.plugins` and `navigator.mimeTypes`
then expose browser-compatible indexed and named collections.

```python
from pyv8c import Browser, PluginConfig, MimeTypeConfig

browser = Browser(environment={
    "plugins": [PluginConfig(
        "Example PDF", "example-pdf", "Example document viewer",
        (MimeTypeConfig("application/x-example-pdf", "expdf", "Example PDF"),),
    )],
})
```

`Page.evaluate(script, filename=None)` 同步执行 JavaScript，并返回转换后的
Python 值。关闭顺序通常只需要调用 `browser.close()`，它会级联关闭 Context、
Page、iframe、Worker、WebSocket 和 V8 Context。

## 加载页面和资源

### 注入离线加载器

`resource_loader` 接收 `(url, request)`，可以返回 `Response`、字典或任意值。
字典至少可以包含 `body`，也可设置 `status`、`headers`、`url` 等字段。fixture
加载器同样经过异步 Page 任务队列，因此 `fetch`/XHR 的回调不会在调用点同步执行。

```python
from pyv8c import Browser, Response

def load(url, request):
    if url.endswith("/index.html"):
        return Response(
            "<body><main id='app'>loaded</main><script src='/app.js'></script></body>",
            {"url": url, "headers": {"content-type": "text/html"}},
        )
    if url.endswith("/app.js"):
        return Response("document.querySelector('#app').setAttribute('data-ready', 'yes')",
                        {"url": url})
    return Response("not found", {"status": 404, "url": url})

browser = Browser(resource_loader=load)
page = browser.new_context().new_page()
response = page.goto("http://example.test/index.html", wait_until="load")
assert response.status == 200
assert page.evaluate("document.querySelector('#app').getAttribute('data-ready')") == "yes"
browser.close()
```

`Page.goto(url, timeout=30, wait_until="load")` 支持等待 `commit`、
`domcontentloaded` 或 `load`。`new_page(url)` 在 URL 不是 `about:blank` 时会自动
调用 `goto`；从 JavaScript 设置 `location.href` 或 `window.location` 会把导航
排入 Page 任务队列。响应体为字节时，会按照 `Content-Type` 中的 `charset` 解码。

### 真实网络

传入 `real_network=True` 后，每个 BrowserContext 创建一个可选的 `cycronet`
后端。请求在后台线程执行，结果通过 Page 任务队列交付，避免工作线程直接进入
V8：

```python
browser = Browser(real_network=True, cycronet_options={"max_workers": 8})
page = browser.new_context().new_page("https://example.com/")
print(page.evaluate("document.title"))
browser.close()
```

如果同时提供 `resource_loader`，它优先于 `cycronet`。未配置任何加载方式时，
只有 `about:blank` 和当前文档可以读取，其余请求会抛出网络错误。

## JavaScript 运行时接口

常用对象包括：

- DOM：`Document`、`Element`、`HTMLElement`、`Text`、`Comment`、
  `DocumentFragment`、`HTMLTemplateElement`、`HTMLImageElement`、
  `HTMLScriptElement`、`HTMLLinkElement`、`HTMLStyleElement`、
  `HTMLIFrameElement`，以及 `NodeList`、`HTMLCollection`、`DOMTokenList`、
  `NamedNodeMap`。
- 事件：`Event`、`UIEvent`、`MouseEvent`、`KeyboardEvent`、`CustomEvent`、
  `MessageEvent`、`CloseEvent`、`ErrorEvent` 和 `EventTarget`。
- 网络/并发：`fetch`、`XMLHttpRequest`、`Blob`、`URL.createObjectURL`、
  `Worker`、`WebSocket`。Worker 在独立线程、Isolate 和 Context 中执行，消息
  使用受限的 structured clone。
- 错误：标准 V8 Error 子类和 `DOMException`。同步 DOM/BOM 错误会直接抛出；
  `fetch` 等异步 API 会以 JavaScript Error/DOMException 拒绝 Promise。

页面解析期间，经典 parser-blocking 外部脚本会暂停解析；`async`、`defer` 和
`type="module"` 走独立调度路径。当前 module 支持静态 `import` 的依赖优先加载，
不是完整的 V8 ES Module 实现。

异步操作可显式推进：

```python
page.evaluate("fetch('/data').then(r => r.text()).then(v => window.result = v)")
page.run_until_idle()             # 处理已就绪的任务
page.run_until_idle(timeout=2)    # 最多等待网络/Worker 任务 2 秒
assert page.evaluate("window.result") == "..."
```

## Chromium Web IDL 基线

默认基线发布为 `pyv8c/baselines/interface-baseline.zip`，采用 schema 5；其中的
构造器、操作和属性 setter 签名是运行时唯一依据。创建 Browser 时会读取并建立只读
基线内容，因此同一进程中的不同 Browser 可以安全使用不同版本：

schema 5 各接口字段的完整说明见 [`doc/interface-baseline-fields.md`](doc/interface-baseline-fields.md)。

```python
browser = Browser(interface_baseline_path="baselines/chromium-variant.json")
print(browser.interface_baseline_path)
print(browser.interface_baseline_info["sha256"])
```

`generate_interface_stubs=False` 可关闭 stub 生成，`expose_unimplemented=False`
可隐藏未实现接口；`stub_logging` 控制待实现成员的日志级别（例如 `"off"`）。
真实实现缺少可解析的签名会导致 Browser 创建失败，而未实现的生成 stub 仍可调用
并返回 `undefined`。

### 拉取 Chromium 源码

`tools/fetch_chromium.py` 负责查询 release tag、拉取 `chromium/src`、同步
DEPS 依赖，并执行 GN/autoninja 生成 Blink 的 `v8_*.cc` 绑定文件：
脚本需要系统已安装 Git；Windows 上缺少 depot_tools 时会自动 clone 到指定路径。
默认版本记录在项目根目录的 `CHROMIUM_VERSION`（当前为 `154.0.8030.0`），
默认目录布局为项目下的 `.chromium-build`：其中包含同级的 `chromium`
（源码在 `chromium/src`）和 `depot_tools`。

```powershell
# 查看每个 milestone 的最新 release tag
python tools/fetch_chromium.py --list

# 使用 CHROMIUM_VERSION 拉取到
# pyv8c/.chromium-build/chromium/src
python tools/fetch_chromium.py

# 交互选择本次使用的版本
python tools/fetch_chromium.py --select

# 拉取指定版本
python tools/fetch_chromium.py --version 140.0.7339.82

# 自定义 workspace、depot_tools 和并发数
python tools/fetch_chromium.py `
  --version 140.0.7339.82 `
  --root "D:\work\chromium" `
  --depot-tools "D:\src\depot_tools" `
  --jobs 8
```

`--list` 显示每个 milestone 的最新 tag；不传 `--version` 或 `--select` 时使用
`CHROMIUM_VERSION`，不会每次重复查询远端。默认 workspace 可通过 `CHROMIUM_ROOT` 覆盖，
版本也可通过 `PYV8C_CHROMIUM_VERSION` 临时覆盖。depot_tools 可通过
`DEPOT_TOOLS_ROOT` 覆盖；未覆盖时使用 `.chromium-build/depot_tools`，目录
目录不存在时会自动 clone。已有 `src` checkout 必须干净且远端为官方 Chromium
仓库，脚本不会覆盖 `src` 中的本地修改；`gclient sync -D` 会按 DEPS 清理或更新
受管理的依赖目录。

拉取和生成完成后，把 `src/out/Cronet145` 下的 bindings 目录传给接口提取器：

```powershell
python tools/extract_interfaces.py pyv8c\baselines\interface-baseline.zip `
  --bindings-dir "D:\code\pyv8env\pyv8c\.chromium-build\chromium\src\out\Cronet145\gen\third_party\blink\renderer\bindings" `
  --report
```

默认 baseline 不包含 C++ callback 名称、默认 `world` 和每个接口的输入路径，且
`_webidl.compact` 为 `true`。需要审计这些提取证据时传入统一的 `--audit` 参数；
审计产物的 `compact` 为 `false`，不能直接作为运行时 baseline 加载。

提取器只扫描已经生成的 Blink binding 文件，不执行构建。无法由生成代码完整表达的
签名会在生成阶段依据 `tools/webidl-reviews.json` 审核并嵌入基线，审核文件不会在
运行时加载。

## 测试与开发

在 `pyv8c` 目录运行：

```powershell
python -m pytest -q
python -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

测试覆盖 DOM/选择器、事件和生命周期、导航与脚本调度、网络/CORS/Cookie、
iframe、Worker、WebSocket、错误转换、基线隔离和接口提取器。构建 `pyv8` 扩展
本身请参阅 [`_pyv8` 使用指南](../_pyv8/doc/USER_GUIDE.md)。

## 当前限制

- HTML 解析器不是完整 WHATWG tokenizer/insertion-mode 实现，尚不支持
  `document.write`、完整 preload scanner 和所有错误恢复规则。
- module 仅实现静态 import 适配层，不支持 dynamic import、import maps、
  top-level await 或 module Worker。
- CORS、Cookie、重定向和 cycronet 的兼容性仍是基础实现；默认不联网。
- Worker structured clone 尚未覆盖 transfer list、Map/Set 及规范允许的全部循环
  引用场景；Worker 定时器、`importScripts` 和 MessagePort 也不完整。
- WebSocket、Blob URL 和 iframe 的跨 Context/跨源生命周期与安全语义仍未完全对齐
  浏览器；不提供渲染、布局、CSSOM、bfcache 或 Service Worker。
- Chromium 基线包含大量尚未实现的公共接口；这些接口暴露为 stub 不代表功能已经
  实现。
