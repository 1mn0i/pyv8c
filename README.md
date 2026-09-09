# pyv8c

基于相邻 [`_pyv8`](../_pyv8/README.md) 原生扩展的 Chromium-like JavaScript 浏览器环境。
V8 执行 JavaScript，Python 实现 BOM/DOM、资源加载和页面生命周期，Chromium binding baseline 提供接口形状与 Web IDL 元数据。

**这不是 Chromium 内核的完整复刻，也不是无头 Chrome。** 不提供布局、绘制或 Chrome 多进程沙箱；接口可见不等于行为已实现。

## 快速开始

Python 包要求 3.10+；运行浏览器功能还需要可导入、与当前平台及解释器兼容的 `pyv8` 原生扩展。
`pip install -e .` 不会自动安装或编译相邻仓库的 V8。

在当前 Windows 双仓库工作区中：

```powershell
Set-Location D:\src\pyv8env\pyv8c
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pip install -e .
python -c "import pyv8; print(pyv8.__file__)"
```

```python
from pyv8c import Browser

browser = Browser(stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.set_content("<main id='app'>ready</main>", url="https://example.test/")
    assert page.evaluate("document.querySelector('#app').textContent") == "ready"
    page.evaluate("globalThis.answer = 0; setTimeout(() => answer = 42, 0)")
    page.run_until_idle(timeout=1)
    assert page.evaluate("answer") == 42
finally:
    browser.close()
```

这个示例不访问外网。默认 `real_network=False`；导航到外部地址或加载外部资源时，需提供 `resource_loader` 或显式启用真实网络。

## 运行模型

```text
Browser                         配置、baseline、InterfaceUniverse
└── BrowserContext              Cookie、Blob URL、网络客户端、页面集合
    └── Page                    DOM、导航、任务队列、资源生命周期
        ├── PageRealm           V8 Context、BindingSession、owner executor
        ├── iframe 子 Page      独立 realm，通过 WindowProxy 访问
        └── WorkerRuntime       独立 executor、WorkerRealm、独立 V8 Isolate
```

面向调用者使用 `Page.evaluate/set_content/goto/run_until_idle/close`；这些入口会投递到所属 realm 线程。
不要从任意 Python 线程直接操作 `page.context`、JS 引用或平台内部控制器。

## 当前能力与边界

| 范围 | 当前状态 |
| --- | --- |
| DOM/BOM | 实现常用节点、元素、选择器子集、事件、集合、URL、Navigator 和可配置环境 |
| Web IDL | 不可变 IdlSpec、按 Universe 编译缓存、realm 绑定、按需暴露接口；未实现成员可以生成 stub |
| 生命周期 | 增量 HTML 解析、脚本调度、DOM 生命周期事件、任务/微任务/定时器及基础模块脚本支持 |
| 资源 | 注入 loader、可选 cycronet 网络、资源拦截、显式目录磁盘缓存、Fetch/XHR/WebSocket 基础行为 |
| 窗口/并发 | iframe、动态同源检查、稳定代理身份、postMessage、Dedicated Worker |
| 诊断 | V8 Inspector 的 Runtime/Debugger 路由、部分 Target 命令、Python 侧被动 trace |

不承诺完整 CSSOM/布局/渲染、全部 Web API、完整模块解析器、完整网络安全策略或浏览器级 CDP。
`requestAnimationFrame` 仍是调度近似，不是独立显示帧时钟。不要用本框架隔离敌意脚本。

## Trace 开关

```python
from pyv8c import Browser

browser = Browser(traceable=True, stub_logging="off")
try:
    page = browser.new_context().new_page()
    page.trace = True
    page.evaluate("document.createElement('div').id")
    page.trace = False
finally:
    browser.close()
```

trace 在原生回调退出时观察参数、结果和异常，不重新执行读写，不安装额外观察拦截器。
不支持旧的 JS 输出开关 `faker.hook` 或类上的 `__v8_*_hook__`。
纯 JS 自定义属性不属于此观察范围；详见 [Trace](docs/TRACE.md)。
资源 `resource_interceptor` 则会主动改变请求，不能与 trace 混为一谈。

## 文档导航

- [文档索引](docs/README.md)：按使用、实现、验证选择入口。
- [使用与配置](docs/USAGE.md)：Page API、环境、网络和 DevTools。
- [架构与不变量](docs/ARCHITECTURE.md)：分层、realm、IDL、资源所有权。
- [开发与验证](docs/DEVELOPMENT.md)：原生依赖、测试、CI、接口开发和排障。
- [当前交接](HANDOFF.md)：已核实状态、风险及后续工作，不代替操作手册。
- [工作记录](WORKLOG.md)：提交演进和本轮验证，历史数字不代表当前结果。

文档统一位于 `docs/`，旧 `doc/` 路径不再使用。当前状态核对日期：2026-09-09。
