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
| lazy_interfaces | True | eager 核心以外的接口按需物化 |
| eager_interfaces | None | 使用内置核心集合；显式传入时替换集合 |
| expose_unimplemented | True | exposure 计划是否包含没有手写实现的 baseline 接口 |
| generate_interface_stubs | True | 暴露阶段是否走 baseline 补壳路径，与前一开关不是同一概念 |
| stub_logging | info | 未实现成员日志；off/none/disabled 可关闭 |
| strict_validation | False | 创建 Universe 时完整编译检查，否则按需编译 |

关闭 stub_logging 只关闭日志，不会让占位成员获得实现。
关闭 generate_interface_stubs 也不能作为“所有未实现接口完全不可见”的保证；lazy 声明和 exposure 有独立路径。
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

## 网络与资源

默认不联网；本地 HTML 用 set_content，导航或外部脚本使用明确的 loader。
真实网络需要另外安装项目的 network extra，并使用 Browser(real_network=True)。
完整 loader 契约、响应格式、拦截和缓存风险见 [资源加载](resource-interception.md)。

## DevTools

下面的片段需要人工打开打印的调试地址，不属于无人值守测试：

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    context = browser.new_context()
    page = context.new_page()
    handle = context.start_devtools(page)
    print(handle.url)
    input("打开调试地址后按回车继续：")
    page.evaluate("globalThis.debugValue = 42", filename="example.js")
    input("调试结束后按回车关闭：")
finally:
    browser.close()
```

入口参数为 host、port、start_paused、startup_timeout；默认 host=127.0.0.1，port=0 自动选择端口。
每个 BrowserContext 只允许一个活动 DevTools server，server 是单客户端模型。
Handle 提供 close、wait、wait_for_connection；默认未连接时不会自动替你打开浏览器。
start_paused=True 用于等待启动释放，需要客户端配合，否则求值可能等待到指定超时。
支持 Runtime/Debugger 和部分 Target 命令，不提供完整 Page/Network/DOM 域。
不要将没有认证隔离保证的调试端口暴露给不可信网络。

## 使用时的限制

- 不跨线程直接访问底层 Context 或操作持有的 JSObject；使用 Page 公开入口。
- 页面/Context 关闭后不要继续使用它的 JS 引用。
- 缺失成员可能只是 stub；对关键行为写断言，而不是仅检查 typeof。
- 主动读取 trace 事件里的对象引用可能产生 JS 副作用；日志使用安全 formatter，见 [TRACE](TRACE.md)。
- 这不是敌意代码安全沙箱；同源检查只覆盖已建模的浏览器行为。
