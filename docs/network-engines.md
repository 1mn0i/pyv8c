# 网络引擎与参数

[返回文档索引](README.md)。实现入口：[registry.py](../pyv8c/web/network/registry.py)（注册与校验）、[backend.py](../pyv8c/web/network/backend.py)（装配），以及三个引擎模块 [cronet.py](../pyv8c/web/network/cronet.py)、[curl_cffi.py](../pyv8c/web/network/curl_cffi.py)、[ws_websockets.py](../pyv8c/web/network/ws_websockets.py)。
本页讲“用哪个引擎、给它什么参数”；loader、拦截与缓存见 [资源加载与拦截](resource-interception.md)。

## 两个角色

一个 context 的网络是两个可独立选择的部件：**HTTP 引擎**负责请求，**WebSocket 引擎**负责 socket。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| real_network | False | 为没指定名字的角色装配默认引擎（http=curl_cffi、ws=websockets） |
| network_engine | None | HTTP 引擎的名字 |
| ws_engine | None | WebSocket 引擎的名字 |
| network_backend | None | 整体注入；对象填哪些角色由它自己能做什么决定 |
| http_options / ws_options | None | 分别交给两个部件的参数，见下 |

```python
from pyv8c import Browser, transport_names

transport_names("http")  # ('cronet', 'curl_cffi')
transport_names("ws")    # ('cronet', 'websockets')

browser = Browser(real_network=True)                            # 默认：curl_cffi + websockets
browser = Browser(network_engine="cronet")                      # 只装 HTTP
browser = Browser(ws_engine="websockets")                       # 只装 socket
browser = Browser(network_engine="cronet", ws_engine="cronet")  # 一个引擎两个角色，只构造一次
browser = Browser(network_backend=MyHttpOnly())                 # 注入 HTTP，socket 照常装配
```

边界：

- `real_network=True` 只补“没说名字”的角色；给角色指定名字就装配**恰好**那个角色。因此只装 socket 的 context 里 `page.net`、资源加载会以“没有 HTTP 引擎”失败，而 `new WebSocket` 可用；反过来只装 HTTP 时 `new WebSocket` 会报“没有 WebSocket 引擎”。
- 注入的对象按能力认角色：有 `request_sync` 就是 HTTP 引擎，有 `websocket` 就是 socket 引擎；它没填的角色照常按上面的规则装配。注入对象已经构造完成，所以它填的那个角色的 options 会被拒绝（`TypeError`），不会被静默丢掉。
- 同名同时担任两个角色时只构造一次实例——否则会多出一个客户端、线程池与连接缓存；此时 `ws_options` 会被拒绝，而不是静默忽略。

## 参数与校验

`http_options` 只交给 HTTP 引擎、`ws_options` 只交给 socket 引擎，每个引擎只消费自己认识的键。字典会被拷贝：之后改调用方自己的字典不影响已创建的 Browser。

| 键 | 谁消费 | 含义 |
| --- | --- | --- |
| max_workers | 两个内置 HTTP 引擎的桥接层 | 桥接线程池大小，默认 4；不转发给客户端 |
| default_headers | curl_cffi | 作为会话选项传入；默认 False，本环境不额外添加请求头 |
| cookies / headers | websockets | 握手是否带 context 的 cookie（默认带）；`headers` 追加握手头 |
| 其余键 | 该引擎的客户端 | 按其构造签名转交，如 cronet 的 `timeout_ms`/`verify`、curl_cffi 的 `impersonate`、websockets 的 `open_timeout`/`ping_interval`/`max_size`/`proxy` |

校验发生在**创建 Context 时**（`new_context()`），并且**按角色分别进行**：

| 情况 | 行为 |
| --- | --- |
| 键在该角色引擎的集合里 | 转交该引擎 |
| 拼错的键，或属于另一个角色引擎的键 | `TypeError`，指出键名、角色与引擎名，并列出可接受的键 |
| 引擎在运行时无法列出自己的键 | 不预检，由客户端构造时拒绝，错误里补上它来自哪个 options |
| 宿主注册的引擎未声明键集合 | 不校验 |
| 给了 options 却没有引擎读它 | `TypeError` |
| 注入的引擎填了该角色 | `TypeError`：它已构造完成，不会读这些 options |

```python
browser = Browser(network_engine="cronet", http_options={"timeout_ms": 5000, "impersonete": "chrome"})
browser.new_context()
# TypeError: unknown option 'impersonete' for http engine 'cronet';
#            accepted: base_url, chrometls, default_domain, headers, max_workers, proxies, timeout_ms, verify
```

各引擎的键集合来自各自客户端的构造签名：cronet 随已安装 cycronet 版本变化；curl_cffi 把会话参数 TypedDict 只留给类型检查器（运行时不存在），属于上表第三行；websockets 取已安装 `websockets.sync.client.connect` 的具名参数——它末尾的 `**kwargs` 会转交 `socket.create_connection`，因此不计入，那里的拼写错误只会在 socket 线程里炸。
缺少可选依赖仍报 `NetworkUnavailableError`，且先于键校验发生。

## WebSocket 握手

socket 引擎按浏览器的做法发握手：环境派生的 `User-Agent` 与 `Accept-Language`、文档的 `Origin`、以及 context 里该 URL 的 cookie（按 `http`/`https` 等价形式查询，cookie 存的是主机而不是 scheme）；`ws_options={"headers": {...}}` 再叠加调用方自己的头。

```python
browser = Browser(ws_engine="websockets", ws_options={"cookies": False, "headers": {"X-Page": "socket"}})
```

事件是异步的：`new WebSocket(url)` 返回后 open/message/error/close 依次到达页面；页面 `close(code, reason)` 完成关闭握手后，close 事件报告同一对 code/reason 且 `wasClean` 为真；Context 关闭会关闭它开过的全部 socket。

## 自定义引擎

两个角色各有注册入口：

- `register_transport(name, factory, option_keys=...)`：HTTP 引擎。
- `register_ws_transport(name, factory, option_keys=...)`：socket 引擎。

factory 按 `factory(options=<字典或 None>)` 调用，每个 Context 一次；`option_keys` 是惰性可调用，返回该引擎接受的键，不声明就不校验（由宿主自己负责）。HTTP 引擎需要 `request_sync`/`request_async`，socket 引擎需要 `websocket`/`close_websocket`，两者都需要 `close`；`cookies` 由 Context 在构造后赋值。

```python
from pyv8c import register_transport, register_ws_transport

class MyHttp:      # request_sync / request_async / close
    ...
class MySockets:   # websocket / close_websocket / close
    ...

register_transport("my-http", lambda options: MyHttp(options), option_keys=lambda: frozenset({"region"}))
register_ws_transport("my-sockets", lambda options: MySockets(options))
```

一个对象同时提供两组方法就能同时担任两个角色（cycronet 就是这样），因此在 `network_engine` 与 `ws_engine` 同名时会被复用一次。

## 与每请求参数的区别

`http_options` 是**会话级**的，只在创建 Context 时读一次。单次请求的参数走 `page.net.<method>(..., options={...})`：

```python
page.net.get("https://example.test/", options={"impersonate": "chrome"})
```

每请求参数按请求交给当前 HTTP 引擎，各引擎忽略自己没有对应物的键（cronet 目前只使用其中的 `timeout` 字段，不读 `options`），与 `http_options` 的校验无关。
