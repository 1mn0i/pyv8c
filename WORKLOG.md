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
- 默认 baseline 移入 `pyv8c/baselines/interface-baseline.json` 并通过 package-data 发布。
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
