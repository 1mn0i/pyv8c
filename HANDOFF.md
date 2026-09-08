# pyv8c 当前交接状态

本文是开发交接文档，只描述当前代码可依赖的架构、API、构建方式、验证结果和已知限制。
历史实施过程统一记录在 [WORKLOG.md](WORKLOG.md)，不在本文重复展开。

## 项目定位

`pyv8c` 构建在相邻的 [`_pyv8`](../_pyv8) V8 Python 扩展之上，提供可由 JavaScript
观察的 Chromium-like BOM、DOM、Web IDL binding、页面生命周期、任务调度、导航和基础
网络 API。它不是完整浏览器，不包含布局、渲染、CSSOM、浏览器多进程模型或全部 WHATWG
细节。

最小用法：

```python
from pyv8c import Browser

browser = Browser()
page = browser.new_context().new_page()
page.set_content("<main id='app'>ready</main>")
assert page.evaluate("document.querySelector('#app').textContent") == "ready"
browser.close()
```

`Browser -> BrowserContext -> Page` 是当前正式架构。`BrowserContext` 负责页面、Cookie、
Blob URL、网络会话和 DevTools 会话的隔离；Page API 不应绕过 Context 直接共享这些状态。

## 分层与模块职责

```text
foundation -> engine -> web -> platform
```

- `foundation/`：错误类型、不可变环境配置、realm capability 协议、任务队列、索引属性，
  以及 URL/origin 原语（`urls.py`：special scheme、默认端口表、origin 计算与序列化）。
- `engine/`：Chromium baseline、Web IDL 解析/校验、接口 registry、动态类、wrapper 和 V8
  class-config 装配，以及核心错误类型的注册。
- `web/dom/`：节点树、HTML parser、selector、事件和 DOM 集合。
- `web/bom/`：Window、Location、History、Storage、URL、Navigator、定时器等。
- `web/fetch/`：Request、Response、Headers、Blob、fetch 和 XHR。
- `web/network/`：HTTP transport、CookieJar、WebSocket。
- `web/workers/`：Worker API、独立 Worker realm 和 structured clone。

每个 `web/*` 子包用自己的 `registration.py` 注册本包接口，`platform/bootstrap.py`
按 `errors -> dom -> fetch -> network -> workers -> bom` 的顺序组合；包之间不再跨包
代注册。
- `platform/`：Browser/Context/Page 组合根、PageServices、PageRealm、导航、生命周期、
  脚本、资源加载和 DevTools CDP bridge。
- `tests/`：按 browser、integration、unit、native 分层的行为与架构测试。

依赖只能沿箭头方向流动。Web 层通过注入的 capability 使用平台能力，不得持有 Page、
BrowserContext、网络后端或全局 current Page；新的跨领域能力应先扩展
`foundation/realm.py` 协议，再由 `platform/services.py` 实现。

## 运行时模型

### Realm 与执行线程

每个 Page、iframe 和 Worker realm 都有独立 V8 Context、BindingSession 和
`RealmExecutor`。V8 操作必须在所属 executor owner 线程执行：

```text
public Page API / network callback / DevTools command
        -> RealmExecutor.call() or post()
        -> owner thread enters V8 Context
```

Page 的 `TaskQueue` 由 executor 自动驱动，按 parser、posted、microtask、timer 的顺序
分轮处理。每轮最多处理一个 timer，重复零延时 timer 会被限制为正间隔，以避免页面任务或
调试命令饥饿。`Page.run_until_idle()` 是同步测试辅助接口，不是第二套调度器。

`RealmExecutor.call()` 有超时保护。尚未开始的超时命令会取消，不会在调用方返回后产生幽灵
副作用；已经开始执行的 Python/V8 回调无法被安全抢占，会以 `already running` 诊断。

`RealmExecutor.post()` 携带的异常不镜像到 Future，而是走错误出口：默认接到
`TaskQueue.report_error`（可经 `set_error_sink` 覆盖），记录后不会静默丢失；
`SystemExit`/`KeyboardInterrupt` 会重新抛出而不是写日志。事件循环的等待采用投递序号
门禁：`run_turn()` 与条件等待之间到达的投递不会丢唤醒，`wait` 只作安全上限。

### PageServices 能力边界

`PageServices` 是 platform 组合根，持有六个窄能力对象：
`realm_services`、`task_services`、`resource_services`、`document_services`、
`worker_services`、`context_services`。DOM、BOM、Fetch、Network 和 Worker 只接收自身所需
的 facet。页面的 controller 分别位于 `navigation.py`、`lifecycle.py`、`scripting.py`、
`resource_loading.py` 和 `realms.py`。

### 生命周期与资源

Page、资源请求、iframe、Worker、WebSocket、DevTools target 和 TaskQueue 都遵循
`OPEN -> CLOSING -> CLOSED` 的幂等关闭语义。异步请求使用 `RequestHandle` 和导航
generation；完成或取消后从 Page 的 `ResourceScope` 反注册，关闭页面时统一取消。
资源句柄在发起请求前（或返回后补登记）即纳入 `ResourceScope`，登记与完成交错时
也保证能反注册，不会留下永久滞留的句柄。

关闭顺序是：停止接收新任务，解除 DevTools 暂停，取消资源/Worker，清理 owner 队列，
断开 Inspector，最后销毁 Context/Isolate。关闭操作会继续清理其他独立页面，最终报告首个
异常。关闭后继续使用 Page、Context 或 Browser 会抛出 `InvalidStateError`。

## Binding 与 baseline

bundled baseline 位于 `pyv8c/baselines/interface-baseline.zip`，当前为 schema 5。
运行时会生成旁路 JSON 和 `.pyv8ccache`，两者均不提交、不打包；缓存损坏会回退到 JSON
并重建。每个 Context/realm 使用独立的 binding snapshot，不能修改其他 realm 的 class
config。

默认只 eager 物化核心接口，其余接口通过 `_pyv8.Context.declare_lazy()` 首次访问时
物化。`Browser(lazy_interfaces=False)` 可选择全量物化；`PYV8C_STRICT_VALIDATION=1`
启用完整校验，`PYV8C_STARTUP_STATS=0` 关闭启动统计。

手写实现优先于 metadata stub。未实现接口仍可有正确的 prototype/descriptor 形状，但调用
通常返回 JavaScript `undefined` 并记录日志，不能把 stub 当作功能已实现。

## DevTools

`BrowserContext.start_devtools(page, ...)` 启动 loopback V8 Inspector WebSocket，并返回
`DevToolsHandle`（`url`、`host`、`port`、`wait_for_connection()`、`wait()`、`close()`）。
当前只支持一个 Context 一个句柄、一个句柄一个 WebSocket 客户端，主要 CDP 域为
`Target`、`Runtime` 和 `Debugger`；未实现域返回 `-32601 Method not found`。

```python
browser = Browser()
context = browser.new_context()
page = context.new_page()
handle = context.start_devtools(page)
print(handle.url)
# 在 Chrome 中打开 handle.url
handle.close()
browser.close()
```

命令通过目标 realm 的 `RealmExecutor` 投递，WebSocket 线程不直接进入 V8。这样在
`Runtime.evaluate("debugger; ...")` 或断点暂停时，仍能接收 `resume`、求值和单步命令。
主页面、iframe 和 Worker 各自拥有 target；断开 WebSocket 会取消该连接的全部 pending
事务，防止旧响应发送给重连客户端。

Inspector 属于 V8，`Debugger.send`/`disconnect` 必须回到 realm owner 线程执行。
`DevToolsTarget._resolve_owner()` 统一解析 Page -> PageRealm、Worker realm、
`worker_runtime` 三段的 executor，`send`/`_send_on_owner`/`close` 共用。关闭时若 owner
不可达或已关闭，则跳过断开调用并把原因记入 `close_diagnostics`（native deallocator 在
最后一个引用释放时仍会断开），绝不从调用者线程直接触碰 V8 Debugger。

需要捕获首个脚本时：

```python
handle = context.start_devtools(page, start_paused=True, startup_timeout=60)
page.goto("http://127.0.0.1:8000/index.html")
```

页面会在首脚本前等待 Chrome 发送 `Runtime.runIfWaitingForDebugger`。`startup_timeout`
到期会解除等待并抛出明确的超时错误。监听地址默认是 `127.0.0.1`，没有认证或 TLS。

## 已实现的 Web 行为

- `document.all`：live `HTMLAllCollection`、undetectable `typeof` 行为、indexed/named
  lookup、legacy call 和严格模式 setter 错误。
- `document.cookie`：Context 级 CookieJar、域/路径/Secure/HttpOnly/Max-Age/Expires、脚本
  读写和删除；实际值变化用 `logger.debug` 记录。
- DOM 集合、HTML parser、事件传播、生命周期事件、History、URL、Storage、fetch/XHR、
  Blob URL、iframe、Worker、WebSocket 和可配置 BrowserEnvironment。

## 构建与验证

开发环境需要 Python 3.10+ 和可导入的 `_pyv8` 扩展。Windows 本地构建：

```powershell
cd D:\src\pyv8env\_pyv8
python310 setup.py build_ext --inplace --force

cd D:\src\pyv8env\pyv8c
$env:PYTHONPATH = "D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c"
python -m pytest -q
```

当前工作区验证结果：`236 passed`（含 A1-A4 竞态回归）。竞态用例以
`pytest -m race --count 20` 重复运行验证不回潮。若未设置 `_pyv8` 的 `PYTHONPATH`，
测试收集会因 `ModuleNotFoundError: pyv8` 失败，这属于环境问题而非 pyv8c 测试结果。
完整 CI 需要能编译 `_pyv8` 的原生工具链，纯 Python 的 lint/unit 阶段不需要。

## 未决项（按优先级）

以下为评审确认但尚未实现的缺口。带「本轮明确不做」的条目只是登记在案，
不改变当前分层与代码结构。

### B 档：结构重构（本轮明确不做）

1. Page 组合根职责去化：controller 仍经 `self.page` 反引用取协作者，目标是改为构造时
   显式注入（navigation/lifecycle/scripting/resource_loading 各自只持有所需 facet）。
2. 能力协议对齐：`foundation/realm.py` 的 realm capability 协议与 web 层实际消费面存在
   偏差，需按真实使用重对齐后再扩展。
3. 上述重构进行前，A1-A4 建立的单 owner、错误出口与资源登记不变量不得回退。

### P2：模型层

1. iframe 目前以独立 Page realm 建模，尚未收敛为完整的 Frame/WindowProxy/origin 模型。
2. `requestAnimationFrame` 尚未使用独立 frame clock。
3. 网络策略/缓存与传输层尚未分离；自定义同步 `resource_loader` 若自身阻塞，
   `goto(timeout=...)` 无法强制抢占其 Python 回调（真实网络请求已有异步 transport
   和取消句柄）。

### P3：观察与验证

1. Worker 虽使用统一 `RealmExecutor` 协议，但仍保留独立 Isolate 和专用生命周期路径，
   需要继续加强 Windows Debug/ASAN、多 Worker 并发与重复关闭场景的压力验证。
2. 请求取消/导航代际检查、完整浏览器级 CDP `Page`/`Network` 域仍未实现。
3. `_pyv8` 原生扩展必须与运行 Python 版本和 ABI 一致；跨 Python 版本复用 `.pyd` 不受支持。

后续改动优先补齐 Frame/Origin 模型、原生 Inspector 压力测试与 B 档结构重构，
同时保持 capability 边界、单 owner 线程和全量回归测试不退化。
