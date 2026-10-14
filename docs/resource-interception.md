# 资源加载、拦截与缓存

[返回文档索引](README.md)。实现集中在 [platform/resources](../pyv8c/platform/resources/)，组合入口为 Page.resource_fetcher / Page.frame_resources / Page.document_resources。
资源拦截会主动改变请求；它不是只观察参数和结果的 [Trace](TRACE.md)。

## 离线 loader

默认 `real_network=True`。离线运行时设置 `real_network=False`，并为 HTTP(S) 导航、外部脚本、Fetch/XHR 等提供明确的资源来源：

```python
from pyv8c import Browser

def load(url, request):
    if url.endswith('/app.js'):
        return {"body": "globalThis.loaded = 42", "headers": {"content-type": "text/javascript"}}
    return {"status": 200, "body": "<script src='/app.js'></script>",
            "headers": {"content-type": "text/html"}}

browser = Browser(real_network=False, resource_loader=load, stub_logging="off")
try:
    page = browser.new_profile().new_page()
    response = page.goto("https://example.test/")
    assert response.status == 200
    assert page.evaluate("loaded") == 42
finally:
    browser.close()
```

loader 接收解析后的绝对 URL 和 request 字典。字段依调用路径包含 method、headers、body、mode、credentials、cache、timeout、resource_type。
resource_type 常见 document、script、style、image、media、iframe、fetch、xhr；不要假定每条路径都有所有字段。
框架会在缺失时补 User-Agent 和 Accept-Language。

返回值可为 Response、包含 body/status/headers 的字典，或普通值。
字典响应的 URL 被设为请求目标；若需要显式最终 URL，请返回 Response。
普通值走 str 转换，字节内容应放到字典的 body 或 Response 中，避免被转换成 Python bytes 的字符串表示。

返回 301/302/303/307/308 且带 `location` 时由 pyv8c 逐跳跟随，每一跳都重新调用 loader：
301/302 把 POST 改成 GET 并丢弃 body，303 把除 HEAD 外的方法都改成 GET，307/308 保持方法与 body，
最多跟随 20 跳。只有最终响应会被交付与缓存，`Response.url` 是最终 URL、`Response.redirected` 为 true。
`location` 缺失或为空时原样交付该响应，无法解析为 http(s) URL 时报网络错误。

loader 默认在页面 owner 线程上执行，所以它可以在等请求的同时看页面（在 loader 里 page.evaluate 采样 DOM 是支持的）。
`Browser(threaded_loaders=True)` 改为在 Browser 的有界线程池上执行：被阻塞的 loader 不再冻住 realm，
`goto` 的等待超时也终于能生效。代价是这种回调不得触碰页面——`page.evaluate`、读取页面状态都不行——
因为它已经不在 owner 线程上了。两种模式下完成通知都经任务队列回到 realm。

## 请求顺序

公共入口先检查关闭状态、解析 URL、补环境请求头，再执行资源拦截和磁盘缓存查找。
之后处理本地 `about:blank`、`data:`、`blob:` 和允许的扩展资源，再选择注入 loader 或真实网络。同步与异步入口使用同一来源选择和逐跳策略。
同时配置 loader 与真实网络时，loader 优先，不会自动因其返回空值而回退到网络。

异步完成通知通过任务队列回到 realm。LoadHandle、ResourceScope 与导航 generation 用于取消、清理和丢弃过期结果；
导航代际保护不等于能够强制终止已经运行的任意 Python 函数（`threaded_loaders` 下它在池线程上跑，至少不再挡住 realm）。

HTTP 与 loader 响应使用相同的逐跳 CORS、`same-origin` 和重定向检查。检查使用请求发起时的真实 Realm origin；初始 iframe 的继承 origin 与 sandbox 的 opaque origin 不由页面 URL 代替。
Blob 读取检查创建者 origin；共享 Profile 不授予跨源读取权限。Blob 文档导航仍由 iframe/导航生命周期处理。
显式 `mode="no-cors"` 的跨域 fetch 返回 opaque 响应，元素自身的经典脚本、样式和图片加载仍可消费资源内容。导航使用 navigate 模式。

## 实现与生命周期

| 文件 | 职责与状态归属 |
| --- | --- |
| `resources/ownership.py` | Page 的组合入口；Web API 仍只使用 ResourceServices |
| `resources/fetcher.py` | 请求快照、来源选择、同步/异步驱动和共享跳转决策 |
| `resources/callbacks.py` | host 回调执行、池提交失败和 loader 计数清理 |
| `resources/cache.py` | Profile 所属缓存；读写普通 payload，不持有 Realm Response |
| `resources/handles.py` | 原子取消/完成与 transport 绑定；取消后的迟到绑定立即取消 |
| `resources/documents.py` | 文档资源记录、加载屏障、事件及导航代际 |
| `resources/frames.py` | 初始文档、srcdoc、子 Page 生命周期和完成通知 |
| `resources/local.py` / `resources/request_headers.py` | 本地解析和请求头生成 |

传输结束不等于文档资源完成：iframe 的条目持续占用加载屏障，直到匹配的子文档完成。过期 token、导航代次或子文档身份不会派发完成事件。
取消和交付都会释放 ResourceScope 登记；取消后不再继续跳转、写缓存或向页面交付结果。不能中断的池回调可在后台结束，其结果会被丢弃。

## 行为迁移

公开调用签名及 loader/interceptor 字典格式保持不变。内部导入改为 `platform.resources.fetcher.ResourceFetchController` 和 `platform.resources.handles.LoadHandle`；旧的 `platform/resource_loading.py` 已移除，不提供兼容转发。
原同步路径的以下隐式行为已收敛：本地 URL 不再交给 loader；自定义 loader 不再绕过 CORS；`same-origin` 请求跨源时不会发送下一跳。
需要向本地 URL 注入替代内容时仍可使用 interceptor。测试 Worker 应先设置有效的同源文档地址，例如：

```python
page.set_content("<body></body>", url="https://example.test/")
page.evaluate("new Worker('/worker.js')")
```

维护内部代码时，请求头辅助函数从 `pyv8c.platform.resources.request_headers` 导入；不要读取 controller 的内部资源字典，使用 `has_pending_document_resources` 和 `outstanding()`。

## 主动拦截

```python
from pyv8c import Browser

def intercept(url, request):
    if request.get("resource_type") in {"image", "style"}:
        return False
    return None

browser = Browser(resource_interceptor=intercept, resource_loader=lambda url, request: {"body": ""})
try:
    page = browser.new_profile().new_page()
    page.goto("https://example.test/")
finally:
    browser.close()
```

| 返回值 | 行为 |
| --- | --- |
| None / True | 继续原请求路径 |
| False | 跳过下载，返回状态 200、空 body、text/plain 的响应；不是网络错误 |
| Response / 字典 / 其他值 | 按 loader 返回格式转换成替代响应 |

interceptor 接收 request 的副本；仅修改副本不是受支持的请求重写协议。
替代响应直接返回，不自动经历后续 transport、缓存写入或全部响应策略。编写拦截器时自行承担所替代行为的正确性。

## 真实网络

安装项目可选依赖后显式开启：

```powershell
python -m pip install -e ".[network]"
```

默认联网（HTTP=curl_cffi、WebSocket=websockets，见 [Browser 参数](USAGE.md#browser-参数)），Browser(real_network=False) 表示不装配引擎。每个 Profile 拥有自己的网络栈和 CookieJar。
两个角色可用 http_engine / ws_engine 分别替换，http_options / ws_options 传该引擎的参数：引擎不认识的键（拼错，或属于另一个角色的引擎）会在创建 profile 时报错，不会被静默转交或丢弃；缺少依赖同样在创建 profile 时失败，不会静默伪装联网成功。
选引擎、参数含义、校验规则、握手与自定义引擎见 [网络引擎与参数](network-engines.md)。
网络实现见 [registry.py](../pyv8c/network/registry.py) 与 [backend.py](../pyv8c/network/backend.py)，响应策略包含基础同源/CORS/no-cors 处理，但不是完整 Fetch/CORS 协议栈。
不要假定拦截响应、缓存命中和所有同步 loader 路径都重新执行相同的安全策略。
跟随由资源层完成（见 [redirects.py](../pyv8c/network/redirects.py)），引擎一律被要求不自己跟随；逐跳 CORS 只在该路径本来就会执行响应策略时进行，
因此同步 loader 路径既不重跑策略，也不逐跳检查。重定向链的 `redirect: "manual"` 交付 opaque redirect（status 0、type opaqueredirect、url 为请求 URL），`redirect: "error"` 与跳数超限都是网络错误。

## chrome-extension:

`http`/`https`/`about`/`blob`/`data` 以外，页面发出的加载在资源层就被拒绝，不会上网——`chrome-extension:`、`devtools:` 这类 scheme 属于浏览器自己。

例外是**已安装且正在运行**的扩展向页面公开的资源：环境键 `extensions` 记录 `id -> [{resources, matches}]`（就是 Chrome 自己的 `web_accessible_resources` 形状），资源层据此本地回答。命中时 `type` 为 `basic`（Chrome 对这类资源免 CORS，`fetch`/XHR 与元素加载都拿得到），且不经过拦截器之外的任何网络路径；未声明的 id、未公开的路径、`matches` 未点名当前来源、以及被暂停（`disable_reasons` 非空）的扩展一律照常失败——这与 Chrome 实测一致。

```python
browser = Browser(environment={"extensions": {
    "cneomjecgakdfoeehmmmoiklncdiodmh": [
        {"resources": ["/icons/icon_64.png", "/_locales/*/messages.json"],
         "matches": ["http://*/*", "https://*/*"]},
    ],
}})
```

档案里没有文件的**字节**：命中时按路径后缀给一份同类型的占位内容（图片 1×1 PNG、SVG 16×16、文本为空）。所以"取不取得到"与 Chrome 一致，`naturalWidth` 之类的**具体尺寸**不保证一致；要完全一致得把文件本身带进档案。

## 磁盘缓存

仅 Browser(cache=True, cache_dir="./.pyv8c-cache") 会写入资源响应缓存；默认 cache_dir=None 没有持久响应缓存。
该缓存与 baseline 的 JSON/pickle 编译缓存完全不同。

- GET/HEAD 可缓存；请求 cache 为 no-store、reload、no-cache 时绕过。
- 缓存键包含 method、URL、mode、credentials、发起源和规范化请求头；目录按目标域名/非默认端口组织，文件名为键摘要。
- 复用命中时输出 Loguru DEBUG 的 resource cache hit 日志。
- 响应 no-store、no-cache、private、Set-Cookie、Vary: * 和非 2xx 状态不写入；仍没有完整 HTTP freshness、ETag/304 和重验证语义。
- 重定向链只缓存最终响应，键仍是原始请求的 method/URL/mode/credentials；中间跳不单独入缓存，与 Chrome 的逐跳缓存不同。
- 请求携带 Authorization、Cookie 或 CookieJar 中存在匹配 Cookie 时绕过缓存。同一目录仍可被多个 profile/Browser 共享；旧版缓存键与新版不同，旧记录不会被新版命中。
- 处理鉴权、敏感或依赖 Cookie 的内容时关闭缓存，或使用隔离目录并自行控制可缓存资源。

cache=False 关闭响应缓存使用；Browser.close 不删除显式持久目录。

## 回归入口

资源驱动和生命周期契约见 [专项测试](../tests/integration/test_resource_loading.py)，loader/真实 HTTP 的请求模式与本地来源见 [Chrome 对照](../tests/repros/test_resource_loading_vs_chrome.py)。

[导航测试](../tests/integration/test_navigation.py)、[网络后端测试](../tests/unit/test_network_backend.py)、
[生命周期测试](../tests/integration/test_lifecycle.py)、[竞态回归](../tests/integration/test_race_regressions.py)、
[扩展资源测试](../tests/integration/test_extension_resources.py)。
重定向的规则表见 [单元用例](../tests/unit/test_redirect_rules.py)，loader 驱动的完整行为见 [集成用例](../tests/integration/test_redirects.py)，
与本机 Chrome 的逐条对照见 [对照用例](../tests/repros/test_redirects_vs_chrome.py)。
实际 Cookie 行为见 [cookies.py](../pyv8c/network/cookies.py) 与 [document.cookie 测试](../tests/browser/test_document_cookie.py)。


### Round 2 internal migration

```python
# Was page.resources.load_resource(...)
page.resource_fetcher.load_resource(url, options)
# Was page.resources.ensure_iframe_context(...)
page.frame_resources.ensure_iframe_context(element)
# Was page.resources.discover_element_resource(...)
page.document_resources.discover_element_resource(element)
```

ResourceServices remains the Web API boundary. ResourceOwnership registers one cleanup callback in ResourceScope. Its tickets distinguish ownership release from result delivery; document tickets survive transfer completion until element completion. Closed/obsolete synchronous owners raise LoadFailure; asynchronous owners cancel and stop delivery. Profile always owns a CookieJar.

Discovery consumes immutable owner-thread snapshots. The tracker owns typed document records, generations and child-document identity checks. Completion owns element effects and events. _AsyncLoad drives asynchronous requests; HopState is shared with the blocking driver. Neither driver waits for its own task queue. No RequestHandle alias or ResourceController compatibility facade is provided.
