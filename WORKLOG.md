# pyv8c 工作日志

本文按周记录已经完成的工作、重要决策和验证结果。它不替代
[HANDOFF.md](HANDOFF.md) 中的当前状态，也不记录逐条终端操作。

## 2026-08-23 至 2026-08-29：运行时、网络与 baseline 建立

### 完成事项

- 建立 `Browser -> BrowserContext -> Page` 生命周期、V8 Context、Window、Document、
  DOM 树、HTML parser、selector、事件和基础页面生命周期。
- 接入 fetch、XHR、History、iframe、Worker、WebSocket、Blob URL 和可注入
  `resource_loader`；真实网络使用可选 cyCronet 后端。
- 建立 V8 Error/DOMException 映射，统一非法 DOM、BOM、XHR、Worker 和网络操作的异常语义。
- 从 Chromium 生成 schema 5 interface baseline，加入 Web IDL 参数校验、动态接口 stub、
  prototype/descriptor/constructor 形状和多 realm class-config 隔离。
- 引入 baseline JSON 与二进制快照缓存、lazy interface materialization，并完成启动性能
  测量工具和 `lazy_interfaces` 开关。
- 将 Page 拆为 navigation、lifecycle、scripting、resource loading 等 controller，清理
  早期冗余 facade、死参数和未使用代码。

### 结果

运行时从最小 DOM 原型逐步扩展为可执行页面脚本的基础框架，baseline 驱动的接口形状和
realm-local binding 成为后续重构的稳定基础。

## 2026-08-30 至 2026-09-05：浏览器 API 与架构收敛

### Web API 实现

- 完成 `document.all` 的 `HTMLAllCollection`：live 集合、indexed/named lookup、legacy
  caller、undetectable 行为和 Chromium 风格严格模式错误。
- 完成 Context 级 `document.cookie` 与 CookieJar：脚本读写、路径/域/Secure/HttpOnly、
  Max-Age/Expires、删除和 `logger.debug` 变更日志；测试改用本地 HTTP 服务。
- 增加不可变 `BrowserEnvironment`、viewport/screen/geolocation、Navigator plugins 和
  MIME type 集合；iframe、Worker 继承父页面环境快照。

### DevTools 与原生 Inspector

- 在 `_pyv8` 增加 Inspector 的 pause-on-next-statement、disconnect、run-if-waiting 回调
  和首脚本启动闸门；pyv8c 增加 loopback WebSocket/CDP bridge。
- 支持 Target 发现、attach/detach、close、Runtime/Debugger 路由，以及主页面、iframe、
  Worker target 的创建和销毁通知。
- 后续将 DevTools 请求统一为 `CommandEnvelope(request/session/target/Future)`，Worker
  命令通过所属 executor 投递，避免跨线程直接调用 V8。

### 架构重构

- registry 从模块级可变全局收敛为 `InterfaceRegistry` 实例；`InterfaceUniverse` 拆分为
  baseline/catalog、stub、wrapper、assembly、session 协作者组成的组合根。
- 引入 `PageRealm`/`WorkerRealm`，统一页面与 Worker 的 Context 装配和 BindingSession。
- 引入 `PageServices` 六个 capability facet，web 层不再持有 Page 或访问平台私有状态。
- 引入 `ResourceScope`、`Ownership`、`RequestHandle` 和导航 generation，统一网络、iframe、
  Worker、WebSocket 与页面关闭清理。
- `TaskSource` 和事件驱动 `RealmExecutor` 落地；页面、iframe、Worker 均由单一 owner 线程
  执行 V8，自动消费 posted task、microtask 和 timer。

### 2026-09-04 修复

- 修复 DevTools WebSocket 线程在同步 `Debugger.send()` 上阻塞的问题；暂停求值时仍可收到
  `resume`、单步和继续命令。
- WebSocket 断开时取消所有连接事务，并增加 connection generation，避免重连客户端收到
  旧响应或过期事件。
- 修复 executor 超时后排队命令仍会执行的幽灵副作用；未开始的命令现在会被取消。
- 修复零延时 `setInterval` 独占 realm 的问题：每轮最多执行一个 timer，并施加最小重复间隔。
- 修复 Page 关闭在 owner 调用失败后提前关闭 `ResourceScope`、无法重试的问题；Context 和
  Browser 关闭会继续清理其他页面并报告首个异常。
- 资源正常完成后从 `ResourceScope` 反注册，iframe 子页面关闭时同步移除父页面所有权。

### 2026-09-04 并发/生命周期竞态修复（A1-A4）

- A1 DevTools 跨线程断连：`DevToolsTarget` 抽出唯一 `_resolve_owner()`，`send()`/
  `_send_on_owner()`/`close()` 三段 owner 查找统一（此前 `close()` 只查
  `self.realm.executor`，对 Page 恒为 None，导致从调用者线程直接断连 V8 Debugger）。
  owner 不可达或已关闭时跳过 disconnect 并记入 `close_diagnostics`，由 native
  deallocator 兜底；`Debugger.disconnect` 只在 owner 线程经 `owner.call` 执行。
- A2 事件循环丢失唤醒：`TaskQueue` 增加 `_signal_seq` 投递序号门禁，
  `pump_until`/`run_until_idle` 在 `run_turn()` 后持锁比较序号，变化则跳过 `wait`
  立即再转一轮，消除「检查-等待」窗口内 `notify_all()` 蒸发；长定时器不再把导航
  等待拖到定时器到期。`PYV8C_AUTO_PUMP=0` 旧语义不受影响。
- A3 异步异常出口：`TaskQueue` 新增公开 `report_error()`；`RealmExecutor` 增加
  `_error_sink`/`set_error_sink()`，`attach_task_queue()` 默认接 `report_error`；
  `post()` 路径异常上报错误出口而非静默吞没，`SystemExit`/`KeyboardInterrupt` 重抛
  不写日志。foundation executor 与 platform devtools 的日志由 stdlib `logging`
  统一改为 `loguru`（loguru 本就是 pyproject 运行时依赖，foundation 未新增包；
  executor 降级记录用 `logger.opt(exception=exc)`）。
- A4 资源登记顺序：`platform/resource_loading.py` 先登记到 `ResourceScope` 再发起
  请求；`platform/services.py` 的 `load_resource_async` 改为本地状态机 + 补登记，
  消除「先发起、后登记」窗口下的句柄永久滞留。web 层调用点零改动。
- 全仓枚举 `load_resource_async`/`_track_resource`/`_untrack_resource` 调用点后确认
  修复无遗漏登记路径。

### 2026-09-04 工程保障与文档收敛

- 新增 `tests/conftest.py`（`browser`/`page`/`browser_factory`/`thread_leak_check`
  fixture、pyv8 缺失跳过钩子）；`pyproject.toml` 声明 `native`/`race`/`slow` markers
  并开 `--strict-markers` 与全局 timeout；仅迁移本轮受益文件到 fixture。
- 新增 `tests/integration/test_race_regressions.py`：A1-A4 四组定向竞态回归，
  标记 `race` + `native`，可用 `pytest -m race --count 20` 重复运行。
- 新增 `[tool.ruff]`/`[tool.mypy]` 分阶段配置与 `.github/workflows/ci.yml`
  （lint/unit/full 三 job；full 缓存 `_pyv8` 构建并在其上跑 race 重复）。
- 文档收敛：`PYV8C_ARCHITECTURE_REVIEW.md` 的未决项并入 HANDOFF（新增「未决项
  （按优先级）」含明确排除的 B 档与 P2 模型层），历史过程并入本日志后删除该评审
  文档。

### 验证

- 本周重构前基线：`224 passed`。
- 修复后全量：`236 passed`（原 226 + A1-A4 竞态回归 10），包括 DevTools 暂停/恢复、
  executor 超时取消、事件循环、生命周期压力、Cookie、DOM 集合和 native smoke 测试。
- `pytest -m race --count 20` 重复运行无回潮；`PYV8C_AUTO_PUMP=0` 旧语义专测通过。

## 当前未完成项

以下事项仍是后续工作，不应在交接文档中误写为已实现：

- iframe 的完整 Frame/WindowProxy/origin 安全模型。
- 自定义同步 resource loader 的强制超时与可抢占取消。
- 独立 requestAnimationFrame frame clock、完整网络策略/缓存和浏览器级 CDP Page/Network 域。
- `_pyv8` Inspector 在 Windows Debug/ASAN、多 Worker 并发和重复关闭场景下的长期压力验证。
- immutable InterfaceCatalog、显式 ClassConfig 中间表示以及统一配置/可观测性错误体系。

## 提交索引

| 日期 | 提交 | 主题 |
| --- | --- | --- |
| 2026-08-26 至 08-31 | `5b858a8` ... `1980864` | baseline、Web IDL、运行时拆分、lazy materialization、性能与资源所有权 |
| 2026-09-01 | `be1884a` ... `ca22aeb` | document.all、Cookie、环境配置与 Navigator plugins |
| 2026-09-02 | `e317523`、`2d14cf5`、`0d6da7d` | DevTools、首脚本暂停与 realm 边界 |
| 2026-09-03 | `cac0aa4` ... `c81e28f` | registry、PageServices、InterfaceUniverse 协作者拆分 |
| 2026-09-04 | `cedb505`、`f2f65ab` | DevTools 事务、ResourceScope、事件循环、能力协议与快照 |

`_pyv8` 是独立仓库；其 native 提交和构建结果只在本日志中交叉引用，不能用 pyv8c 的
commit 代替 `_pyv8` 提交。
