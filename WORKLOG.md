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

### 验证

- 本周重构前基线：`224 passed`。
- 当前修复后：`226 passed`，包括 DevTools 暂停/恢复、executor 超时取消、事件循环、
  生命周期压力、Cookie、DOM 集合和 native smoke 测试。

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
