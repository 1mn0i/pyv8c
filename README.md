# pyv8c

pyv8c 是基于 V8 和 Python 的浏览器脚本运行框架。原生桥接由本仓库中的 `pyv8` 项目提供，Python 实现页面、DOM、Web API、资源加载、任务调度和调试接入。适合运行页面脚本、构造可重复的浏览器行为测试，以及通过本地 Chrome 对照完善 Web 能力。

框架提供常用 DOM 与事件、导航、iframe、Worker、Fetch、XMLHttpRequest、WebSocket、存储、部分样式与几何计算，以及 V8 Inspector。接口暴露由 Chromium IDL 和浏览器环境档案共同决定；**接口存在不代表功能完整实现**。

## 安装

Windows x64 发行包将框架与 V8 引擎打包为单个 `pyv8c-*-cp312-abi3-win_amd64.whl`。下载构建产物后可执行 `py -m pip install 路径\pyv8c-版本-cp312-abi3-win_amd64.whl`；它不依赖顶层 `pyv8` 发行包。发行包中的原生引擎也可单独使用：

```python
from pyv8c import pyv8

class Global:
    __v8_idl_spec__ = pyv8.IdlSpec(global_this=True)

context = pyv8.Context(Global())
try:
    print(context.exec_js("1 + 2"))
finally:
    context.close()
```

此安装包由 Windows workflow 生成，暂不发布到 PyPI。源码开发和发行包均使用 `pyv8c.pyv8` 入口。

本机构建同一发行包时，先按 `pyv8/README.md` 准备 V8，然后在仓库根目录使用 CPython 3.12：

```powershell
py -3.12 -m pip install build wheel
py -3.12 -m build --wheel --outdir dist .
```

生成的 `pyv8c-*-cp312-abi3-win_amd64.whl` 是单包发行物。

要求启用 GIL 的 CPython 3.12 及以上，并能导入与当前解释器匹配的 `pyv8` 原生扩展。Windows 上已对 CPython 3.15.0 完成原生测试和浏览器常规测试；其他平台需要分别验证。free-threaded CPython 暂不支持。

仓库包含 `pyv8/`（V8 绑定源码）和 `pyv8c/`（浏览器框架源码）。以下命令在仓库根目录使用 CPython 3.12 执行：

```powershell
python312 -m pip install -e ".[network,dev,reference]"
python312 -c "from pyv8c import pyv8; print(pyv8.__file__)"
```

构建前须配置 V8 构建目录，详见原生项目文档。

默认 HTTP 和 WebSocket 引擎随基础依赖安装。`dev` 安装测试工具，`canvas` 提供 Pillow 绘图，`cronet` 提供可选网络引擎，`benchmark` 提供可选进程监测，`reference` 安装驱动本机 Chrome 的工具，不下载浏览器。原生扩展构建要求见 [pyv8 文档](pyv8/README.md)。

## 快速开始

```python
from pyv8c import Browser

browser = Browser(real_network=False)
try:
    page = browser.new_profile().new_page()
    page.set_content("<main id='app'>就绪</main>", url="https://example.test/")
    assert page.evaluate("document.querySelector('#app').textContent") == "就绪"
    page.evaluate("globalThis.answer = 0; setTimeout(() => answer = 42, 0)")
    page.wait_for("answer === 42", timeout=1)
    assert page.evaluate("answer") == 42
finally:
    browser.close()
```

默认 `Browser()` 开启真实网络，HTTP 使用 `curl_cffi`，WebSocket 使用 `websockets`。离线运行应显式设置 `real_network=False`，使用 `set_content()` 或资源加载器提供页面。

访问网站时，使用 `page.goto(url, wait_until="load", timeout=30)` 等待指定加载阶段。`run_until_idle()` 只等待当前可运行工作排空，不等待未来网络、定时器或 Worker 结果。`evaluate()` 不自动等待 Promise。

## 对象与执行模型

```text
Browser                         框架配置、接口装配和回调线程池
└─ Profile                      Cookie、网络后端、存储和 Blob URL
   └─ Page                      文档、导航、服务和资源所有权
      ├─ WindowExecutionGroup   owner 线程、Isolate 和帧时钟
      │  └─ Agent               任务源队列、显式微任务队列
      ├─ iframe Page            与所属页面共享执行组，使用独立 Realm
      └─ Worker                 独立线程、Isolate、Agent 和 Realm
```

独立 `new_page()` 创建独立执行组。同步跨 iframe 访问在同一 owner 线程切换 Context。活 JS 句柄只能在所属 owner 线程使用，原 Context 关闭后失效。关闭 Browser 会释放其 Profile、页面及相关资源；调用方应始终显式关闭。

## 文档

- [使用指南](docs/pyv8c/usage.md)：页面 API、等待、环境、网络入口与关闭。
- [架构设计](docs/pyv8c/architecture.md)：模块职责、执行所有权、绑定和生命周期。
- [开发指南](docs/pyv8c/development.md)：开发约定、测试、Chrome 对照和性能验证。
- [任务调度](docs/pyv8c/task-scheduling.md)、[资源加载](docs/pyv8c/resource-loading.md)、[网络传输](docs/pyv8c/network-transports.md)。
- [请求头](docs/pyv8c/request-headers.md)、[窗口与同源访问](docs/pyv8c/window-security.md)、[IDL 格式](docs/pyv8c/idl-format.md)。
- [调试器](docs/pyv8c/devtools.md)、[运行跟踪](docs/pyv8c/tracing.md)。

完整导航见 [文档索引](docs/README.md)。

## 能力边界

pyv8c 不包含 Chromium 的完整渲染、进程和安全沙箱体系。样式、布局、图形、媒体、权限及设备 API 存在实现范围和环境差异；部分成员按基线暴露但使用占位实现。网络栈不是完整的 Fetch、HTTP 缓存或 Chrome 网络服务。

当前没有实现 OOPIF、完整 COOP/COEP、Origin-Agent-Cluster 和 `document.domain` 模型，也不提供完整 CDP Page、Network、DOM 域。不能以单个网站加载成功或接口数量衡量 Chrome 兼容程度。

具体实现与限制见 [Web 能力与实现范围](docs/pyv8c/web-capabilities.md)。
