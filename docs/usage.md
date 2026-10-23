# 使用指南

[文档索引](README.md)

## 创建与关闭

`Browser` 管理公共配置，`Profile` 管理会话状态，`Page` 管理页面。不同 Profile 的 Cookie 和内存状态独立；同一 Profile 可以创建多个页面。

```python
from pyv8c import Browser

browser = Browser(real_network=False)
try:
    profile = browser.new_profile()
    page = profile.new_page()
    page.set_content("<p id='text'>内容</p>", url="https://example.test/")
    assert page.evaluate("document.querySelector('#text').textContent") == "内容"
finally:
    browser.close()
```

`new_page()` 默认地址为 `about:blank`。加载主文档使用 `goto()`，不要将创建 Page 与完成网络导航混为一谈。

`page.close()`、`profile.close()`、`browser.close()` 逐级释放所有者资源，正常关闭可重复调用。独立资源清理失败会报告异常，其他资源仍尽力释放；不能将异常当作成功关闭。关闭子页面不会停止仍被其他页面使用的执行组。

跨线程关闭可中断活跃 JS。后台任务因匹配的关闭票据终止时不报告宿主故障；正在等待同步操作的宿主仍会收到 `pyv8.JavaScriptTerminated`。该异常继承 `BaseException`，不属于普通 JS 异常。需要分类处理时应显式捕获，真实超时和未知终止不能统一忽略。

## 页面 API

| 调用 | 契约 |
| --- | --- |
| `set_content(html, url="about:blank")` | 提供 HTML 并启动文档处理；外部资源完成需另外等待 |
| `goto(url, timeout=30, wait_until="load")` | 导航并等待 `commit`、`domcontentloaded` 或 `load`；`None` 按提交阶段处理 |
| `evaluate(script, filename=None, timeout=30)` | 执行源码并物化可返回的数据，不自动等待 Promise |
| `evaluate_handle(script, filename=None)` | 返回基本值或活 JS 句柄，不进行数据快照转换 |
| `wait_for(expression, timeout=30)` | 等待 JS 条件成立；条件应无副作用 |
| `wait_for_load_state(state="load", timeout=30)` | 等待当前文档代次的加载状态，文档替换或关闭会失败 |
| `run_until_idle(timeout=1.0)` | 等待目标 Agent 可运行工作及检查点排空；成功返回 True，超时返回 False |
| `dispatch_event(event_type, ...)` | 向指定窗口、文档或元素派发宿主构造的事件 |
| `set_visibility(hidden)` | 更新可见性并驱动相关生命周期行为 |

`goto(..., wait_until="domcontentloaded")` 可能在 `load` 监听器仍运行时返回。网络请求完成、DOM 就绪和应用就绪是不同条件，应分别等待。加载流程被终止时，等待 `load` 会失败，不把已经开始派发当作加载成功。

```python
page.set_content("<script>globalThis.ready = false; setTimeout(() => ready = true, 0)</script>")
page.wait_for_load_state("load", timeout=2)
page.wait_for("ready === true", timeout=2)
```

`run_until_idle(timeout=0)` 只查询状态。远期 timer、在途网络、长连接和存活 Worker 不直接阻止空闲；不要用空闲等待代替业务条件等待。owner 线程内部不得同步等待未完成的导航或网络操作。

## 返回值与句柄

`evaluate()` 将对象和数组转换为可物化的 Python 数据。需要 DOM、函数等对象身份时，使用 `evaluate_handle()`；操作必须回到所属执行组的 owner 线程：

```python
handle = page.evaluate_handle("({answer: 42})")
snapshot = page.executor.call(handle.to_python)
assert snapshot == {"answer": 42}
```

`page.executor` 属于内部执行设施，只有需要原生句柄操作时才使用。不要从其他线程直接读取句柄，也不要在不相关页面间传递活对象。导航替换 Context 后，旧句柄不再是新文档的对象；原 Context 最终关闭后句柄失效。

## 环境配置

`BrowserEnvironment` 使用扁平键名，构建后不可变。未显式设置的值来自随包环境档案及派生规则。`Browser(environment=...)` 设置默认环境，`new_page(environment=...)` 可覆盖页面环境。

```python
from pyv8c import BrowserEnvironment

environment = BrowserEnvironment({
    "languages": ["zh-CN", "zh"],
    "viewport.width": 1280,
    "viewport.height": 720,
    "viewport.device_scale_factor": 1,
})
values = environment.resolved()
assert values["viewport.width"] == 1280
```

常用配置包括 `user_agent`、`platform`、`language`、`languages`、`timezone`、`webdriver`、`hardware_concurrency`、`device_memory`，以及 `viewport.*`、`screen.*`、`client_hints.*`、`media.*`、`permissions.*` 等。不是任意字符串都有效，未知键和不合法值会被校验。

`to_dict()` 返回显式配置，`resolved()` 返回解析后的完整值；`overlay()` 创建覆盖后的新环境，`from_file()`、`to_file()` 用于 JSON 文件。修改环境值不会自动提供真实硬件、权限或媒体解码能力。

## 浏览器选项

| 选项 | 默认值及含义 |
| --- | --- |
| `real_network` | True；是否装配真实网络引擎 |
| `http_engine` / `ws_engine` | `curl_cffi` / `websockets`；引擎名、实例、工厂或 None |
| `http_options` / `ws_options` | 各引擎配置，创建 Profile 时校验 |
| `proxy` | None；未设置时直连，Profile 可覆盖 |
| `default_headers` | 宿主 `page.net` 请求的默认头 |
| `resource_loader` / `resource_interceptor` | None；资源来源和拦截回调 |
| `threaded_loaders` | False；是否将宿主回调放入有界线程池 |
| `cache` / `cache_dir` | True / None；只有指定目录才有持久资源响应缓存 |
| `frame_rate` | 60.0；页面树的虚拟帧时钟频率，必须为有限正数 |
| `trace` / `traceable` / `trace_callback` / `trace_scripts` | False / False / None / None；跟踪配置与脚本 URL 筛选 |
| `expose_unimplemented` | True；是否暴露基线中的未实现接口 |
| `stub_logging` | `off`；也可为 `on` 或 `verbose`，控制未实现成员日志 |
| `interface_overrides` | None；接口成员实验性替代回调 |
| `idl_path` | None；默认读取包内 `pyv8c/idl/idl.zip` |
| `baseline_mismatch` | `warn`；实现与所选基线不匹配时警告，`error` 为严格校验 |

`new_profile(proxy=..., trace=..., traceable=..., trace_callback=...)` 可设置相应会话选项。网络与资源回调的线程限制见[资源加载](resource-loading.md)。

`interface_overrides` 格式为 `{"接口名": {"成员名": 回调}}`，属性可使用 `{"get": 回调, "set": 回调}`。实例方法接收 `(receiver, *args)`，静态方法接收 `(*args)`，属性读取接收 `(receiver)`，写入接收 `(receiver, value)`。未知成员、非法槽位和只读属性的写入覆盖会被拒绝；不覆盖构造器、常量或 named/indexed handler。正式修复应进入所属实现层。

## 网络调用

`page.net.get()`、`request()` 及对应异步入口是宿主传输请求，使用 Profile 网络后端和 Cookie；它们不执行页面 `fetch`，也不经过完整页面资源策略。异步入口返回 Future。

`page.net.fetch()`、`xhr()` 及其异步入口调用页面当前的 Fetch/XMLHttpRequest，实现可能已被页面替换；结果为宿主响应快照，适用页面资源管线和相应同源规则。不要把两组调用当作可互换入口。

## 事件与输入

```python
page.set_content("<button id='go'>继续</button>")
page.evaluate("document.querySelector('#go').addEventListener('click', () => globalThis.clicked = true)")
page.click("#go")
page.wait_for("globalThis.clicked === true", timeout=1)
```

宿主支持 `click()`、`dblclick()`、`hover()` 和 `page.mouse`。输入行为基于当前布局与命中测试；几何和真实 Chrome 渲染仍可能有差异。`dispatch_event()` 提供明确的事件派发，不等同于完整输入动作序列。

应用侧异常、宿主故障、网络失败和执行终止是不同结果。排查时先查看失败阶段，再选择[运行跟踪](tracing.md)、[调试器](devtools.md)或本地 Chrome 对照，不通过延长固定休眠掩盖问题。
