# pyv8c

## ???????

`Browser` ???????????????????? `BrowserContext` ?? GET/HEAD ???

```python
def intercept(url, request):
    if request.get("resource_type") in {"image", "style"}:
        return False
    return None

browser = Browser(
    real_network=True,
    resource_interceptor=intercept,
    cache=True,
)
```

????? `None` ? `True` ???????? `False` ????????????????? `Response`????????????????????? `document`?`script`?`style`?`image`?`media`?`iframe` ? `fetch`?

???????????????????????? `Browser` ??????????????????`Browser(cache=True, cache_dir="./.pyv8c-cache")`??? `Browser(cache=False)` ?????????? `cache` ? `no-store`?`reload` ? `no-cache` ????????????? `doc/resource-interception.md`?

`pyv8c` 是构建在相邻 [`_pyv8`](../_pyv8) V8 Python 扩展之上的 Chromium-like
BOM/DOM 运行时。它面向页面脚本执行、Web IDL 兼容性测试、DOM 行为验证和 V8 Inspector
调试，不是完整的浏览器实现。

项目当前覆盖：

- DOM 树、HTML parser、selector、事件传播和页面生命周期
- Window、Document、Location、History、Storage、URL、Navigator 和定时器
- Web IDL 驱动的接口、原型链、descriptor、类型校验和 lazy materialization
- fetch、XHR、Request/Response/Headers、Blob、Blob URL、Cookie、CORS 基础行为
- iframe、Worker、structured clone 和 WebSocket
- 基于 `_pyv8.Debugger` 的 V8 Inspector、Runtime/Debugger 和基础 Target 路由

布局、渲染、CSSOM、浏览器多进程模型、完整网络栈和全部 WHATWG/Chromium 细节不在项目
范围内。

## 快速开始

要求 Python 3.10 或更高版本，并安装与当前 Python 版本匹配的 `_pyv8` 扩展。

```powershell
python -m pip install -e .
```

如果需要真实 HTTP/WebSocket 后端：

```powershell
python -m pip install -e ".[network]"
```

最小页面示例：

```python
from pyv8c import Browser

browser = Browser()
context = browser.new_context()
page = context.new_page()

page.set_content("<main id='app'>ready</main>")
assert page.evaluate("document.querySelector('#app').textContent") == "ready"

browser.close()
```

`Browser -> BrowserContext -> Page` 是当前生命周期模型。Context 隔离页面、Cookie、Blob
URL、网络会话和 DevTools 会话；页面关闭后不可复用。

## 使用注入的资源加载器

默认不访问外部网络。可以通过 `resource_loader(url, request)` 提供确定性的离线资源：

```python
from pyv8c import Browser, Response

def load(url, request):
    if url.endswith("/index.html"):
        return Response(
            "<main id='app'>loaded</main><script src='/app.js'></script>",
            {"url": url, "headers": {"content-type": "text/html"}},
        )
    if url.endswith("/app.js"):
        return Response(
            "document.querySelector('#app').dataset.ready = 'yes'",
            {"url": url},
        )
    return Response("not found", {"url": url, "status": 404})

browser = Browser(resource_loader=load)
page = browser.new_context().new_page()
page.goto("http://example.test/index.html", wait_until="load")
assert page.evaluate("document.querySelector('#app').dataset.ready") == "yes"
browser.close()
```

`Page.goto(url, timeout=30, wait_until="load")` 支持 `commit`、`domcontentloaded` 和
`load`。`new_page(url)` 会自动导航到非 `about:blank` 地址。自定义同步 loader 如果自身
阻塞，不能被 Python 强制抢占；真实网络请求使用异步 transport 和取消句柄。

## 真实网络

安装可选依赖后，使用 `real_network=True` 启用 Context 级 cyCronet 后端：

```python
from pyv8c import Browser

browser = Browser(real_network=True, cycronet_options={"max_workers": 8})
page = browser.new_context().new_page("https://example.com/")
print(page.evaluate("document.readyState"))
browser.close()
```

若同时提供 `resource_loader`，注入 loader 优先于真实网络。未配置任何加载方式时，只有
`about:blank` 和当前文档可读取。

## DevTools 调试

`BrowserContext.start_devtools()` 会启动只监听 loopback 的 V8 Inspector WebSocket，并返回
`DevToolsHandle`。将 `handle.url` 粘贴到 Chrome 地址栏即可打开 DevTools：

```python
from pyv8c import Browser

browser = Browser()
context = browser.new_context()
page = context.new_page()
page.set_content("<script>globalThis.answer = 40</script>")

handle = context.start_devtools(page)
print(handle.url)
# 在 Chrome 中打开上面的 devtools:// 地址

handle.close()
browser.close()
```

句柄提供以下接口：

```python
handle.url
handle.host
handle.port
handle.wait_for_connection(timeout=None)
handle.wait(timeout=None)
handle.close()
```

需要在首个脚本执行前连接时，使用启动暂停：

```python
handle = context.start_devtools(
    page,
    start_paused=True,
    startup_timeout=60,
)
print(handle.url)
page.goto("http://127.0.0.1:8000/index.html")
```

页面会等待 Chrome 发送 `Runtime.runIfWaitingForDebugger`，随后在首条可暂停语句处进入
Inspector。命令通过目标 Realm 的 `RealmExecutor` 投递，暂停期间仍可执行 `resume`、
单步和 `Runtime.evaluate`。

当前 DevTools 约束：

- 一个 `BrowserContext` 同时只能有一个调试句柄。
- 一个句柄只接受一个 WebSocket 客户端，第二个客户端以 close code `1013` 关闭。
- 主要支持 `Target`、`Runtime` 和 `Debugger`；未实现的 CDP 域返回 `-32601`。
- 调试范围是指定 Page 及其 iframe、Worker 后代，不包含 Context 中其他独立 Page。
- 默认监听 `127.0.0.1`，不提供认证和 TLS。

## 运行时架构

```text
foundation -> engine -> web -> platform
```

- `foundation`：错误、环境配置、Realm capability 协议、任务队列和通用索引属性。
- `engine`：Chromium baseline、Web IDL、接口 registry、stub、wrapper 和 class-config。
- `web`：DOM、BOM、Fetch、Network、Worker 等面向脚本的对象。
- `platform`：Browser/Context/Page 组合根、Realm 装配、导航、生命周期、资源和 DevTools。

每个 Page、iframe 和 Worker 都有独立 V8 Context、BindingSession 和 owner executor。所有
V8 操作必须在所属 Realm 的执行线程完成；网络和 Worker 回调先进入任务队列，再进入 V8。
PageServices 以六个窄能力 facet（Realm、Task、Resource、Document、Worker、Context）向
Web 层注入能力，禁止 Web 层持有 Page 或全局 current Page。

页面、请求、iframe、Worker、WebSocket、DevTools target 和 TaskQueue 使用幂等的
`OPEN -> CLOSING -> CLOSED` 生命周期。资源请求在完成或取消后从 `ResourceScope` 反注册。

## 重要行为

### `document.all`

`document.all` 是 live 的 `HTMLAllCollection`：

```text
typeof document.all                          -> "undefined"
Object.prototype.toString.call(document.all) -> "[object HTMLAllCollection]"
document.all[0] / document.all.item(0)        -> 按文档树顺序访问
document.all.app / namedItem("app")            -> 按 id/name 查找
document.all("app")                            -> legacy caller
```

严格模式下对不支持的 indexed/named 属性写入会抛出 Chromium 风格的 `TypeError`。

### `document.cookie`

Cookie 状态属于 `BrowserContext`，同一 Context 的页面共享 Cookie，不同 Context 相互隔离。
支持脚本读写、域/路径匹配、Secure、HttpOnly、Max-Age、Expires、同名更新和删除；状态发生
变化时使用 `logger.debug` 记录当前页面可见的 cookie 值。非 HTTP(S) 的不透明 URL（例如
`about:blank`）不会读写脚本 Cookie。

### Window / iframe 同源策略

Window 访问检查自动启用，无需调用底层 Context API。同源按 scheme、规范化 hostname、有效端口比较；
about:blank iframe 继承父页面 origin。跨源敏感属性、未知属性和非允许写入抛出真正的
DOMException（name="SecurityError"、code=18）。跨源 location 允许导航写入，不允许读取 URL。

iframe 的 top/parent/self/window 保持稳定身份；同源访问转发到实际子页面，导航后重新检查 origin。
postMessage 支持异步消息、targetOrigin 检查以及 origin/source。详细白名单和兼容性边界见
[Window security](doc/WINDOW_SECURITY.md)。这是浏览器行为兼容层，不是运行敌意代码的安全沙箱，
也不宣称完整一比一复刻 Chrome。

## 配置与启动性能

`BrowserEnvironment` 可配置 User-Agent、语言、viewport、screen、geolocation、plugins
和 MIME types，并由 iframe/Worker 继承。可通过 `Browser(environment=...)` 或
`new_page(environment=...)` 设置。

默认只 eager 物化核心接口，其余接口首次访问时 lazy materialize：

```python
browser = Browser(lazy_interfaces=False)  # 全量物化
```

调试或 CI 可使用：

```powershell
$env:PYV8C_STRICT_VALIDATION = "1"
$env:PYV8C_STARTUP_STATS = "0"
```

## 构建 `_pyv8`

`_pyv8` 是独立 Git 仓库。Windows 本地重建扩展：

```powershell
cd D:\src\pyv8env\_pyv8
python310 setup.py build_ext --inplace --force
```

`.pyd` 必须与运行时 Python 版本和 ABI 匹配，不能把 Python 3.12 构建的扩展直接用于
Python 3.10/3.11。

## 测试

在 `pyv8` 可导入的环境中运行：

```powershell
cd D:\src\pyv8env\pyv8c
$env:PYTHONPATH = "D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c"
python -m pytest -q
```

测试按 `tests/browser`、`tests/integration`、`tests/unit` 和 `tests/native` 分层，分别
覆盖 JavaScript 可观察行为、跨模块生命周期、架构约束和 native smoke。另有四类定向覆盖：

- 并发/竞态回归（标记 `race`）：DevTools owner 线程断连、事件循环丢失唤醒、`post()`
  异常出口、资源登记顺序，用 `pytest -m race --count 20` 重复运行防回潮；
- 生命周期压力与 DevTools 暂停/恢复；
- binding 快照隔离与 realm 线程泄漏基线断言。

Window 安全回归位于 `tests/integration/test_window_security.py`；原生访问检查回归位于
独立 `_pyv8` 仓库的 `tests/test_window_access_checks.py`。请以当前运行的 pytest 结果为准。
lint/unit 阶段不需要原生扩展；完整 `full` 阶段依赖能编译 `_pyv8` 的工具链。

## 当前限制

- iframe 已实现基础 WindowProxy/origin 访问策略；完整导航 realm 更换、document.domain、
  sandbox、COOP/COEP、site isolation 等仍不支持，详见 Window security 文档。
- `requestAnimationFrame` 尚未使用独立 frame clock。
- 完整网络策略、缓存、渲染和浏览器级 CDP `Page`/`Network` 域尚未实现。
- Worker 仍保留独立 Isolate 和专用生命周期路径，需要持续进行 Windows Debug/ASAN 压力
  验证。

更多当前架构事实与后续重点见 [`HANDOFF.md`](HANDOFF.md)，历史改动按周记录在
[`WORKLOG.md`](WORKLOG.md)。
