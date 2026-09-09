# 当前交接状态

核对日期：2026-09-09。本文描述当前工作区，而不是仅描述 Git HEAD。
使用示例见 [README](README.md)，设计细节见 [文档索引](docs/README.md)，历史见 [WORKLOG](WORKLOG.md)。

## 1. 接手时先确认

| 项目 | 本轮核对值 |
| --- | --- |
| pyv8c HEAD | `a760520`，2026-09-08，按 Universe 缓存不可变接口编译 |
| 相邻 _pyv8 HEAD | `d5054ea`；原生 trace 改造还包含工作区修改 |
| 运行环境 | Windows x64、CPython 3.12.9；通过 PYTHONPATH 使用相邻 `_pyv8/pyv8` |
| 文档目录 | `docs/`；迁移前的 `doc/` 删除状态不应恢复 |
| 源码获取默认目标 | `CHROMIUM_VERSION` 为 `154.0.8030.0`，不是已发布 ZIP 的版本证明 |
| baseline | schema 5、compact、1,254 个接口条目；不等于实现 1,254 个完整 Web API |

工作区已包含 Python 侧 trace 接入和原生被动观察实现。仅检出上述两个 HEAD 不一定重现当前行为；交付时应同时记录两个仓库的提交、diff 和扩展产物。
不要把已有未提交业务修改归入本次文档重写，也不要仅提交 pyv8c 后假定原生依赖已同步。

## 2. 已实现的架构

- `Browser → BrowserContext → Page` 是正式运行入口。Browser 持有 InterfaceUniverse；Context 持有 Cookie、Blob URL 和可选网络客户端；Page 组合导航、脚本、资源与生命周期控制器。
- `foundation / engine / web / platform` 分层已落地。engine 不导入 platform；web 通过六个 capability facet 消费服务，不把 Page 当作通用后门。
- Page/iframe 的 V8 操作经 PageRealm 的 RealmExecutor 串行执行。WorkerRuntime 有自己的 executor，WorkerRealm 创建独立 Isolate。
- 默认事件循环自动推进；TaskQueue 区分任务来源、执行微任务检查点，支持定时器与异步完成投递。它不是渲染事件循环。
- ResourceScope、Ownership、RequestHandle 和导航 generation 已用于清理与丢弃过期完成通知；不能再写成“请求取消和代际检查尚未实现”。

源码地图与修改不变量见 [ARCHITECTURE](docs/ARCHITECTURE.md)。

## 3. Binding 的现状

`InterfaceRegistry` 默认在 bootstrap 后冻结并共享；每个 Browser 拥有独立 Universe 编译缓存，BindingSession 持有 realm 配置。
手写类用 `__idl_spec__` 表示接口身份，用不可变 `__v8_idl_spec__` 表示原生安装描述。

接口首次结构编译由 InterfaceCompiler 完成，realm 无关描述复用，仅需 realm 的回调与构造适配器再次绑定。
BindingSession 先准备本批接口及祖先，再调用 native `update_configs`；成功后才更新 session 状态。
默认 eager 核心接口加 lazy 构造器声明；首次触达剩余接口时再物化。

baseline 决定可观察接口形状，Python 实现决定实际功能。StubFactory 可补出未实现成员并返回 Undefined/占位行为。
`strict_validation=True` 是完整编译检查，不是“禁用所有 stub”或“满足 Chromium 全部规范”。
详见 [IDL](docs/IDL_MIGRATION.md) 和 [baseline](docs/interface-baseline-fields.md)。

## 4. 窗口、网络与调试的边界

- 已有 Origin、WindowProxy、受限 Location、WindowProperties 命名查找、同源/跨源检查和 postMessage；不能再笼统标记为“没有 WindowProxy/origin”。
- iframe 仍以子 Page 建模。完整 session history、浏览上下文组、COOP/COEP、sandbox 等不在当前实现承诺内。
- 自定义 loader 默认支持离线运行；真实网络需 cycronet。资源拦截和磁盘缓存已存在，但不是完整浏览器网络策略。
- 磁盘缓存键不包含 Cookie、Authorization 或完整 Vary 信息，显式共用缓存目录可能跨 Context 复用内容；私有数据场景应关闭缓存或隔离目录。
- DevTools 是单客户端 WebSocket + 部分 Target + V8 Runtime/Debugger 路由，不是完整 CDP Page/Network 实现。

## 5. Trace 必须保持被动

当前 trace 在既有原生回调的 RAII 作用域退出时观察参数、返回值与异常：

- 不重复 Get/Set，不调用 Reflect.set 代写，不新增仅用于 trace 的属性拦截器。
- 不向类注入 `__v8_*_hook__`，不使用 JS 输出开关。
- `Browser(traceable=True)` 允许后续 `page.trace = True/False`；关闭时跳过事件转换。
- 回调返回值忽略，自身异常隔离；复杂 JS 对象传引用，默认日志不展开属性。
- 纯 JS expando/accessor、常量读取以及进入原生绑定前失败的操作不保证有事件。

详见 [TRACE](docs/TRACE.md)。若为“补全日志”再次引入读写转发，必须视为设计回退，而非小修补。

## 6. 本轮验证

2026-09-09，在上述本地原生扩展环境执行：

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q --tb=short` | 353 passed |
| `python -m ruff check pyv8c/foundation` | 通过 |
| `python -m mypy --config-file pyproject.toml` | 9 个源文件检查通过 |
| 文档本地链接检查 | 13 份文档、108 个链接全部有效 |
| Python 示例 | 10 个离线示例运行通过；1 个交互示例仅语法检查 |
| PowerShell / JSON 示例 | 10 个 PowerShell 片段语法通过；1 个 JSON 片段解析通过 |

本轮文档整理不重建 V8、不重新生成 baseline、不重新跑性能采样，也不将旧日志的 native 测试结果计入本轮验证。
CI 当前使用 Windows CPython 3.12 加载相邻仓库预构建扩展，并非现场完整构建 V8；见 [DEVELOPMENT](docs/DEVELOPMENT.md)。

## 7. 后续工作建议

这些是后续方向，不表示本轮承诺实现：

1. **交付一致性**：固定两个仓库和原生构建产物；更新 baseline 时补齐可追踪的 Chromium 来源证据，避免把 UA/目标版本当来源。
2. **回归与压力**：保留 owner 线程、关闭、过期任务和 trace 无副作用测试；补充 native Debug/ASAN、多 Worker 与 Inspector 长时间压力记录。
3. **网络与安全模型**：完善缓存私有数据隔离、缓存校验与网络策略；继续补齐 Frame/WindowProxy 行为，不把现有同源检查包装成安全沙箱。
4. **执行限制**：同步 Python loader 已开始阻塞后无法靠等待超时强制抢占；独立 frame clock 和完整模块/渲染模型仍缺失。
5. **维护性**：逐步减少控制器对 Page 的反引用，校准 capability 协议；保留当前单 owner 与资源登记顺序，不以“大重构”破坏已有不变量。

## 8. 提交前检查

- 新行为测试放入 unit/browser/integration 对应层；涉及 native 语义时同时在 _pyv8 验证。
- 先定向测试，再全量和分阶段静态检查；不要把 native 缺失导致的 skip 写成通过了浏览器验证。
- 核对 `docs/` 相对链接，不恢复旧目录；修改参数时同步 USAGE，修改 trace 契约时同步两仓文档。
- 更新 WORKLOG，分别写清已提交、工作区变化、实际运行的命令和未验证事项。
