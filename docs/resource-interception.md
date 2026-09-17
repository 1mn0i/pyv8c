# 资源加载、拦截与缓存

[返回文档索引](README.md)。实现入口：[ResourceController](../pyv8c/platform/resource_loading.py)。
资源拦截会主动改变请求；它不是只观察参数和结果的 [Trace](TRACE.md)。

## 离线 loader

默认不访问外网。为导航、外部脚本、Fetch/XHR 等提供明确的资源来源：

```python
from pyv8c import Browser

def load(url, request):
    if url.endswith('/app.js'):
        return {"body": "globalThis.loaded = 42", "headers": {"content-type": "text/javascript"}}
    return {"status": 200, "body": "<script src='/app.js'></script>",
            "headers": {"content-type": "text/html"}}

browser = Browser(resource_loader=load, stub_logging="off")
try:
    page = browser.new_context().new_page()
    response = page.goto("https://example.test/")
    assert response.status == 200
    assert page.evaluate("loaded") == 42
finally:
    browser.close()
```

loader 接收解析后的绝对 URL 和 request 字典。字段依调用路径包含 method、headers、body、mode、credentials、cache、timeout、resource_type。
resource_type 常见 document、script、style、image、media、iframe、fetch；不要假定每条路径都有所有字段。
框架会在缺失时补 User-Agent 和 Accept-Language。

返回值可为 Response、包含 body/status/headers 的字典，或普通值。
字典响应的 URL 被设为请求目标；若需要显式最终 URL，请返回 Response。
普通值走 str 转换，字节内容应放到字典的 body 或 Response 中，避免被转换成 Python bytes 的字符串表示。

loader 本身同步执行。load_resource_async 只把完成通知放入队列，不会自动把自定义 loader 搬到后台线程。
因此长时间阻塞的 Python loader 不能被 goto 的等待超时强制抢占。

## 请求顺序

公共入口先检查关闭状态、解析 URL、补环境请求头，再执行资源拦截和磁盘缓存查找。
之后处理本地 about:blank/Blob URL、注入 loader 或真实网络；同步与异步路径对 about:blank 的位置有细微区别。
同时配置 loader 与真实网络时，loader 优先，不会自动因其返回空值而回退到网络。

异步完成通知通过任务队列回到 realm。RequestHandle、ResourceScope 与导航 generation 用于取消、清理和丢弃过期结果；
导航代际保护不等于能够强制终止已经运行的任意 Python 函数。

## 主动拦截

```python
from pyv8c import Browser

def intercept(url, request):
    if request.get("resource_type") in {"image", "style"}:
        return False
    return None

browser = Browser(resource_interceptor=intercept, resource_loader=lambda url, request: {"body": ""})
try:
    page = browser.new_context().new_page()
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

使用 Browser(real_network=True)。每个 BrowserContext 拥有自己的网络栈（默认 HTTP=curl_cffi、WebSocket=websockets）和 CookieJar。
两个角色可用 network_engine / ws_engine 分别替换，http_options / ws_options 传该引擎的参数：引擎不认识的键（拼错，或属于另一个角色的引擎）会在创建 Context 时报错，不会被静默转交或丢弃；缺少依赖同样在创建 Context 时失败，不会静默伪装联网成功。
选引擎、参数含义、校验规则、握手与自定义引擎见 [网络引擎与参数](network-engines.md)。
网络实现见 [registry.py](../pyv8c/web/network/registry.py) 与 [backend.py](../pyv8c/web/network/backend.py)，响应策略包含基础同源/CORS/no-cors 处理，但不是完整 Fetch/CORS 协议栈。
不要假定拦截响应、缓存命中和所有同步 loader 路径都重新执行相同的安全策略。

## 磁盘缓存

仅 Browser(cache=True, cache_dir="./.pyv8c-cache") 会写入资源响应缓存；默认 cache_dir=None 没有持久响应缓存。
该缓存与 baseline 的 JSON/pickle 编译缓存完全不同。

- GET/HEAD 可缓存；请求 cache 为 no-store、reload、no-cache 时绕过。
- 缓存键包含 method、URL、mode、credentials；目录按目标域名/非默认端口组织，文件名为键摘要。
- 复用命中时输出 Loguru DEBUG 的 resource cache hit 日志。
- 没有完整 HTTP freshness、ETag/304、Cache-Control、Vary 等缓存语义保证。
- 键不包含 Cookie、Authorization 或完整请求头。同一目录可能被多个 Context/Browser 共享，私有响应存在串用风险。
- 处理鉴权、敏感或依赖 Cookie 的内容时关闭缓存，或使用隔离目录并自行控制可缓存资源。

cache=False 关闭响应缓存使用；Browser.close 不删除显式持久目录。

## 回归入口

[导航测试](../tests/integration/test_navigation.py)、[网络后端测试](../tests/unit/test_network_backend.py)、
[生命周期测试](../tests/integration/test_lifecycle.py)、[竞态回归](../tests/integration/test_race_regressions.py)。
实际 Cookie 行为见 [cookies.py](../pyv8c/web/network/cookies.py) 与 [document.cookie 测试](../tests/browser/test_document_cookie.py)。
