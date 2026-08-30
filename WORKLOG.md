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

- 当前工作区使用旧预编译 `_pyv8`；常量 native descriptor 默认关闭，重新编译后设置 `PYV8C_ENABLE_NATIVE_CONSTANTS=1` 启用。
- `_pyv8` 源码修改尚未在当前工作区重新编译验证。

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
- Native smoke with `PYV8C_ENABLE_NATIVE_CONSTANTS=1`: `1 passed`; Event/Node constants resolve to their Chromium values.

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
