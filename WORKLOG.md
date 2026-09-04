# pyv8c 工作记录

本文记录已经完成的阶段性工作、重要设计决策、提交和当时的验证结果。当前库的真实状态以 [HANDOFF.md](HANDOFF.md) 为准。

维护规则：

- 新工作在文末追加日期章节，使用“目标、修改、验证、限制”结构。
- 只记录帮助后续开发理解代码演进的事实，不记录终端操作过程。
- 历史测试数字表示当时状态，不替代 HANDOFF 中的最新验证结果。
- `_pyv8` 的修改在这里说明，但提交仍位于其独立 Git 仓库。
- 每轮开发后同步更新 HANDOFF 的当前能力和最后一节遗留问题。

## 2026-08-23 至 2026-08-24：初始运行时与严格原型链

### 目标

创建独立 `pyv8c` 包，以 `_pyv8env/renderer` 为 Chromium binding 基线，实现 `Browser -> BrowserContext -> Page` 和严格的 JS 接口继承。

### 修改

- 建立 Browser、Context、Page、Window 和 Document 运行时。
- Python DOM 节点作为唯一真相源，并维持 JS wrapper identity。
- 显式登记 EventTarget、Node、Document、Element、HTMLElement、事件派生类及常见 HTML 元素，禁止从 Python MRO 推断 JS 原型链。
- 实现 DOM 树操作、HTML 解析、选择器、集合、事件传播、BOM、定时器和页面任务队列基础能力。
- 初步实现 fetch、XHR、History、iframe、Worker、WebSocket、Blob 和 URL。
- 创建中文 HANDOFF，并将开发历史拆分到 WORKLOG。

### 验证

- 初始核心、DOM、事件和 native smoke 测试通过。

### 限制

- Chromium 接口覆盖、Web IDL 参数语义、真实网络、模块脚本和生命周期细节均不完整。

## 2026-08-24：V8 15.2.20.1 Windows 构建环境

### 目标

直接编译项目中的 V8 `15.2.20.1`，不使用旧 `v8.7z`。

### 修改

- 修复 `_pyv8/v8_build.py` 生成 `.gclient` 的 `custom_deps` 格式。
- Windows 上使用 depot_tools `.cipd_bin/vpython3.exe` 执行 GN/autoninja。
- 删除 `_pyv8/v8.7z`。
- 在 `_pyv8/README.md` 和 `_pyv8/doc/USER_GUIDE.md` 记录 VS 2026、Windows SDK 和构建环境变量。

### 提交

- `_pyv8`：`5ac3b46`（构建修复）、`83d3027`（构建文档）。

### 验证

- V8 源码同步到 `15.2.20.1`，GN 生成 x64 release 成功，后续 `build_ext --inplace` 完成。

## 2026-08-24 至 2026-08-25：网络、Worker、生命周期和错误体系

### 目标

扩展浏览器异步表面，并让非法 DOM/BOM 调用产生 JS 可识别的标准异常。

### 修改

- 增加可注入 `resource_loader`，默认禁止网络；增加可选 cyCronet 后端及 Context 级 session。
- fetch/XHR、Cookie、基础 CORS、redirect、History、iframe、Worker 和 WebSocket 接入 Page 队列。
- Worker 增加独立线程/Context、Blob URL 脚本源、postMessage 和基础 structured clone。
- 增加 readyState、readystatechange、DOMContentLoaded、load/pageshow 及 beforeunload/pagehide/unload。
- 新增 `error.py`：V8 Error 桥接、DOMException、DOMError、标准异常工厂和 `as_js_error()`。
- DOM、选择器、History、XHR、Worker、WebSocket 和网络错误迁移到标准 TypeError/DOMException。

### 验证

- 阶段性测试从 `15 passed` 增长至 `21 passed`。

### 限制

- cyCronet 真实 wheel/DLL、完整 CORS/Cookie、Worker 调度和 structured clone 仍有限制。

## 2026-08-25：全局暴露隔离、URL 和 Blob URL

### 目标

禁止内部 pyv8c 对象进入 JS 全局，将 URL 作为正式浏览器接口实现。

### 修改

- `expose_interfaces()` 按 Window/Worker exposure 过滤公共接口。
- 移除 `__pyv8c_*` Promise、Blob URL 和 Worker 临时全局桥接。
- WindowProperties 不再作为页面公共全局接口。
- URL 实现组成属性、searchParams、序列化和 `canParse/parse/createObjectURL/revokeObjectURL`。
- Blob URL 改为 `blob:<serialized-origin>/<uuid-v4>`，opaque origin 使用 `null`。
- Promise resolver 使用闭包记录，URL 静态方法通过当前 Page owner 访问 Blob store。

### 验证

- 阶段性全量测试 `24 passed`。
- `Object.getOwnPropertyNames(globalThis)` 不包含 `__pyv8c_*`。

### 限制

- WHATWG URL 全部边界、MediaSource Blob URL 和复杂跨 Context 生命周期尚未覆盖。

## 2026-08-25：Web IDL 参数校验与 Chromium 基线提取

### 目标

统一 JS 可见构造器、方法和 setter 的参数校验，并修复只有 URL 成员较完整的 baseline。

### 修改

- 新增 `webidl.py`，实现必需/可选/variadic、undefined/null、字符串、布尔、数字、接口、dictionary、sequence、enum、callback 和 union 基础转换。
- 同步 DOM 方法抛标准错误，fetch 参数错误走 rejected Promise。
- 重写 `tools/extract_interfaces.py`，使用平衡括号扫描全部 renderer core/modules binding。
- 提取 WrapperTypeInfo、父接口、exposure、AttributeConfig、OperationConfig、ConstantValueConfig、callback 参数和 source evidence。
- baseline 覆盖全部 renderer 接口，无法推导的签名明确标记 unresolved/manual review。

### 验证

- 阶段性全量测试从 `30 passed` 增长至 `33 passed`。
- 提取器 fixture 和真实 binding 测试通过，解析失败数为 0。

### 限制

- 复杂 overload、union、dictionary 和构造器行为仍需生成期审核。

## 2026-08-26：动态接口桩与多版本 baseline

### 目标

基于完整 baseline 动态暴露未实现公共接口，同时允许多个 Browser 使用不同 Chromium 快照。

### 修改

- 新增 `interface_runtime.py`，集中生成动态接口类、方法、属性、常量和构造器桩。
- 使用 `loguru.logger.info` 在实际访问桩时记录待实现；真实实现永远覆盖桩。
- 默认 baseline 移入 `pyv8c/baselines/interface-baseline.zip` 并通过 package-data 发布。
- Browser 增加 `interface_baseline_path`、`interface_baseline_info`。
- 新增 `BaselineRepository` 和 Browser 级 `InterfaceUniverse`；动态类和成员配置不写入全局 IMPLEMENTATIONS。
- `_pyv8.Context` 增加 `class_configs` overlay，支持 Context-local 构造器、成员、tag、callback owner 和父模板。
- Worker、Window 和 iframe 使用所属 Browser 的 Universe。
- 删除 Window/self/globalThis 的重复 `context.expose()` 注入；WorkerGlobalScope 补齐 self。

### 提交

- pyv8c：`5b858a8`。
- `_pyv8`：`afe37ad`。

### 验证

- 阶段性测试增长至 `48 passed`，wheel 已确认包含包内 baseline。
- 多 Browser 不同 baseline 的原型、成员和关闭隔离测试通过。

### 限制

- Context 初始化仍会创建大量接口模板，尚未按需懒加载。

## 2026-08-27：Baseline 驱动的 Web IDL BindingPlan

### 目标

删除持续膨胀的手工签名表，并统一 `_pyv8` 元数据命名。

### 修改

- 删除 `_install_signatures()`、模块级 `SIGNATURES` 和 `CONSTRUCTORS`。
- 每个 InterfaceUniverse 从自己的 baseline 编译并缓存 BindingPlan。
- 真实方法、setter 和构造器通过 Context-local overlay 包装，不修改 Python 类或 `__init__`。
- `_pyv8` overlay 增加 `__v8_constructor_adapter__` 和 direct callback 调度。
- `__idl_stub__` 全量改为 `__v8_idl_stub__`；`__v8_direct_callback__` 独立表示调用约定。
- baseline 升级为 schema 3；生成期审核数据放在 `tools/webidl-reviews.json`，最终结果嵌入 baseline。

### 提交

- pyv8c：`373d293`。

### 验证

- pyv8c 阶段性全量测试 `56 passed`。
- `_pyv8` 阶段性全量测试 `163 passed`，扩展构建成功。
- 静态扫描确认生产代码不存在旧签名表和 `__idl_stub__`。

### 限制

- unresolved 签名集中在尚未实现的动态桩，复杂类型仍需扩展提取器或生成期审核。

## 2026-08-27：生命周期、DOM 树和异步边界修复

### 目标

修复代码审查发现的关闭状态、定时器、DOM parentNode、Worker clone/计数和网络重入问题。

### 修改

- BrowserContext/Page 增加严格关闭状态；Page 关闭后从 Context 移除并释放 V8 Context。
- timer heap 改用 monotonic 到期时间，修复 timeout 提前执行和 interval 忙循环。
- DOM mutation 统一 detach/adopt/replace，保证移动、替换和清空后的 parentNode 一致。
- Worker structured clone 改为严格白名单，不再失败时返回原对象；生命周期计数与消息任务分离。
- fixture loader 与无后端错误统一经 Page task queue 回调。
- 补齐 CORS credentials、Cookie path 边界、重复 Set-Cookie 和 cyCronet timeout 签名预检。
- `_pyv8.Context.close()` 增加显式幂等释放；修复 `context.expose()` 错误路径引用清理。

### 提交

- pyv8c：`9ff9954`。
- `_pyv8`：`2024ed6`。

### 验证

- pyv8c：`63 passed`。
- `_pyv8`：`164 passed`，V8 `15.2.20.1`，`build_ext --inplace` 成功。

### 限制

- transfer list、Map/Set/规范循环 clone、完整 CORS/Cookie 和 cyCronet 真机兼容仍未完成。

## 2026-08-27：增量 HTML parser 和脚本调度

### 目标

使脚本在解析过程中按浏览器规则执行：经典外部脚本暂停解析，async/defer/module 使用独立调度路径，动态脚本按连接状态加载。

### 修改

- 新增可恢复 `IncrementalDOMParser`，保存 token cursor 和元素栈。
- `set_content()` 同步推进至 parser-blocking 外部经典脚本；资源完成后通过 Page task queue 恢复解析。
- 外部脚本从开始标签即发现；经典脚本、async、defer 和 module 分别调度。
- defer/module 阻塞 DOMContentLoaded；async 独立执行；资源事件仍影响最终 window load。
- 静态 module import 支持递归加载、依赖优先执行和基础 alias 转换。
- 动态经典脚本默认异步，显式 `async=false` 使用有序队列。
- detached script 不加载；插入当前 Document 后才开始请求或执行。
- 新增 `test_parser_scripts.py` 和 `test_module_scripts.py`。

### 提交

- `aad2c01`：增量 HTML 解析与脚本调度。
- `1db9a10`：detached script 延迟到插入后加载。

### 验证

- pyv8c 全量测试：`66 passed`。
- pyv8c wheel：`pyv8c-0.1.0-py3-none-any.whl` 构建成功。

### 限制

- 当前 module 是静态 import 适配层，不是完整 V8 ES Module 实现。
- dynamic import、import maps、top-level await、document.write、preload scanner 和完整 HTML insertion mode 未实现。

## 2026-08-28：URL 主文档导航

### 目标

设置页面 URL 时自动下载 HTML，并复用既有解析、脚本调度和生命周期实现。

### 修改

- 新增 `Page.goto(url, timeout=30, wait_until="load")`，支持等待 commit、DOMContentLoaded 或 load。
- `BrowserContext.new_page(url)` 在 URL 不是 `about:blank` 时自动导航。
- JavaScript 设置 `location.href` 或 `window.location` 时，将导航投递到 Page 任务队列。
- 主文档使用 `resource_loader` 或 cyCronet 下载，支持响应最终 URL 和 HTTP charset 解码。
- 顶层导航不套用 fetch CORS 检查；子资源继续沿用现有安全策略。
- 新增 `test_navigation.py`，覆盖主文档、外部脚本、Location 导航、charset 和无网络后端错误。

### 验证

- pyv8c 全量测试：`70 passed`。

### 限制

- 默认仍不联网；真实 URL 需要 `Browser(real_network=True)` 和 cyCronet，或显式 `resource_loader`。
- 主文档请求当前为同步入口；真实后端超时和取消能力仍受 cyCronet 适配层约束。

## 2026-08-29：Schema 5 完整 V8 binding metadata

### 目标

补齐后续生成和运行时所需的 Chromium V8 template、拦截器、常量和构造器元数据。

### 修改

- 重写 `tools/extract_interfaces.py`，提取 `identity`、构造器、template、handlers、constants 和 callbacks。
- 新增 schema 5 baseline，记录 named/indexed `SetHandler`、cross-origin access-check、`MarkAsUndetectable`、immutable、global-this 及构造器导出状态。
- 常量同时保留原始表达式和静态解析值，无法解析时标记 `resolved=false`。
- `interface_runtime.py` 支持 schema 5，动态生成普通及 cross-origin interceptor stub，并映射 `_pyv8` class-config overlay。
- `interfaces.py` 增加 template、handler 和 constant metadata 校验。
- `_pyv8/src/pyclass.cpp` 调整 undetectable 与 call handler 安装顺序。

### 验证

- 当前 Chromium snapshot：1254 个接口、12616 个成员、2223 个常量。
- schema 5 可由 `BaselineRepository` 加载，Browser 页面基础接口暴露通过。
- extractor fixture 和动态 stub 测试通过。

### 限制

- 当前工作区需要使用重新编译的 `_pyv8`；Context-local class-config overlay 与 native constants 已作为默认运行时能力启用。
- `_pyv8` 已完成当前源码重编译和 Browser/native smoke 验证。

## 2026-08-29：领域包与运行时编排重排

### 目标

将平铺模块按单向领域依赖重组，降低 Browser/Page、接口 metadata 和网络对象之间的隐式耦合。

### 修改

- 建立 `core`、`bindings`、`dom`、`bom`、`network`、`workers`、`runtime` 包；顶层 `pyv8c` 继续作为公共 API。
- schema 5 成为唯一 baseline 输入，删除 schema 3/4 运行时展开路径。
- interface decorator 不再直接写全局表；bootstrap 显式注册各领域实现并导出只读 registry。
- 新增 `BindingSession`，每个 Window/Worker Context 独立持有 class-config 和 callback owner。
- 删除 `current_page` 线程局部和类级当前页面状态，网络、URL、Worker 构造器通过 `_realm` 注入访问 Page 服务。
- Page 将任务队列与 navigation、lifecycle、script、resource 状态拆入独立控制器；测试重排为 unit/integration/native。

### 验证

- 新增架构测试，冻结顶层 API 并检查 core、领域包和 extractor 的导入边界。
- Chromium 生成绑定重新提取并与发布 baseline 对比一致：1254 个接口、12616 个成员、3105 个 unresolved signature。
- `python -m py_compile` 和 wheel 构建通过；架构和提取器测试通过。
- 当前 shell 未配置可用的完整 pyv8/gevent 运行时；预编译 `_pyv8` 在连续 V8 Context 创建时触发 native access violation，因此完整 pytest 需在项目标准 native 环境复跑。

## 2026-08-29：单页启动优化第一阶段

### 修改

- `InterfaceUniverse` 增加成员索引、稳定 Window 名称表和固定核心预热集合。
- `BindingSession` 增加 `interface_names()`、`materialize()`、`materialize_constructor()` 和 materialized 统计；动态接口不再在 Page 创建时批量生成。
- `Window` 使用现有 `_pyv8` named property handler 按首次访问生成 constructor/template，同时保持完整 Window 名称枚举。
- `Browser` 增加 `startup_stats`、`eager_interfaces` 和 `strict_validation` 选项；默认延迟全量校验，`PYV8C_STRICT_VALIDATION=1` 启用严格模式。
- `BaselineRepository` 增加基于文件 stat 的进程内缓存，重复 Browser 不再重复解析和冻结相同 baseline。
- 新增 `tools/benchmark_startup.py`，支持独立进程冷启动 p50/p95 测量。

### 当前边界

- Python 层 lazy exposure 已接入现有 native handler；`_pyv8` 批量 Context API 和 V8 Snapshot 尚未实现。
- 完整运行时基准需要项目标准 `_pyv8`/gevent 环境；当前 shell 只完成静态检查和 Universe 层验证。
## 2026-08-29: prebuilt _pyv8 fallback and startup measurement

- Context-local class-config mappings are now a required `_pyv8` runtime contract.
- Windows prebuilt smoke measurement: Browser + new_context + new_page + close is about 620ms in the current environment, with 10 core interfaces materialized at page creation.
- `_pyv8/src/context.cpp` retains Context-local class-config mappings; `_pyv8/src/pyclass.cpp` installs undetectable templates before call handlers.

### Native rebuild verification

- Installed `gevent` with `python310 -m pip install gevent`.
- Rebuilt `_pyv8` with `python310 setup.py build_ext --inplace --force`; Context-local class-config overlays are required at runtime.

## 2026-08-30: remove live-config capability probing

- Removed `Context.supports_live_class_configs` and the associated feature probe.
- Page and Worker realms now always pass their independent `BindingSession.configs`
  mapping to `_pyv8.Context`.
- Removed the legacy Python class-overlay/isolation fallback. Use a rebuilt `_pyv8`
  with Context-local `class_configs` support.
- `tools/benchmark_startup.py --runs 5`: p50 `378.9ms`, p95 within the `0.6s` target.
- `pyv8c` full suite: `76 passed`; `_pyv8` full suite: `164 passed`.
- Native smoke: `1 passed`; Event/Node constants resolve to their Chromium values with the default configuration.

## 2026-08-30: strict compact baseline marker

### Changes

- Added mandatory `_webidl.compact` metadata. Only `compact: true` baselines are accepted by the runtime.
- Audit output from `tools/extract_interfaces.py --audit` is marked `compact: false` and is rejected by `BaselineRepository`.
- Removed all runtime compatibility branches for missing/legacy compact markers and audit-only callback fields; invalid input raises immediately.
- Regenerated the bundled schema 5 baseline with callback names, default worlds, getters, readonly duplication, and provenance omitted.

### Verification

- Extractor unit tests: `7 passed`.
- `compileall` and `git diff --check` passed.
- Full integration collection was not available in the current shell because the `pyv8` native module is not installed.

## 2026-08-30: generated constant value resolution

### Changes

- The extractor now scans generated V8 headers for `static constexpr` constants
  such as `V8GPUBufferUsage::Constant::kCopySrc = 4`.
- Constant expressions are evaluated through a restricted numeric expression
  parser supporting literals, casts, arithmetic, bitwise operators, shifts and
  references to other generated constants; arbitrary code is never executed.
- Resolved values take precedence over unresolved C++ expressions while the
  original `expression` remains in the baseline for auditability.

### Verification

- `GPUBufferUsage`, `GPUTextureUsage` and `GPUColorWrite` constants now contain
  their real numeric values.
- Current baseline: 2,223 constants, 0 unresolved constants.
- Extractor unit tests: `8 passed`.

## 2026-08-30: compressed bundled baseline

### Changes

- Bundled runtime metadata is now published as `pyv8c/baselines/interface-baseline.zip`.
- `BaselineRepository` accepts `.zip` and `.json` inputs. ZIP inputs are expanded
  to a same-directory JSON cache once, then future loads use that cache.
- The extractor keeps JSON output as its default. A `.zip` output path or the
  optional `--zip` flag writes the one-member compressed release artifact.
- Package data now includes only baseline ZIP files; extracted JSON caches are ignored.

## 2026-08-31: startup and runtime performance round

### 目标

落地已确认的 P0 四项 + P1 三项优化：native 接口懒物化、Worker 关闭全量物化、
`core.realm` 服务协议、Universe/Session 双缓存合并、JS helper 一次性编译、
`validate_arguments` 预编译、baseline 二进制缓存与索引。

### 修改（pyv8c）

- `core/realm.py`：新增 `RealmServices` Protocol（`make_promise` / `js_error_value` /
  `post_task`）与 `resolve_realm()`，bindings 层不再直接访问 `runtime` 对象。
- `bindings/universe.py`：
  - `BindingSession.lazy_factory()` / `lazy_names()` / `materialize()` /
    `materialized_interfaces()`，供 native 惰性访问器回调；`_materialized` 改按接口名记录。
  - 删除 `InterfaceUniverse._configs` 与 `class_config_for()`，config 拆为 realm 无关
    （Universe 级共享）与 realm 相关（Session 级合成）。
  - handler stub 按 `spec["template"]["handlers"]` 实际声明生成，不再固定 20 个。
  - `BaselineRepository` 新增带 `schema + sha256` 校验头的 pickle 二进制缓存
    （`.pyv8ccache`），命中即跳过 JSON 解析、接口投影与父链成环检查；损坏时静默回落。
  - `_member()` 兜底改查 parent → children 邻接表子树；`exposed_names()` 改用
    `exposed_in` 倒排索引，两者均保持文档序输出。
  - JS helper（`_make_promise` / `_js_error_value` / `promise_resolve`）改为初始化时
    一次性预编译 `pyv8.Script` 缓存，消除异步路径每次调用的 V8 编译。
- `bindings/webidl.py`：`MethodSpec.required_arg_count` 预计算；`BindingPlan.overload_plans`
  编译期生成；`_convert` 改 dict 分派并保留枚举/ByteString 约束；`_interface` 先试注册
  实现再走 MRO；union 按运行时形状先试匹配分支。
- `bindings/registry.py`：`depth()` 复用 universe 深度表。
- `runtime/browser.py`：删除失效的 `PYV8C_DISABLE_LAZY_BOOTSTRAP` 引导块与调试 `print`；
  `Page` 向 `pyv8.Context` 传 `lazy_names` + `lazy_factory`；启动统计新增
  `lazy_registered` / `lazy_materialized` / `lazy_register_ms`。
- `network/api.py`：Worker 的 `expose_interfaces` 改为 `lazy_interfaces=True` 并注册惰性名字。
- `workers/api.py`：`WorkerScope` 实现 `RealmServices`。
- 新增测试：`tests/integration/test_lazy_interfaces.py`、
  `tests/unit/test_webidl_precompiled.py`；`test_architecture.py` 增加 bindings 不得
  import runtime、不得引用 `_make_promise` 的边界断言。

### 修改（`_pyv8`，独立仓库）

- `Context.__init__` 新增可选 `lazy_factory` kwarg；新增 `Context.declare_lazy(names)`。
- `ConstructAccessorNameGetterCallback` miss 分支回调 `lazy_factory`，复用
  `py_class_to_template` 构建并回填模板缓存；工厂返回 `None` 或抛错时保持返回
  `undefined` 的既有行为。不传 `lazy_factory` 时行为完全不变。
- `context_dealloc` 增加 `Py_CLEAR(self->lazy_factory)`。
- 新增 `tests/test_lazy_construct.py`（13 个用例）。

### 验证

- `_pyv8`：`177 passed`。
- `pyv8c`：`108 passed`，1 个 pre-existing failed（`test_baseline_versions` 的
  empty-`{}` 用例受 git 外 temp 目录权限影响，与本轮无关）。
- 启动基准 `python tools/benchmark_startup.py --runs 5`：p50 `98.2 ms`
  （懒物化接线前 `378.9 ms`）。
- baseline 二进制缓存：命中 `21.3 ms` vs 冷加载 `73.5 ms`；损坏缓存回落 JSON 并重建。
- `git diff --check` 通过。

### 限制

- 惰性访问只在 JS 首次访问该名字时物化；`Object.getOwnPropertyNames(window)` 依旧覆盖
  全部暴露名（不可枚举属性），物化发生在取值时。

## 2026-08-31: Page 门面拆分（方案 B）

### 目标

`runtime/browser.py` 此前 905 行、`Page` 单类约 726 行 / 79 个方法，五种职责
（导航、解析、脚本、资源、生命周期）物理混在一起。`controllers.py` 里的
controller 只是纯状态容器。本轮把逻辑搬进 controller，`Page` 收敛为薄门面。

### 修改

- `runtime/state.py`（新增）：`StateAlias` 描述符从 `controllers.py` 移入，
  继续把 controller 状态暴露在 `Page` 上（`_has_navigation` 等用法不变）。
- `runtime/navigation.py`（新增）：`NavigationController`——导航（`goto` /
  `set_content` / `commit_content` / `request_navigation` / `navigation_allowed` /
  `dispatch_beforeunload` 调用链）与文档解析（`resume_parser` /
  `parser_is_blocked` / `finish_document_parse` / `parser_script_start` /
  `parser_script_complete` / `element_class`）。
- `runtime/lifecycle.py`（新增）：`DocumentLifecycle`——事件分派
  （`dispatch_page_event` / `dispatch_script_error` / `dispatch_beforeunload`）与
  ready-state 迁移（`maybe_fire_dom_content_loaded` / `maybe_fire_document_load` /
  `when_loaded`）。
- `runtime/scripting.py`（新增）：`ScriptController`——脚本执行与模块适配
  （`execute_script` / `execute_module` / `evaluate`）、解析/动态脚本协调
  （`drain_script_queue` / `run_deferred_scripts` / `node_adopted` /
  `install_inline_handler`）、realm JS helper（`make_promise` /
  `js_error_value` / `promise_resolve` 及 `_js_script_cache`）。
- `runtime/resource_loading.py`（新增）：`ResourceController`——同步/异步加载
  （`load_resource` / `load_resource_async` / CORS 策略）、子资源追踪
  （`discover_element_resource` / `track_document_resource` /
  `complete_document_resource` / iframe / worker source）。
- `runtime/browser.py`：`Page` 保留 `__init__`、`StateAlias`、`url`、`close`、
  任务门面与被外部调用的公开方法（每行转发到对应 controller）；私有逻辑方法
  全部搬走。`Browser` / `BrowserContext` 不变。
- `runtime/controllers.py`（删除）：逻辑与状态已拆入上述四个文件，无其他引用。

controller 之间通过 `self.page.<sibling>` 协作（如资源完成回调脚本队列与
生命周期）；`resource_loading.py` 创建子页时延迟导入 `Page` 以避免循环导入。

### 验证

- 全量 `84 passed`（`--ignore` 三个 baseline 环境问题文件后 exit=0）；排除后与
  重构前 108 总数守恒（三个文件含 12+10+2 个测试）。
- 三个 baseline 文件（`test_baseline_versions` / `test_interface_extractor` /
  `test_fetch_chromium`）未改动，其 WinError 448 / empty-`{}` 失败为 Windows
  junction 清理的既有环境问题，与本轮无关。
- 启动基准 `--runs 5`：p50 `97.7 ms`（拆分前 `107.6 ms`，无回退）。
- 修复过程记录：首轮遗漏 `_discover_element_resource` 门面转发导致解析中断
  （`document.body` 为 null），补上后受影响的 7 个集成文件 26 个用例全过。

### 限制

- `Page` 实例仍保留全部 controller 引用与状态别名，属性空间未变；只是源码
  按职责分文件，行为零变化。
- 若后续要把外部调用方改为直接使用 controller（`page.navigation.goto(...)`），
  需另行评估，属二期。

## 2026-08-31：冗余清理第二轮（死参数与死代码移除）

### 目标

清除上一轮拆分后遗留的恒真参数、无引用 helper 和 `__import__` hack，收敛接口
注册表面的无效自由度。

### 修改

- `bindings/universe.py`：删除恒为 `True` 的 `lazy_interfaces` 参数；
  `_resolve_names` 恒走 eager 名单；`_stub_handler` 收敛为 enum 拦截器返回 `()`、
  其余返回 `None`。
- `bindings/registry.py`：`InterfaceSpec` 与 `interface()` 装饰器删除无消费者的
  `constants` 字段及对 baseline 的重复校验；`expose_interfaces` 删除
  `lazy_interfaces` 参数。
- `runtime/browser.py`：`InterfaceUniverse` 构造不再传 `lazy_interfaces`；
  `_run_callback` 内联进 `_run_queued_callback`。
- `network/api.py`：Worker 暴露接口不再传 `lazy_interfaces`；删除
  `_runtime_finished`，`fn_terminate` 与生命周期回调直连 `_finish_lifecycle`。
- `bom/api.py`：`History.fn_go` 改用直接导入的 `Event`，移除 `__import__`
  hack；`URL._resolve` 异常路径去掉冗余 `isinstance` 判断。
- `_pyv8/pyv8/tools.py`：删除全库无引用的 `impl_warp`、`attr_warp`、
  `CtxTypeIndex` 并同步 `__all__`；`pyv8/__init__.py` 的 `_HELPER_EXPORTS`
  同步收敛。
- `tests/integration/test_baseline_versions.py`：`{}`-empty 用例的错误 regex
  与 `baseline.py` 实际错误信息对齐（`must contain _webidl metadata`），
  消除上一轮记录的遗留失败。

### 验证

- `pyv8c` 全量 `109 passed`（`PYTHONPATH` 指向本地 `_pyv8`），上一轮的
  1 个 baseline regex 失败已清零。
- `startup_stats` 中 `lazy_interfaces` 键保留为惰性物化计数指标，
  与被删除的同名参数无关。

### 限制

- `InterfaceSpec` 形状变化属内部 API，无兼容承诺；外部若以关键字传入
  `constants` / `lazy_interfaces` 会直接 `TypeError`。

## 2026-08-31：恢复 lazy_interfaces 开关并修复传参链路

### 目标

上一轮把 `lazy_interfaces` 当恒真参数删除，实际它承载「全量物化
（`lazy_interfaces=False`）」模式。本轮恢复该开关，修完整条传参链路，
并消除全量物化路径上的 native 崩溃。

### 修改

- `bindings/universe.py`：`InterfaceUniverse` 恢复 `lazy_interfaces` 参数；
  `BindingSession.lazy_names()` 在 `lazy_interfaces=False` 时返回空元组
  （全部接口已随 `class_configs` 物化，`declare_lazy` 不再对已暴露名字重复装
  accessor）；`_resolve_names` 条件改写为正向表述。
- `bindings/registry.py`：`expose_interfaces` 删除 `lazy_interfaces` 参数，
  直接读 `universe.lazy_interfaces`（消灭 `is not False` 判断与调用方重复传参）。
- `runtime/browser.py` / `network/api.py`：Page 与 Worker 调用
  `expose_interfaces` 时不再传 `lazy_interfaces`。
- `_pyv8/src/pyclass.cpp` + `pyclass_handlers.cpp`（新增 `py_class_noop_call`）：
  修复 undetectable 模板缺 instance call handler 的 V8 CHECK 崩溃
  （`Check failed: !IsUndefined(obj->GetInstanceCallHandler())`）。V8 要求
  undetectable 对象（`document.all` 式 legacy 接口，唯一实例为
  `HTMLAllCollection`）带 call handler；无真实 `__call__` 时 native 安装 no-op
  handler，调用返回 `undefined`。此前任何 undetectable 类一实例化即进程崩溃，
  与惰性/全量路径无关。

### 验证

- 最小复现确认：仅 `__v8_undetectable__ = 1` 的类 expose 即崩溃；修复后通过。
- `Browser(lazy_interfaces=False)` 全量物化路径：`lazy_names()` 为空、
  1045 个 Window 接口直接可访问（`typeof Storage` / `AbortController` /
  `HTMLAllCollection` 均为 `"function"`）、DOM evaluate 正常。
- 默认惰性路径不回归：`lazy_names("Window")` 非空、首访物化正常。
- `_pyv8` 重建后 `177 passed`；`pyv8c` 全量 `109 passed`。
- 启动基准 `--runs 5`：p50 `91.4 ms`，无回退。

### 限制

- no-op call handler 使无 `__call__` 的 undetectable 对象可调用并返回
  `undefined`；真实实现 `document.all` 时应提供真正的 `__call__`。

## 2026-09-01：架构拆分、native 配置清理与发布同步

### 修改

- 新增 `bindings/catalog.py`、`bindings/config.py`、`bindings/wrappers.py` 和
  `bindings/schema.py`，将 baseline 索引、模板 flag、签名缓存和 baseline 写出职责拆开。
- `InterfaceUniverse` 保留 facade，复用独立 catalog 和签名缓存；extractor 复用统一的
  schema 写出入口。
- Page 新增资源登记/移除方法，WebSocket、Worker 和 iframe 资源不再跨层访问 Page 私有集合。
- `_pyv8` 新增 `src/pyclass_config.cpp/.h`，移除 `pyclass.cpp` 中重复的 ClassConfig 读取实现。

### 验证

- `_pyv8`：`python -m pytest -q`，`177 passed`。
- `pyv8c`：使用本地 `_pyv8` 扩展运行全量测试，`112 passed`。
- `HTMLAllCollection` 复现脚本输出正常，未再触发
  `Check failed: !IsUndefined(obj->GetInstanceCallHandler())`。
- native 扩展通过 `build_ext --inplace --force` 重建。

### 提交与发布

- `pyv8c`：`1980864 refactor: split binding catalog and page resource ownership`。
- 文档：`538f98f docs: update handoff and worklog`。
- `_pyv8`：`429ec5e refactor: isolate native class configuration loading`。
- 两个提交均已推送到 GitHub，远端分支与本地 HEAD 一致，工作区干净。

## 2026-09-01：实现 document.all（HTMLAllCollection）

### 修改

- `dom/collections.py`：新增手写 `HTMLAllCollection`（无 IDL 父接口，tag 为
  `HTMLAllCollection`），提供 `length`、`item()`、`namedItem()`、legacy `__call__`
  以及 baseline 声明的 named/indexed interceptor。
- `dom/model.py`：`Document.all` 由 stub 改为真实 getter，返回按文档缓存的 live
  `HTMLAllCollection`；新增 `_materialize_interface()` 在首次使用时物化惰性接口，
  不扩大默认 eager 集合。
- `bindings/universe.py`：baseline `call_handler` 不再无条件覆盖 `__call__`——
  手写 legacy caller（`document.all`）优先，只有没有真实 `__call__` 的接口才安装
  no-op handler。这消除了上一轮记录的限制。
- `tests/browser/`（新增目录）：浏览器对象类型与功能检查，`test_collections.py`
  按类型、使用、等待三组覆盖 `document.all`。

### 行为要点

- 类型：`typeof document.all === "undefined"`（undetectable）、
  `[object HTMLAllCollection]`、`instanceof HTMLAllCollection`、
  falsy 且 `== null`、`new HTMLAllCollection()` / `HTMLAllCollection()` 抛
  `Illegal constructor`。
- 使用：索引按树序、`item()` 字符串参数走命名查找、`namedItem()` 少参抛
  `TypeError`、重复 id/name 返回 live `HTMLCollection`、未命中时属性访问为
  `undefined` 而 `item()`/`namedItem()` 为 `null`、legacy 调用 `document.all(name)`。
- 命名属性遵循 `[LegacyUnenumerableNamedProperties]`：可访问但
  `Object.keys` / `Object.getOwnPropertyNames` / `for-in` 只暴露索引。
- 等待：视图 live，`setTimeout` 任务、`run_until_idle()` 之后的新增/删除/改 id
  立即可见，缓存引用与 `document.all` 始终一致。

### 验证

- `pyv8c`：全量 `142 passed`（原 112 + 新增 30）。
- 覆盖三条绑定路径：默认惰性（首次访问 `document.all` 时物化）、
  `lazy_interfaces=False` 全量物化、`strict_validation=True`。
- 启动基准 `--runs 10`：p50 `100.5 ms`（本次会话 baseline 二进制缓存缺失，
  `baseline_decode_ms` 约 27 ms；eager 集合与 `lazy_register` 未受影响）。

## 2026-09-01：document.cookie 与 context 级 CookieJar

### 修改

- `Document.prototype.cookie` 接入 `BrowserContext.cookies`，同一 context 的 Page 共享
  cookie，真实网络后端启用时复用同一 CookieJar。
- `network/backend.py` 扩展 CookieJar：支持默认路径、domain/path 匹配、Secure、HttpOnly、
  Max-Age、Expires、过期清理、稳定排序和脚本对 HttpOnly cookie 的覆盖保护。
- 非 HTTP(S) URL 的 document cookie 读取为空，写入不改变状态。实际状态变化时使用
  `logger.debug` 记录当前页面可见的 cookie 字符串。
- `tools/webidl-reviews.json` 和 bundled baseline 为 `Document.cookie` setter 添加了
  一个必需 `DOMString` 参数的已审核签名，严格校验路径可用。
- 新增 `tests/browser/test_document_cookie.py`；通过标准库 `ThreadingHTTPServer` 的本地
  回环地址执行导航测试，避免依赖 `example.test` 或公网网络。

### 验证

- `tests/browser/test_collections.py`、`tests/browser/test_document_cookie.py` 与既有
  CookieJar 集成测试：`56 passed`。
- `pyv8c` 全量：`161 passed in 28.72s`，使用本地 `_pyv8` 扩展。
- `python -m py_compile` 与 `git diff --check` 通过。
## 2026-09-01：Browser 环境参数

- 新增不可变 `BrowserEnvironment`、`ViewportConfig`、`ScreenConfig` 和
  `GeolocationConfig`，由 `Browser(environment=...)` 提供默认值，
  `BrowserContext.new_page(environment=...)` 支持页面级 Mapping 覆盖。
- Navigator、Window viewport、Screen、iframe 和 Worker 都读取同一 Page 快照；
  默认网络头会补充 `User-Agent` 与 `Accept-Language`，显式 headers 优先。
- `navigator.geolocation` 使用配置坐标并经 Page 任务队列异步回调；`timezone`
  当前仅为每个 Browser/Page 的元数据，不修改进程级 ICU/Intl 状态。

## 2026-09-01：Navigator plugins

- `BrowserEnvironment.plugins` 新增不可变 `PluginConfig` 与 `MimeTypeConfig`。
  默认值按当前 Chrome 的内置 PDF Viewer 快照提供五个 Plugin 和两个 MIME type。
- 新增浏览器兼容的 `PluginArray`、`Plugin`、`MimeTypeArray`、`MimeType`：支持
  索引、命名访问、迭代、只读描述符和 `PluginArray.refresh()`，同时保留 Chrome
  的 Plugin MIME 对象与 `navigator.mimeTypes` 对象身份差异。
- 抽取 `core.indexed_properties.ReadOnlyIndexedPropertyMixin`，消除 BOM 对 DOM
  私有集合实现的反向依赖。
## 2026-09-02：pyv8c DevTools 调试模式

### 实现

- 新增 `runtime/devtools.py`，基于 `_pyv8.Debugger` 和 V8 Inspector 提供非阻塞 WebSocket/CDP bridge。
- `BrowserContext.start_devtools(page, host="127.0.0.1", port=0)` 返回 `DevToolsHandle`，支持 `url`、`host`、`port`、`close()` 和 `wait()`。
- 实现 `Target.getTargets`、发现、attach/detach、activate、close、auto-attach，以及 `Runtime.*`/`Debugger.*` 消息路由。
- 主页面和 iframe 使用独立 Inspector Target；Worker 的调试命令通过 Worker 所在线程队列执行，避免跨线程直接访问 V8 Isolate。
- 页面、iframe、Worker、Context 关闭时自动清理 Target；同一 Context 只允许一个调试句柄，第二个 WebSocket 客户端以 `1013` 关闭。
- 更新 `pyproject.toml`，将 `websockets>=14` 纳入运行依赖，并在 README 增加使用示例。

### 验证

- `tests/browser/test_devtools.py`：4 passed。
- pyv8c 全量测试：`181 passed`。
- `git diff --check`：通过。

### 边界

- 当前协议范围是 V8 原生 Runtime/Debugger 加最小 Target 路由，不实现完整 Chrome 浏览器级 `Page`、`Network`、`/json` 发现接口。
- 一个调试句柄只允许一个 Chrome DevTools WebSocket 客户端；调试范围是指定 Page 及其 iframe/Worker 后代，不包含同一 Context 的其他独立 Page。
### 首脚本暂停与 Inspector 调度

- `_pyv8/src/debugger.cpp` 新增 `schedule_pause_on_next_statement()`、`disconnect()`，修复空 Inspector 消息解引用，并重建原生扩展。
- `pyv8c/platform/devtools.py` 增加 `start_paused`、`startup_timeout`、`wait_for_connection()`、启动闸门和暂停队列。
- Page/iframe/Worker 的 CDP 命令改为通过 Target 队列串行处理；Worker 在创建 Inspector 后才执行启动脚本。
- `Page` 增加 V8 访问锁，`NavigationController` 在首个脚本前等待调试闸门。
- 新增首脚本等待、暂停和恢复测试；专项测试 `6 passed`，手工 WebSocket 流程验证通过。
## 2026-09-02: PageRealm extraction and second-stage boundary cleanup

- 将 `Page` 的 V8 Context 创建、`BindingSession`、接口暴露与 lazy 注册集中到 `platform.browser.PageRealm`；`Page` 保留 `context`、`binding_session` 等兼容门面，关闭顺序统一经 realm 执行。
- 架构测试改为检查 `foundation -> engine -> web -> platform` 依赖方向，并扫描新目录中的 extractor 与领域代码，禁止访问 Page/Context 私有资源状态。
- 完整 `pyv8c` 测试：`189 passed`。
- 独立进程冷启动基准（20 次）：p50 `326.1 ms`，p95 `345.8 ms`；懒加载断言保持 `lazy_materialized == 0`，eager/lazy 注册计数未改变。
- 将 Worker Isolate/Context、BindingSession、接口暴露与销毁迁移到 `platform.realms.WorkerRealm`；Worker 线程仅保留消息循环和生命周期回调。专项 Worker/DevTools 测试与 native smoke 通过。
- WorkerRealm 接入后的独立进程冷启动复测（20 次）：p50 `247.1 ms`，p95 `293.7 ms`。
- 代码审查清理：移除 `platform.browser` 已无使用的 V8/接口暴露导入，消除 `PageRealm` 重复导出和未使用的 eager 配置变量；清除本地生成的 baseline JSON/cache 与 wheel 检查目录。目标源码包仅保留 `foundation`、`engine`、`web`、`platform` 及 `baselines`。
- 按 Web 能力拆分网络与 Worker：Fetch 数据类型、事件、XHR 分别位于 `web/fetch` 子模块；HTTP/Cookie/WebSocket 位于 `web/network`；Worker API 与线程 runtime 位于 `web/workers/api.py`、`web/workers/runtime.py`。`fetch/api.py` 仅保留聚合导出。
- 拆分后完整测试：`189 passed`；冷启动复测（20 次）p50 `248.0 ms`、p95 `290.1 ms`，eager `18`、lazy 注册 `1028`、冷启动 `lazy_materialized == 0` 保持不变。
- 最终移除旧的 `web/fetch/api.py` 聚合文件，改由 `web/fetch/__init__.py` 直接导出拆分后的 API；架构守护测试新增后完整套件为 `190 passed`。

## 2026-09-03：结构性债务重构（registry 去全局化 + PageServices 边界）

### 目标

消除架构评审确认的两项结构性缺点：registry 进程级全局与 Page 上帝对象。项目处于开发阶段，无兼容性负担。

### 修改

- **Phase 1 registry 去全局化**：删除 `engine/registry.py` 模块级 `REGISTRY`/`INTERFACES`/`IMPLEMENTATIONS`；`InterfaceUniverse` 必填参数持有冻结 `InterfaceRegistry`，`validate_registry` 改为实例方法 `InterfaceRegistry.validate`，`expose_interfaces` 经 `universe.registry` 取用；`bootstrap_interfaces()` 返回模块级缓存的冻结默认注册表；`engine/webidl.py` 校验改收 registry 参数；顶层 `INTERFACES`/`IMPLEMENTATIONS`/`validate_registry` 公共导出删除。
- **Phase 2 Page 内部收敛**：任务错误收集（`_run_callback`/`_run_queued_callback`）移入 `TaskQueue.run_callback`；三类 owned 集合收敛为 `platform/state.py` 的 `Ownership`；worker 配置 dict 改为 frozen `WorkerContext` dataclass；`_record_lazy_materialized` 移入 `PageRealm`。
- **Phase 3 PageServices 边界**：新增 `platform/services.py` 的 `PageServices`（实现 `RealmServices`/`ResourceServices` 协议，不持有 Page 引用）；web 层全部对象改收 services；`Page` 移除约 45 个 web 服务 facade（navigation/scripting/resource/task 转发、realm service boundary、ownership tracker），仅保留 `goto`/`set_content`/`evaluate`/`run_until_idle`/`close` 与 `url`、`binding_session`；controllers 间直接互访（`page.tasks.post`、`page.context_owner`、`page._owned`）；iframe 子页面创建改在 `ResourceController` 内直接构造 `Page`。
- **Phase 4 架构测试与文档**：`test_architecture.py` 新增 `PageServices` 结构性窄面断言（不得暴露 `context_owner`/`network_backend`/`binding_session`/`page`）、`Page` 用户 API 白名单断言（web 服务 facade 不得回流）；web 层禁止 token 追加 `binding_configuration(`、`resource_loader`；更新 `foundation/realm.py` 协议文档。

### 验证

- 全量测试：`192 passed`（新增 2 项架构测试，含原有 190 项）。
- 启动基准（20 次）：重构后 p50 `108.4 ms` / p95 `123.9 ms`；stash 对照重构前 p50 `111.1 ms`，无回退（与历史 `97 ms` 基线差异为当日机器状态漂移）。
- `rg`：web 层零命中 `context_owner`/`network_backend`/`binding_session`/`resource_loader`；platform 层无残留 facade 调用；`REGISTRY`/`INTERFACES`/`IMPLEMENTATIONS` 全局仅剩注册表内部实现。

### 限制

- `PageServices` 仍是显式协议面而非接口自动推导；新增 web 服务需求需同步扩展协议与实现。
- `worker_configuration` 组装保留在 `PageServices`（web 层经 services 调用获取 `WorkerContext`），未下沉到 Worker 创建链直接传参。
- 本轮重构未提交 Git；`platform/services.py` 为新增文件。

## 2026-09-03：web 层服务引用命名收敛

### 目标

清除 PageServices 边界重构遗留的命名债：web 层对象实际持有的是 PageServices，但构造参数叫 `page=`、属性叫 `self._page`，阅读时会产生持有平台 Page 的错误心智模型。

### 修改

- web 层 13 个文件（dom/model、elements、events、collections、selectors、parser，bom/api、navigator，fetch/request、response、xhr，network/websocket，workers/api、runtime）统一改为构造参数 `services=`、属性 `self._services`；parser 与 WorkerRuntime 的 `self.page` 同步改为 `self.services`。
- `foundation/realm.py` 的 `realm_of()` 回退属性从 `_page` 改为 `_services`，与注入通道 `_realm` 的文档说明同步更新。
- 顺带修复 iframe 边界泄漏：`HTMLIFrameElement._child_page` 原持有真实子 `Page`，改为 `_child_services` 持有子页面的 services，web 层不再触碰平台 Page。
- 架构测试 `test_domain_code_uses_page_resource_ownership_api` 追加 `self._page`/`self.page` 禁止 token；`test_baseline_versions.py` 改经 `page._owned.frames` 取子页面。
- 归一化 `web/dom/events.py` 行尾为 LF（仓库内最后一个 CRLF 文件）。

### 验证

- 全量测试 `192 passed`；`git diff --check` 干净。
- `rg 'self\._page\b|\bpage=self\.'` 在源码目录零命中；web 层无任何 Page 引用。

### 限制

- engine 注入通道 `_realm` 参数名保留（属 binding engine 约定，与显式 `services` 参数并存于 Request/Response/XHR/WebSocket/Worker 构造器）。
- 本次为纯命名/边界收敛，未改变任何运行时行为。
## 2026-09-03：InterfaceUniverse 拆分为组合根 + 协作者模块

### 目标

消除 engine 层最后的结构性债务：`universe.py`（700 行）单类混合 baseline 查询、binding plan、stub 生成、wrapper 生成、class config 装配五种职责。拆分后 `InterfaceUniverse` 降级为纯编排组合根，对外 API 完全不变（platform/registry/测试零改动）。

### 修改

- 新增 `engine/stubs.py`：`StubFactory`（原 6 个 `_stub_*` 工厂 + `_log`）与模块级 `mark_direct`。
- 扩展 `engine/wrappers.py`：`BindingWrapperFactory`（原 `_method_wrapper`/`_setter_wrapper`/`_constructor_adapter`/`_reject_conversion`/`_raw_callback`），`SignatureCache` 移入工厂。
- 新增 `engine/assembly.py`：`ClassConfigBuilder`（原 `_declared_handlers`/`_member_configs`/`_class_config_for` + `_handler_cache`）、模块级 flag 转换函数、`HANDLER_FAMILIES`/`CROSS_ORIGIN_FAMILIES`/`own_configs`/`declares_callback`/`raw_callback`。
- 新增 `engine/sessions.py`：`BindingSession` 整体迁移；`class_configs` 内联原 `universe._class_configs` 批量物化。
- `engine/universe.py` 700→205 行：`__init__` 组装 stubs/wrappers/assembly 三协作者；`_resolve_names`/`_walk_parent_names` 公开化为 `resolve_names`/`walk_parent_names`；`class_config_for(name, cache=None, realm=None)` 双用法（检视/物化）；删除死方法 `_interface_positions`/`_children_of_index` 与 `_descendants` 薄委托（`_member` 直用 catalog）；`BindingSession` re-export。
- 架构测试：`test_binding_facade_uses_separate_catalog_and_schema_helpers` 断言更新到新模块；新增 `test_universe_stays_a_composition_root` 防工厂方法体回流。

### 验证

- 全量测试 `193 passed`（192 基线 + 1 新守护）。
- 启动基准 `p50=95.7ms`（对照重构前当日基线 ~108ms，无回退）。
- `rg` 确认 universe.py 无 `_stub_method`/`_member_configs`/`_class_config_for`/`class BindingSession` 残留；全部文件 LF 行尾；`git diff --check` 干净。

### 限制

- `_realm` 注入协议、10 项 attribute tuple wire shape、stub 日志格式逐字节保持，本轮零行为变更。
- `ClassConfigBuilder`/`BindingWrapperFactory` 持有 universe 实例引用（构造注入），与 Page→controllers 模式一致。
- callback_owner 动态类名后缀改用 builder 实例 `id`（原 universe `id`），每 universe 一 builder，唯一性语义等价。
## 2026-09-03：拆分遗留小气味修复（协作者私有访问 + realm 四步重复）

### 目标

清除上轮 InterfaceUniverse 拆分引入/暴露的两个小气味：`assembly.py` 跨模块访问 universe 私有方法；`PageRealm`/`WorkerRealm` context 创建尾部四步模式重复。

### 修改

- `engine/universe.py`：`_spec`/`_own_members` 公开化为 `spec`/`own_members`（协作者查询面，加 docstring），全部内部调用点同步；`_member` 保持私有（仅 `binding_plan` 内部使用）。
- `engine/assembly.py`：5 处 `self.universe._spec`/`._own_members` 改公开调用。
- `platform/realms.py`：提取模块级 `_context_kwargs`（hook/ctx_type/class_configs/lazy_factory 装配）与 `_declare_realm_interfaces`（expose_interfaces + declare_lazy 尾部），两个 realm 共用；`PageRealm` 保留启动计时与 faker hook 语义，`WorkerRealm` 保留 isolate 生命周期。
- 架构测试：新增 `test_universe_collaborators_use_public_surface_only`，禁止 stubs/wrappers/assembly/sessions 出现 `self.universe._`。

### 验证

- 全量测试 `194 passed`（193 基线 + 1 新守护）。
- 启动基准 `p50=96.9ms`，无回退。
- `rg` 确认无私有访问残留；`git diff --check` 干净。

### 限制

- platform 层 controllers 访问 `page._owned` 等组合根私有属既定设计模式（同层组合根），未纳入本轮范围。

## 2026-09-03：DevTools 事务与统一资源作用域

### 目标

落地 P1 中的两项架构改造：CDP 命令统一事务化，并将网络、iframe、Worker 资源统一管理。

### 修改

- 新增 `CommandEnvelope`，统一承载 request/session/target 标识、取消状态和 `Future`；Inspector 响应根据 target 匹配并完成对应事务。
- 目标关闭、Debugger 断开或执行失败时，所有挂起事务均以明确 CDP 错误完成，不再静默丢失。
- `Runtime.runIfWaitingForDebugger` 在首脚本闸门中投递到 Realm owner 线程，可在保持 WebSocket 连接时释放页面。
- 新增 `ResourceScope.remove()`；Page、iframe、Worker、WebSocket 和 Fetch/XHR 请求均注册到 Page 作用域，请求完成后注销，关闭时统一取消。
- 后台执行器仅自动消费定时器；网络和 Worker posted 回调仍保持 `run_until_idle()` 边界。

### 验证

- `python -m pytest -q`：`202 passed`。
