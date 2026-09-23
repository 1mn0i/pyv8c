# 使用与配置

[返回文档索引](README.md)。本页对应 [Browser 实现](../pyv8c/platform/browser.py) 与 [Page 实现](../pyv8c/platform/page.py)，不假定与 Playwright 或 Chrome API 等价。

顶层只导出对象模型（`Browser`、`BrowserContext`、`Page`、`DevToolsHandle`、`DevToolsOptions`、`BrowserEnvironment`）、
可捕获的错误（`V8Error` 家族、`DOMException`、`NetworkUnavailableError`）与网络扩展点（`NetworkBackend`、
`HttpTransport`/`WebSocketTransport`、`register_transport`/`register_ws_transport`/`transport_names`、
`NetworkRequest`/`NetworkResponse`）。引擎实现类从各自模块导入，例如 `pyv8c.network.cronet.CronetTransport`。

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
| `page.set_content(html=空 HTML 文档, url="about:blank")` | 提交新文档并启动解析；省略 html 得到 `<!DOCTYPE html>` 开头的空 standards 文档；设置 URL 不等于从该 URL 下载 HTML |
| `page.goto(url, timeout=30, wait_until="load")` | 加载并导航，通常返回 Response；beforeunload 阻止时可返回 None |
| `page.evaluate(script, filename=None)` | 在 owner 线程执行 JS 源码；filename 用于脚本定位 |
| `page.run_until_idle(timeout=0)` | 驱动就绪任务；正 timeout 允许等待定时器/异步工作，到期返回，不承诺所有工作完成 |
| `page.dispatch_event(type, *, target="window", selector=None, event_class=None, init=None)` | 宿主侧派发 trusted 事件；返回是否未被取消，详见下节 |
| `page.query_selector(selector)` | 返回宿主侧 DOM 节点句柄，未命中返回 None |
| `page.set_visibility(hidden)` | 切换文档可见性并派发 visibilitychange/ freeze / resume |
| `page.trace = True/False` | 切换该页面观察状态，需要创建时配置观察能力 |
| `page.fetch(url, *, method="GET", headers=None, body=None, credentials=None, timeout=30)` | 用页面自己的 `fetch` 发请求并等它落定；返回快照对象（见下节） |
| `page.fetch_async(...)` | 同上，返回 `concurrent.futures.Future`，不阻塞页面 |
| `page.xhr(url, ...)` / `page.xhr_async(...)` | 用页面自己的 `XMLHttpRequest`，同样分别返回快照对象 / `Future` |
| `page.close()` | 关闭页面及其拥有的资源 |

`goto.wait_until` 支持 `commit`、`domcontentloaded`、`load`，以及内部接受的 None；不支持 networkidle。
`set_content` 的默认 URL 是 about:blank，重复调用时需显式传 URL 才能保持预期 origin。
省略 html 得到一份空 standards 文档（`document.compatMode` 为 `CSS1Compat`）；传 `"<body></body>"` 这类不带 doctype 的片段则是 `BackCompat`，按需选择。
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

## 文档写入

解析期调用 `document.write`（或 `writeln`）时，文本按**插入点**进入解析器：元素落在写脚本所在的位置，写入的 `<script>`
与文档中其他解析器插入脚本一样阻塞解析并执行，`writeln` 额外追加一个换行。

解析结束后再写入等价于 `document.open()`：当前文档内容被清空，写入的标记成为新内容，并在新的 `body` 内解析；
`Document` 对象、URL 与 `readyState` 都保持不变，导航生命周期（`unload`/`DOMContentLoaded`/`load`）不会重新派发。
没有浏览上下文的文档没有解析器，写入不产生效果。

## Browser 参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| resource_loader | None | 同步 Python 资源提供函数；离线测试首选 |
| real_network | True | 是否装配 http_engine / ws_engine 指定的引擎；False 表示这个 Browser 不联网 |
| http_engine / ws_engine | curl_cffi / websockets | 各给一个引擎：已注册的名字、现成对象（HTTP 要有 request_sync，socket 要有 websocket）或工厂（每个 Context 调一次）；None 表示该角色不装配 |
| http_options / ws_options | None / None | 分别交给两个引擎的参数；引擎不认识的键在创建 Context 时报错 |
| resource_interceptor | None | 主动跳过/替换请求，区别于被动 trace |
| cache / cache_dir | True / None | 允许缓存；只有显式目录才持久化资源响应 |
| environment | None | 扁平点号键字典或不可变 BrowserEnvironment |
| trace / traceable | False / False | 初始观察状态 / 是否允许后续开启 |
| trace_callback | None | 共享事件回调；未提供时使用 Loguru 输出 |
| interface_baseline_path | None | 默认随包 ZIP，可指定 compact schema 5 JSON/ZIP |
| baseline_mismatch | warn | warn（默认）对实现与所选 baseline 的成员/接口差异记一次警告并跳过；error 改为当场失败，用来盯住自己的实现写错 |
| expose_unimplemented | True | exposure 计划是否包含没有手写实现的 baseline 接口 |
| stub_logging | off | 未实现成员日志：off 关闭（默认）、on 每个未实现成员报一次、verbose 额外报告占位 handler |

关闭 stub_logging 只关闭日志，不会让占位成员获得实现。
原生 binding 描述和已创建模板不是热更新配置；更换 baseline 或修改接口声明后应创建新的 Browser/Context。

## 环境配置

环境是一份扁平的「点号键」配置：每个设置一个键（`viewport.width`、`client_hints.brands` …），
一份配置可以整体写出、读回、叠加，没有第二套嵌套记录类型。字段表见
[environment.py](../pyv8c/foundation/environment.py) 的 `FIELDS`，校验集中在构造时，报错会指明键名。

```python
from pyv8c import Browser, BrowserEnvironment

environment = BrowserEnvironment({
    "languages": ["zh-CN", "en"],
    "viewport.width": 800,
    "screen.width": 2560,
})
browser = Browser(environment=environment, stub_logging="off")
try:
    page = browser.new_context().new_page(environment={"viewport.width": 390})
    assert page.evaluate("[navigator.language, innerWidth]") == ["zh-CN", 390]
finally:
    browser.close()
```

| 入口 | 语义 |
| --- | --- |
| `BrowserEnvironment(data)` / `from_dict(data)` | 逐键校验；未知键、类型或取值不符时抛错并指明键名 |
| `overlay(data)` | 在自身之上叠加若干键；页面级覆盖走同一条路径 |
| `to_dict()` | 只含显式给出的键 |
| `resolved()` | 默认值与推导补齐后的完整键集 |
| `dumps()` / `loads()`、`to_file()` / `from_file()` | 带 `schema` 版本号的文本或文件往返；版本不符即报错 |
| `viewport` `screen` `geolocation` `connection` `battery` `storage` `client_hints` `plugins` | 分组视图，属性名与 Web API 取值一致 |

未给出的键取**随包的默认 profile**（`pyv8c/data/chrome-environment.json`，一份真实 Chrome 的采集结果），
其中 `app_version`、`language`、`client_hints.brands`/`mobile`/`platform` 会**跟随 `user_agent`** 重新推导，
所以换 `user_agent` 时这些值一起变；推导结果不写回显式集合，要钉住某一项就显式给出它。
其余高熵值（架构、位数、平台版本、完整版本列表）无法从 UA 推出，仍取 profile 或显式值。

分组视图的读法：`environment.storage.temporary.quota`、`environment.client_hints.platform`、
`environment.plugins[0].mime_types[0].type`。

## 从真实浏览器采集配置

`pyv8c/tools/collect_environment.py` 用调试端口启动本机已装的 Chrome（`PYV8C_CHROME` 可指向别的可执行文件），
在 localhost 这个安全源上读取浏览器自己能上报的每一项，写出可直接加载的配置；
`tools/collect_environment.py` 是它的命令行壳：

```powershell
$env:PYTHONPATH = 'D:\src\pyv8env\pyv8c'
python tools/collect_environment.py                       # chrome-<版本>-<平台>.json
python tools/collect_environment.py --out -               # 写到标准输出
python tools/collect_environment.py --headed --window-size 1920,1080 --accept-lang en-US,en
```

- 产物只含**真实读到**的键（字段表中 `collected=True` 的那批）：缺一个即报错并以非零码退出，不会静默回落默认值。
- 浏览器无法上报的项不进产物，只作为覆盖项存在：`geolocation.*`（权限与坐标）、`viewport.mobile`、
  `viewport.touch`、`connection.type`、`connection.downlink_max`、`storage.persistent.*`。
- 产物带 `schema` 与 `source`（浏览器版本、可执行文件、平台、采集时间、启动参数），
  `BrowserEnvironment.from_file(path)` 可直接载入。
- 默认无头启动，UA 因而会是 `HeadlessChrome/…`；要正常 UA 加 `--headed`。
- 调试端口本身会让 `navigator.webdriver` 为真；采集器用 `--disable-blink-features=AutomationControlled`
  中和这项由自己引入的痕迹，该参数同样记录在 `source.arguments` 中，便于复核。
- `screen.*`、`viewport.*` 是采集那一刻显示器与窗口的值，`storage.*.quota` 随 profile 与磁盘变化。

`pyv8c/data/chrome-environment.json` 是随包的默认 profile，也是一份真实采集的样例，离线测试直接用它。

配置会校验并冻结；几何数值是环境模拟，不代表存在布局引擎。
配置中出现 `timezone` 也不能推断所有原生 Date/Intl 行为已切换。

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

`page.fetch` / `page.xhr` 在页面里调用 JS 的 `fetch` / `XMLHttpRequest`：命中**页面重写后**的实现（埋点、测试库常见），请求走页面的资源栈（CORS、拦截器、缓存），返回宿主侧快照对象（`status`、`status_text`、`url`、`headers`、`body`，以及 `.ok`、`.text`、`.json()`）；XHR 那份的 `headers` 为空，因为该实现没有 `getAllResponseHeaders`。
`page.net.*` 相反：直接用 context 的传输，不经 JS、不做 CORS。需要哪种语义就选哪条路。
默认联网：`Browser()` 为每个 context 装配 curl_cffi（HTTP）与 websockets（WebSocket），两者来自 `[network]` extra，缺失时在创建 Context 时报错并指明要装什么。
离线测试用 `Browser(real_network=False)` 配 resource_loader，或直接用 set_content 提交本地 HTML。
http_engine / ws_engine 可改选其它已注册引擎（如 `"cronet"`，需装 `[cronet]` extra），也可直接给现成引擎对象或工厂，或传 None 关掉某个角色；http_options / ws_options 只对"按名字"的引擎生效，给现成对象时传会被拒绝。
选引擎、参数含义、校验规则和自定义引擎见 [网络引擎与参数](network-engines.md)；完整 loader 契约、响应格式、拦截和缓存风险见 [资源加载与拦截](resource-interception.md)。

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
