# pyv8c 交接状态

本文只记录当前有效的工程事实，不是开发日志。历史变更请查阅
[WORKLOG.md](WORKLOG.md)。架构、构建流程、验证基线或真实限制改变时，同步更新本文。

## 项目范围

`pyv8c` 是构建于相邻 `_pyv8` V8 扩展之上的 Chromium-like BOM/DOM 运行时。目标是
JavaScript 可观察的 Web IDL 形状、DOM/BOM、页面生命周期、任务、导航和基础网络 API；
不覆盖布局、渲染、CSSOM、Chromium 多进程模型或全部规范细节。

```python
from pyv8c import Browser

browser = Browser()
page = browser.new_context().new_page()
page.set_content("<main id='app'>ready</main>")
assert page.evaluate("document.querySelector('#app').textContent") == "ready"
browser.close()
```

`pyv8c` 与 `../_pyv8` 是独立 Git 仓库。顶层 `from pyv8c import ...` 是稳定 API；
内部包路径不是兼容承诺。

## 目录与依赖方向

```text
core -> bindings -> dom -> bom/network/workers -> runtime
```

| 区域 | 职责 |
| --- | --- |
| `core/` | DOM/V8 错误和 realm service 协议。 |
| `bindings/` | Chromium baseline、Web IDL 转换、registry、动态类、V8 overlay 和提取器。 |
| `dom/` | 节点、事件、HTML parser 和 selector。 |
| `bom/` | Window、Location、History、Storage、URL 和 URLSearchParams。 |
| `network/` | Fetch、XHR、WebSocket、Blob、HTTP 模型和 transport。 |
| `workers/` | Worker API 和 structured clone。 |
| `runtime/` | Browser、BrowserContext、Page、任务队列和页面控制器。 |
| `tools/` | Chromium 拉取、baseline 提取和启动基准工具。 |

领域包不得反向依赖 `runtime`；通过 realm service 注入页面能力。不得重新引入全局
current Page、类级 current Page 或跨 realm 的 callback owner。

## 运行时模型

- `Browser` 持有一个不可变 `BaselineSnapshot` 和一个 `InterfaceUniverse`。
- `BrowserContext` 隔离 Page、Cookie、Blob URL、网络 session 和 frame。
- 每个 `Page` 拥有 V8 Context、Window、Document、任务队列以及 navigation、lifecycle、
  script、resource controller。
- 每个 V8 Context 有独立 `BindingSession`；动态类、class config、callback owner 与 wrapper
  状态均为 realm-local。
- 网络和 Worker 后台回调只能通过 `Page.post_task()` 进入 Page 的 V8 Context。
- Page、Context 或 Browser 关闭后不可复用，继续使用抛出 `InvalidStateError`；Page 关闭时释放
  V8 Context。

文档生命周期：

```text
loading -> parser complete -> defer/module complete -> interactive
        -> DOMContentLoaded -> resources complete -> complete -> load -> pageshow
```

导航替换和关闭派发 `beforeunload -> pagehide -> unload`。导航可被 `beforeunload` 取消；
显式关闭页面忽略取消结果。

## 接口 Baseline

bundled schema 5 baseline 位于 `pyv8c/baselines/interface-baseline.zip`。运行时首次使用时会在同目录展开 JSON 缓存；该缓存不提交也不打包。当前包含：

- 1,254 个接口；10,393 个非 constant member record；2,223 个 constant。
- 提取报告中的源成员数为 12,616，自动签名 3,992，审核签名 225，unresolved 签名 3,105。

接口规范字段：

```text
parent, tag, exposed_in, constructor, template, members, constants
```

`template.handlers` 保存 named、indexed 和 cross-origin interceptor 的角色存在性。默认 baseline
不保存 C++ callback 名称；使用 `--audit` 生成审计 baseline 时才恢复 callback、world 和 provenance。
constant 只存在于 `constants`，不再重复出现在 `members`。

默认 baseline 不含 callback 名称、默认 world 或逐接口 source provenance，并标记
`_webidl.compact: true`；审计时统一显式生成：

```powershell
$env:PYTHONPATH = "."
python tools/extract_interfaces.py pyv8c\baselines\interface-baseline.zip `
  --bindings-dir ".chromium-build\chromium\src\out\Cronet145\gen\third_party\blink\renderer\bindings" `
  --chromium-root ".chromium-build\chromium" `
  --report

# 仅审计产物携带输入文件来源。
python tools/extract_interfaces.py audit-baseline.json --audit
```

`--audit` 产物标记为 `compact: false`，运行时会直接拒绝加载，不执行兼容迁移。

提取器读取生成的 `v8_*.cc`，以 Blink IDL 补充信息，并应用 `tools/webidl-reviews.json`。
无法可靠解析的签名必须保持 unresolved，不能伪造转换规则或 constant 值。

## Binding 与启动

手写 Python 实现优先于 metadata stub。stub 保留构造器、prototype、descriptor、handler 和
toStringTag 形状；未实现成员在实际调用时记录日志并返回 JavaScript `undefined`。

默认 eager 集合固定在 `bindings.universe.DEFAULT_EAGER_INTERFACES`：

```text
Window, EventTarget, Event, Node, Document, Element, HTMLElement,
URL, URLSearchParams, DOMTokenList, NodeList, HTMLCollection, NamedNodeMap
```

其他 Window 名称保持可观察，并在首次访问时物化。不要随意扩大默认 eager 集合，它是启动性能
契约；调用方可通过 `Browser(eager_interfaces=...)` 增加预热接口。

`Browser.startup_stats` 提供耗时和物化计数：

```powershell
python tools/benchmark_startup.py --runs 20
```

`PYV8C_STRICT_VALIDATION=1` 启动时执行全量校验；`PYV8C_STARTUP_STATS=0` 关闭统计。
`_pyv8.Context` 必须支持 Context-local `class_configs` overlay；每个 Page 和 Worker
直接使用独立的 `BindingSession`，不再提供旧扩展的 class 回退路径。

## Chromium 与原生构建

项目只使用已追踪的 `CHROMIUM_VERSION`，没有本地 `.chromium-version` 覆盖文件。版本优先级：

```text
--version / --select -> PYV8C_CHROMIUM_VERSION -> CHROMIUM_VERSION
```

```powershell
python tools/fetch_chromium.py
python tools/fetch_chromium.py --select
python tools/fetch_chromium.py --version 154.0.8030.0
```

Windows 原生扩展使用本地 `_pyv8` checkout：

```powershell
cd D:\code\pyv8env\_pyv8
python310 -m pip install gevent
python310 setup.py build_ext --inplace --force
```

重新构建 V8 时，按 `../_pyv8` 文档配置 Visual Studio、Windows SDK、V8 版本和 depot_tools。
只在完成当前原生重建后启用 native constant 安装：

```powershell
$env:PYV8C_ENABLE_NATIVE_CONSTANTS = "1"
```

## 验证

运行 pyv8c 测试时使用本地构建的扩展：

```powershell
$env:PYTHONPATH = "D:\code\pyv8env\_pyv8;D:\code\pyv8env\pyv8c"
python310 -m pytest -q
python310 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

最近验证结果：

- `_pyv8`：`164 passed`，通过 `python310 setup.py build_ext --inplace --force` 重建。
- `pyv8c`：使用重建原生扩展时 `76 passed`。
- 启动基准：5 次冷启动 p50 为 `378.9 ms`。
- baseline extractor 与 baseline-version 回归子集：`16 passed`。
- baseline 默认/audit 单元子集：`2 passed`。

交接前执行 `git diff --check`。通过静态测试或 extractor 子集不能替代 DOM/network/native
集成覆盖。

## 真实限制与优先级

1. 大部分 Chromium 接口仍是 metadata stub；3,105 个签名 unresolved。优先补充公开 API 的精确
   签名、overload、descriptor、receiver error 和真实实现。
2. 提取器是生成 C++ 文本解析加 IDL 补充，不是 C++ AST。Chromium generator 输出变化时，应补充
   template 与 cross-origin fixture。
3. module script 是 static-import 适配层，不是完整 V8 ES module：没有 live binding、dynamic import、
   import maps、top-level await 或 module Worker。
4. HTML parser 未实现完整 WHATWG insertion mode、`document.write`、speculative preload scanner 和
   Chromium 级错误恢复。
5. CORS preflight、Cookie/SameSite/expiry、cache、redirect、credentials 和真实 cyCronet
   HTTP/WebSocket 行为仍不完整。
6. Worker 缺少完整 timer/fetch/importScripts、MessagePort、transfer ownership、Map/Set 和规范允许的
   cyclic structured clone。
7. iframe WindowProxy/security、frame-tree navigation、sandbox 和所有 context 间 Blob URL 共享仍不完整。
8. layout、rendering、CSSOM、bfcache、Service Worker 和 Chromium process scheduling 不在当前范围。

后续顺序：先强化已实现 public API 与 native overlay 的测试，再完善 CORS/真实网络，再扩展
iframe/Worker 隔离；最后增加非核心接口实现，且不扩大默认启动期开销。
