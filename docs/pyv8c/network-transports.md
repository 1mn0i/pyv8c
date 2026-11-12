# 网络传输

**简体中文** | [English](../en/pyv8c/network-transports.md)

[文档索引](../README.md)

## 传输角色

HTTP 和 WebSocket 是两个可分别替换的角色。默认 HTTP 为 `curl_cffi`，WebSocket 为 `websockets`；`cronet` 可选，使用 cycronet 依赖。

```powershell
python -m pip install -e .
# 需要 cycronet 时单独安装：
python -m pip install -e ".[cronet]"
```

Browser 默认开启真实网络；`real_network=False` 不装配传输，用于本地资源测试。缺失所选引擎依赖会明确失败，不静默切换另一引擎。

```python
from pyv8c import Browser

browser = Browser(http_engine="curl_cffi", ws_engine="websockets")
try:
    page = browser.new_profile().new_page()
    # response = page.net.get("https://example.com/")
finally:
    browser.close()
```

`http_engine`、`ws_engine` 可以是注册名、符合协议的对象、每个 Profile 创建一次的工厂，或 None。None 表示缺少对应角色，不等于禁止所有 loader 和本地资源。

## 参数与代理

`http_options`、`ws_options` 交给对应引擎。内建引擎从自身与底层客户端可接受的键建立校验，选项拼错或传错角色会在创建 Profile 时失败。支持集合取决于当前安装版本，不应把另一引擎的参数直接复制过来。

curl_cffi 常用参数包括 `impersonate`、`verify`、`timeout`，框架还处理 `default_headers`、`max_workers` 和统一 `proxy`。`impersonate` 只影响所选传输，不会同步改变 IDL、navigator、设备或页面环境。

Browser 的 `proxy` 是统一的 HTTP/HTTPS 代理入口，Profile 可覆盖；未设置或空字符串表示直连，不自动继承系统代理。支持 URL 中的代理凭据；统一代理模型不支持 SOCKS。不能声明支持代理却静默忽略配置。

## 宿主请求与页面请求

| 入口 | 行为 |
| --- | --- |
| `page.net.get(url, ...)`、`request(method, url, ...)` | 宿主网络请求，不执行页面 Fetch，也不完整应用页面资源策略 |
| `page.net.get_async(...)`、`request_async(...)` | 宿主异步请求，返回 Future |
| `page.net.fetch(...)`、`xhr(...)` | 调用页面当前 Fetch/XMLHttpRequest，等待宿主响应快照 |
| `page.net.fetch_async(...)`、`xhr_async(...)` | 页面对应调用的 Future |

宿主请求常用关键字包括 headers、params、data、json、cookies、timeout、referer 和 options。宿主与页面请求共享 Profile Cookie 状态，但安全策略、拦截器、缓存和页面覆写行为不同。

NetworkRequest 使用关键字构造，包含 URL、method、headers、body、timeout、credentials、mode、redirect、origin、site_for_cookies、top_level_navigation 和 options。NetworkResponse 是普通响应数据，提供 status、headers、body、text、content、json() 等读取入口；它不是 Web Response。

Profile 始终拥有 CookieJar。传输依据请求凭据决定 Cookie 发送与保存；document.cookie 的访问还受页面规则限制。现成传输实例只能由一个活动 Profile 拥有；需要多个 Profile 时使用工厂或注册的传输名称。

Cookie Domain 使用依赖内置的 Public Suffix List 快照检查；`SameSite=None` 要求 `Secure`，并校验 `__Secure-` / `__Host-` 的部分属性。页面请求按发起站点、祖先站点和顶层导航方法过滤 Strict、Lax 及默认 SameSite Cookie；宿主请求不自动应用页面站点上下文。分区 Cookie 和浏览器完整 Cookie 策略尚未实现，不能将 CookieJar 当成 Chrome 安全边界。

跨源 CORS 请求在需要时先发送不带凭据的 OPTIONS 预检，并验证来源、凭据、方法和请求头；预检未通过不会发送实际请求。当前不缓存预检结果；无法逐跳控制重定向的传输会拒绝需要预检的请求。流式请求体尚未实现，使用 `ReadableStream` 作为请求体会在发送前报错；XHR upload 监听触发的强制预检和私网访问策略尚未实现，加载不可信页面前应由宿主限制网络访问。

## 自定义传输

HTTP 实现 `request_sync(request)`、`request_async(tasks, request, callback)`、`close()`，并连接框架 CookieJar。异步回调格式为 `(response, error)`，只交付普通数据；资源层负责排队回 owner，传输不能直接修改 DOM。

WebSocket 实现 `websocket(tasks, url, protocols, callbacks, options=None)`、`close_websocket(socket)` 和 `close()`。角色协议分别为公开的 HttpTransport 与 WebSocketTransport。

```python
from pyv8c import register_transport

# MyHttpTransport 需实现完整传输协议。
def install_transport(MyHttpTransport):
    register_transport(
        "local-http",
        lambda options=None: MyHttpTransport(options=options),
        option_keys=lambda: frozenset({"timeout"}),
    )
```

注册工厂以 `options=<字典或 None>` 调用；直接传给 Browser 的非注册工厂则无参调用。已有实例及非注册工厂不能再通过 http_options/ws_options 或统一 proxy 配置，应在实例创建时完成设置。`register_ws_transport()` 注册 WebSocket 角色；注册时 `proxy=True` 表示实际支持统一 proxy。异步取消句柄属于传输层，与页面资源 LoadHandle 分工，不能省略迟到结果的所有者验证。

资源层通常要求引擎返回单跳响应，由共享 redirects 规则推进；无法关闭自动跳转的引擎必须明确声明该行为，不能静默吞掉中间跳。

## WebSocket 与限制

WebSocket 的 open、message、error、close 回投 networking 来源。默认 websockets 发送器按 FIFO 异步发送，bufferedAmount 的减少回投 owner；页面销毁会取消该连接所属未完成工作。正常 socket.close 与页面销毁的中止策略不同。

默认传输对 Windows 阻塞 socket 提供取消与关闭协调，但自定义连接工厂需提供等价释放能力。完整背压、队列预算、尚未返回连接对象的阻塞和各引擎生命周期都需单独验证。

TLS、HTTP 版本、压缩、代理和握手排列受引擎及依赖构建影响；选择某个引擎不意味着与 Chrome 全网络栈一致。具体请求头规则见[请求头](request-headers.md)。
