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
| `bindings/` | Chromium baseline（`baseline.py`：快照加载与两级磁盘缓存）、Web IDL 转换、registry、动态类、V8 overlay 和提取器。 |
| `dom/` | 节点（`model.py`）、集合视图（`collections.py`）、事件模型（`events.py`）、HTML parser 和 selector。 |
| `bom/` | Window、Location、History、Storage、URL 和 URLSearchParams。 |
| `network/` | Fetch、XHR、WebSocket、Blob、HTTP 模型和 transport。 |
| `workers/` | Worker API 和 structured clone。 |
| `runtime/` | Browser、BrowserContext、Page、任务队列和页面控制器。 |
| `tools/` | Chromium 拉取、baseline 提取和启动基准工具。 |

领域包不得反向依赖 `runtime`；通过 realm service 注入页面能力。不得重新引入全局
current Page、类级 current Page 或跨 realm 的 callback owner。

## 运行时模型

- `Browser` 持有一个不可变 `BaselineSnapshot` 和一个 `InterfaceUniverse`。
- `BaselineRepository`/`BaselineSnapshot` 定义在 `bindings/baseline.py`（universe re-export 保持
  旧导入路径可用）；`InterfaceUniverse` 与 `BindingSession` 留在 `bindings/universe.py`。
- `BrowserContext` 隔离 Page、Cookie、Blob URL、网络 session 和 frame。
- 每个 `Page` 拥有 V8 Context、Window、Document、任务队列以及 navigation、lifecycle、
  script、resource controller。
- `Page` 是薄门面：逻辑分别位于 `runtime/navigation.py`、`runtime/lifecycle.py`、
  `runtime/scripting.py`、`runtime/resource_loading.py`（controller 通过 `Page` 聚合，
  状态经 `runtime/state.py` 的 `StateAlias` 暴露在 `Page` 上），`Browser` /
  `BrowserContext` 也在同文件。
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

其余暴露名称由 native 惰性访问器提供，并在首次访问时物化（见下节）。不要随意扩大默认
eager 集合，它是启动性能契约；调用方可通过 `Browser(eager_interfaces=...)` 增加预热接口。

### 惰性接口物化

Page 与 Worker 创建时会调用 `_pyv8.Context.declare_lazy(names, skip=eager_names)`，
为每个非 eager 名称在 global 对象上装一个不可枚举的 accessor，整个过程不构建任何
FunctionTemplate，也不生成任何 Python stub。首次访问该名称时 native 回调
`BindingSession.lazy_factory()`，物化接口并回填模板缓存，之后同类访问直接命中缓存。

```python
session = page.binding_session
session.lazy_names("Window")   # 已声明但未物化的名称
session.lazy_factory("Window") # 交给 native 的回调
session.materialized_interfaces
```

契约要点：

- 工厂返回 `None` 表示该名称不属于此 realm，JS 侧解析为 `undefined`；工厂抛错时按
  V8 异常语义处理（`try/catch` 可捕获，未捕获则抛给 `exec_js` 的调用方）。
- 物化失败的**不会**写入模板缓存，下次访问会重试。
- `declare_lazy` 的 `skip` 参数用于保护 eager 名称：缺了它，每个名称都要做一次 global
  属性查找并触发 Window 的 Python 命名拦截器（实测 1032 个名称约 42ms；传 `skip` 后约 1.7ms）。
- Worker 与 Page 走同一条惰性路径。`Browser(lazy_interfaces=False)` 可显式选择全量物化：
  `class_configs`、`expose_interfaces` 直接构建全部暴露接口，`lazy_names()` 返回空，
  `declare_lazy` 不注册任何 accessor。`expose_interfaces` 不再有 `lazy_interfaces`
  参数，统一读 `universe.lazy_interfaces`。
- V8 要求 undetectable 模板（`document.all` 式 legacy 接口，如 `HTMLAllCollection`）
  带 instance call handler：无真实 `__call__` 时 `_pyv8` 自动安装 no-op handler，
  调用返回 `undefined`，否则模板实例化会触发 V8 CHECK 崩溃。

`Browser.startup_stats` 提供耗时和物化计数：`lazy_registered`（声明数）、
`lazy_materialized`（已按需物化数）、`lazy_register_ms`。冷启动的
`lazy_materialized` 应为 0。

```powershell
python tools/benchmark_startup.py --runs 20
```

`PYV8C_STRICT_VALIDATION=1` 启动时执行全量校验；`PYV8C_STARTUP_STATS=0` 关闭统计。
`_pyv8.Context` 必须支持 Context-local `class_configs` overlay 与 `declare_lazy`；
每个 Page 和 Worker 直接使用独立的 `BindingSession`，不再提供旧扩展的 class 回退路径。

### Baseline 缓存

`BaselineRepository` 在 baseline 旁维护两级缓存：展开的 JSON（`.json`）和二进制
pickle 快照（`.pyv8ccache`）。二进制缓存带 magic + schema + sha256 校验头，只在源
字节完全相同时命中，命中后跳过 JSON 解析、接口投影和父链成环检查；任何读取异常都
静默回落到 JSON 路径并重建缓存，启动永远不会因缓存损坏失败。两个缓存文件都不提交、
不打包（见 `.gitignore`）。

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
native constants 和 Context-local class-config overlay 在当前重建的 `_pyv8` 中默认启用。

## 验证

运行 pyv8c 测试时使用本地构建的扩展：

```powershell
$env:PYTHONPATH = "d:\src\pyv8env\_pyv8;d:\src\pyv8env\pyv8c"
python310 -m pytest -q
python310 -m pip wheel --no-deps --no-build-isolation --wheel-dir dist .
```

最近验证结果（2026-08-31，全库冗余清理与架构拆分后）：

- `_pyv8`：`177 passed`（原 164 + 新增 native 懒物化 13），通过
  `python310 setup.py build_ext --inplace --force` 重建。
- `pyv8c`：`109 passed`（`test_baseline_versions` 的 `{}`-empty 用例 regex 已与
  `baseline.py` 实际错误信息 `must contain _webidl metadata` 对齐，上一轮记录的
  遗留失败清零）。
- 本轮拆分新增文件：`bindings/baseline.py`（BaselineRepository/BaselineSnapshot/缓存）、
  `dom/collections.py`（NodeList/HTMLCollection/DOMTokenList）、`dom/events.py`
  （Event 家族与 EventTarget）；原模块 re-export 公开名保持导入兼容。
- 本轮清理：删除死代码（`InterfaceUniverse.report`/`info`、`BindingSession.materialize_*`、
  `Page.wait_for_network`/14 个未用 StateAlias/`_promise_counter`、`_decode_clone_markers`、
  `Request.backend_request`/`Headers.items`、`Node.DOCUMENT_POSITION_*`、`serialize`、
  `extractor/idl.py`/`schema.py` 空壳、`cpp.PACKAGE_ROOT`）；修复 `NamedNodeMap._list`→
  `_items`、`bom/api.py` 缺 `import pyv8`、`network/api.py` 缺 `import json` 三处悬空引用；
  收敛重复逻辑（`Response.body_text`、`ResourceController._network_request`/
  `_response_from_loader`、`EventTarget._get_handler` 复用、`History._validate_target`、
  名称校验正则提升为模块级常量）。
- 第二轮清理：删除恒真参数 `lazy_interfaces`（`InterfaceUniverse` /
  `expose_interfaces`）与 `InterfaceSpec.constants` 字段及其 baseline 重复校验；
  `_stub_handler` 收敛；`_run_callback` 内联；Worker 删除 `_runtime_finished`；
  `bom/api.py` 移除 `__import__` hack；`_pyv8/pyv8/tools.py` 删除无引用的
  `impl_warp` / `attr_warp` / `CtxTypeIndex`。`InterfaceSpec` 形状属内部 API，
  无兼容承诺。
- 启动基准：`python tools/benchmark_startup.py --runs 5` p50 为 `91.4 ms`
  （懒物化接线前 `378.9 ms`）。
- `lazy_interfaces` 传参链路修复后：`Browser(lazy_interfaces=False)` 全量物化
  路径可用（1045 个 Window 接口直接物化，`lazy_names()` 为空）；同时修复
  `_pyv8` undetectable 模板缺 instance call handler 的 V8 CHECK 崩溃
  （触发点为暴露 `HTMLAllCollection`），`_pyv8` 重建后 `177 passed`。
- baseline 加载：二进制缓存命中时 `21.3 ms`（冷加载 `73.5 ms`，约 3.4x）；
  损坏缓存自动回落 JSON 并重建，启动不失败。

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

## 当前状态（2026-09-01）

- 完成 bindings catalog/config/wrappers/schema 拆分；`InterfaceUniverse` 继续作为组合 facade。
- Page 资源所有权统一通过 `_track_*` / `_discard_*` facade 方法管理，network/resource 层不再直接操作 Page 私有集合。
- `_pyv8` 的 `ClassConfig` 读取与 overlay 校验已移至 `src/pyclass_config.cpp`，并保留已跟踪的 `pyv8/_pyv8.pyd`。
- `HTMLAllCollection` 复现脚本正常输出 `function HTMLAllCollection() { [native code] }`，不再触发 V8 DCHECK。
- 最新验证：`_pyv8` 全量 `177 passed`，`pyv8c` 全量 `112 passed`。
- 已推送 GitHub：`pyv8c` 提交 `1980864`，`_pyv8` 提交 `429ec5e`；两个工作区均干净。
