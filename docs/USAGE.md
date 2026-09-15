# 使用与配置

[返回文档索引](README.md)。本页对应 [Browser/Page 实现](../pyv8c/platform/browser.py)，不假定与 Playwright 或 Chrome API 等价。

## 创建与关闭

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    page.set_content("<p id='message'>hello</p>", url="https://example.test/")
    assert page.evaluate("document.getElementById('message').textContent") == "hello"
finally:
    browser.close()
```

Browser、BrowserContext、Page 均提供显式 close；不要假定实现了 Python with 协议。
Context 关闭会关闭其页面、DevTools 和网络客户端，并清理 Blob URL。
多个 Context 的 Cookie/Blob/网络会话独立；显式指向同一磁盘缓存目录时，缓存文件不随之隔离。

## Page API

| API | 语义 |
| --- | --- |
| `context.new_page(url="about:blank", trace=None, environment=None)` | 创建页面；非 about:blank URL 会触发 goto，失败后关闭该页面 |
| `page.set_content(html, url="about:blank")` | 提交新文档并启动解析；设置 URL 不等于从该 URL 下载 HTML |
| `page.goto(url, timeout=30, wait_until="load")` | 加载并导航，通常返回 Response；beforeunload 阻止时可返回 None |
| `page.evaluate(script, filename=None)` | 在 owner 线程执行 JS 源码；filename 用于脚本定位 |
| `page.run_until_idle(timeout=0)` | 驱动就绪任务；正 timeout 允许等待定时器/异步工作，到期返回，不承诺所有工作完成 |
| `page.dispatch_event(type, *, target="window", selector=None, event_class=None, init=None)` | 宿主侧派发 trusted 事件；返回是否未被取消，详见下节 |
| `page.query_selector(selector)` | 返回宿主侧 DOM 节点句柄，未命中返回 None |
| `page.set_visibility(hidden)` | 切换文档可见性并派发 visibilitychange/ freeze / resume |
| `page.trace = True/False` | 切换该页面观察状态，需要创建时配置观察能力 |
| `page.close()` | 关闭页面及其拥有的资源 |

`goto.wait_until` 支持 `commit`、`domcontentloaded`、`load`，以及内部接受的 None；不支持 networkidle。
`set_content` 的默认 URL 是 about:blank，重复调用时需显式传 URL 才能保持预期 origin。
`evaluate` 不是自动等待 Promise 的异步求值器；可将完成结果写到 JS 全局，再推进队列并读取。

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.evaluate("globalThis.result = null; Promise.resolve(21).then(value => result = value * 2)")
    page.run_until_idle(timeout=1)
    assert page.evaluate("result") == 42
finally:
    browser.close()
```

默认 executor 自动消费事件循环；run_until_idle 是测试/等待辅助工具，并非每条语句之后都必须调用。
持续 interval 或永不结束的脚本不能被“空闲”调用可靠收敛；等待超时也不能抢占已开始的同步 Python 回调。

## 事件触发

页面加载生命周期事件由宿主按阶段派发：`readystatechange` → `DOMContentLoaded` → `load` → `pageshow`，导航与关闭时依次为 `beforeunload` → `pagehide` → `unload`；元素资源按 `loadstart` → `progress` → `load`/`error` → `loadend`，iframe 在子文档 load 后追加 `DOMFrameContentLoaded`。`hashchange`、`popstate`、`visibilitychange`、`freeze`、`resume` 由片段导航与 `set_visibility` 触发。

需要主动打事件时用 `Page.dispatch_event`，它始终在 owner 线程执行并把事件标记为 trusted：

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.set_content("<button id='go'>go</button>", url="https://example.test/")
    page.evaluate("document.querySelector('#go').onclick = e => window.__x = e.clientX")
    page.dispatch_event("click", selector="#go", init={"clientX": 7})   # 事件类按类型推断为 MouseEvent
    page.dispatch_event("custom", target="document", event_class="CustomEvent", init={"detail": {"n": 1}})
    page.set_visibility(True)
    assert page.evaluate("__x") == 7
finally:
    browser.close()
```

`target` 取 `"window"`（默认）、`"document"` 或 `query_selector` 返回的节点；`selector` 与 `target` 二选一，未命中抛 `NotFoundError`。`init` 是普通 dict，缺省时按事件类型补 `bubbles`/`cancelable` 默认值；`event_class` 可显式指定接口名，未知接口或非 `Event` 接口报 `TypeError`。

事件类本身（`MouseEvent`/`KeyboardEvent`/`PointerEvent`/`WheelEvent`/`FocusEvent`/`InputEvent`/`CompositionEvent`/`PopStateEvent`/`HashChangeEvent`/`StorageEvent`/`AnimationEvent`/`TransitionEvent`/`PromiseRejectionEvent`/`PageTransitionEvent`/`ProgressEvent` 等）只实现常用 init 成员与同名属性，其余成员仍由 stub 兜底；`BeforeUnloadEvent` 按 baseline 不可 `new`。

边界：`PromiseRejectionEvent`、`StorageEvent`、`AnimationEvent`/`TransitionEvent` 目前只提供构造与显式派发，不会自动触发（分别需要原生 Promise 拒绝回调、跨窗口 storage 广播与 CSS 动画引擎）；元素 `abort` 仅在显式取消路径可达。页面 `close()` 或导航重建文档时不会补发被取消资源的事件。

## Browser 参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| resource_loader | None | 同步 Python 资源提供函数；离线测试首选 |
| real_network | False | 为每个 Context 创建可选 cycronet 客户端 |
| cycronet_options | None | 网络客户端参数；max_workers 单独用于桥接线程池 |
| resource_interceptor | None | 主动跳过/替换请求，区别于被动 trace |
| cache / cache_dir | True / None | 允许缓存；只有显式目录才持久化资源响应 |
| environment | None | 不可变 BrowserEnvironment 或字典 |
| trace / traceable | False / False | 初始观察状态 / 是否允许后续开启 |
| trace_callback | None | 共享事件回调；未提供时使用 Loguru 输出 |
| interface_baseline_path | None | 默认随包 ZIP，可指定 compact schema 5 JSON/ZIP |
| expose_unimplemented | True | exposure 计划是否包含没有手写实现的 baseline 接口 |
| stub_logging | info | 未实现成员日志；off/none/disabled 可关闭 |

关闭 stub_logging 只关闭日志，不会让占位成员获得实现。
原生 binding 描述和已创建模板不是热更新配置；更换 baseline 或修改接口声明后应创建新的 Browser/Context。

## 环境覆盖

```python
from pyv8c import Browser

browser = Browser(environment={"languages": ["zh-CN", "en"], "viewport": {"width": 800}}, stub_logging="off")
try:
    page = browser.new_context().new_page(environment={"viewport": {"width": 390}})
    assert page.evaluate("[navigator.language, innerWidth]") == ["zh-CN", 390]
finally:
    browser.close()
```

支持 user_agent、platform、language/languages、on_line、hardware_concurrency、device_memory、max_touch_points，
以及 viewport、screen、geolocation、plugins 等结构配置。详见 [environment.py](../pyv8c/foundation/environment.py)。
配置会校验并冻结；页面覆盖基于 Browser 配置合成。几何数值是环境模拟，不代表存在布局引擎。
配置中出现 timezone 也不能推断所有原生 Date/Intl 行为已切换。

## window.chrome

`window.chrome` 按 Chromium 的形态安装：可写、可枚举、不可配置的全局属性，值是普通对象
（`Object.getPrototypeOf(chrome) === Object.prototype`、`chrome.constructor === Object`），
own key 顺序为 `loadTimes`、`csi`、`app`，没有扩展时不提供 `chrome.runtime`；Worker 全局没有 `chrome`。

```python
page.evaluate("[typeof chrome, Object.keys(chrome), typeof chrome.csi]")
# ['object', ['loadTimes', 'csi', 'app'], 'function']
page.evaluate("chrome.csi()")
# {'startE': 1789462997343.7854, 'onloadT': ..., 'pageT': ..., 'tran': 15}
```

- `chrome.csi()`：`startE`、`onloadT`、`pageT`、`tran`（正常导航为 15），每次调用返回新对象。
- `chrome.loadTimes()`：13 个字段，`navigationType` 为 `Other`，`wasFetchedViaSpdy`/`wasNpnNegotiated` 为 true、
  `npnNegotiatedProtocol`/`connectionInfo` 为 `h2`，尚未到达的里程碑为 0。
- `chrome.app`：`isInstalled`、`getDetails`、`getIsInstalled`、`installState`、`runningState`，
  以及 `InstallState`、`RunningState` 枚举。
- `chrome.csi`/`chrome.loadTimes` 是与绑定同源的 native 函数：`String(chrome.csi)` 为
  `function csi() { [native code] }`，`chrome.csi.name` 为 `csi`。
- 时间以当前文档的导航起点为锚，`goto`/`set_content` 之后会重新计时；精度高于 Windows `time.time()` 的
  15ms 刻度，连续两次 `chrome.csi().pageT` 不相等。

## 网络与资源

默认不联网；本地 HTML 用 set_content，导航或外部脚本使用明确的 loader。
真实网络需要另外安装项目的 network extra，并使用 Browser(real_network=True)。
完整 loader 契约、响应格式、拦截和缓存风险见 [资源加载](resource-interception.md)。

## DevTools

下面的片段需要人工打开打印的调试地址，不属于无人值守测试：

```python
from pyv8c import Browser, DevToolsOptions

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page, options=DevToolsOptions(port=9005))
    print(handle.url)
    input("打开调试地址后按回车继续：")
    page.evaluate("globalThis.debugValue = 42", filename="example.js")
    input("调试结束后按回车关闭：")
finally:
    browser.close()
```

统一使用 `DevToolsOptions`：host="127.0.0.1"、port=0、wait_for_debugger=False、
pause_on_start=True、startup_timeout=None。默认不阻塞业务；完成握手后，在下一段宿主脚本第一条语句暂停一次。
每个 BrowserContext 只允许一个活动服务，保留单客户端模型；Browser 直接复用 `pyv8.devtools` 核心。
Handle 提供 close、wait_for_connection、wait_for_ready 和上下文管理；不会自动打开浏览器、打印地址或接管进程信号。
`wait_for_debugger=True` 显式等待启动释放；超时抛出 TimeoutError，不执行该脚本也不关闭页面。
同端口提供 /json、/json/list、/json/version。支持 iframe/Worker 自动附加和稳定 Target ID 重连。
支持 Runtime/Debugger 和部分 Target 命令，不提供完整 Page/Network/DOM 域。
不要将没有认证隔离保证的调试端口暴露给不可信网络。
旧 `start_paused=True` 改为 `DevToolsOptions(wait_for_debugger=True)`；详情见 [DevTools](DEVTOOLS.md)。

## 使用时的限制

- 不跨线程直接访问底层 Context 或操作持有的 JSObject；使用 Page 公开入口。
- 页面/Context 关闭后不要继续使用它的 JS 引用。
- 缺失成员可能只是 stub；对关键行为写断言，而不是仅检查 typeof。
- 主动读取 trace 事件里的对象引用可能产生 JS 副作用；日志使用安全 formatter，见 [TRACE](TRACE.md)。
- 这不是敌意代码安全沙箱；同源检查只覆盖已建模的浏览器行为。
