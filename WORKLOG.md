# 工作记录

本日志以 Git 可查的里程碑和实际验证为依据。当前设计以 [HANDOFF](HANDOFF.md) 和 [docs](docs/README.md) 为准。
历史测试数字与性能结论仅属于当时的构建；不沿用为当前版本的验证结果。

## 2026-09-09：工作区 trace 与文档整理

### 状态区分

- pyv8c HEAD 仍为 `a760520`；相邻 _pyv8 HEAD 为 `d5054ea`。
- 当前工作区包含此前完成的 trace 接入、原生观察器及测试改动，不能用 HEAD 名称单独复现。
- 本轮任务只重新整理文档，不修改业务实现，不生成提交。
- 文档已迁移到 `docs/`；保留迁移状态，不恢复旧 `doc/` 和已移除的历史 benchmark CSV。

### 当前 trace 契约

此前工作区改造把观察能力改为原生 RAII 被动观察：Python Context 统一派发，支持 Page/Context 运行时开关。
删除 JS 输出协议与类上的观察方法绑定；不通过 Reflect.set 或再次读取属性获取观察结果。
观察器返回值忽略，自身错误隔离，复杂 JS 值不自动展开。
这取代旧的“全对象 handler 拦截后重新执行”实现，不等价于跟踪全部 JS 属性访问。

### 文档重写

- README 收敛为定位、最小运行示例、能力边界及导航。
- HANDOFF 改为当前工作区快照、关键不变量、交付风险和后续方向。
- 新增 docs 索引、使用指南、架构地图和开发验证指南；重写已有 IDL、baseline、性能、窗口、资源、trace 专题。
- 删除“Window 同源模型未实现”“请求取消/代际检查未实现”“仍使用 ClassConfig 字典装配”等过时结论。
- 不再将旧 CSV 缺失情况下的百分比提升作为当前性能事实；保留 Git 历史作为追溯入口。
- 明确 Chromium 源码目标版本、ZIP 快照、默认 User-Agent 与原生 V8 构建是不同信息来源。

### 本轮执行的验证

环境：Windows x64、CPython 3.12.9；PYTHONPATH 指向相邻 _pyv8 和当前 pyv8c。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest -q --tb=short` | 353 passed |
| `python -m ruff check pyv8c/foundation` | 通过 |
| `python -m mypy --config-file pyproject.toml` | 9 个源文件检查通过 |
| 文档本地链接检查 | 13 份文档、108 个链接全部有效 |
| Python 示例 | 10 个离线示例运行通过；1 个交互示例仅语法检查 |
| PowerShell / JSON 示例 | 10 个 PowerShell 片段语法通过；1 个 JSON 片段解析通过 |

baseline 实读为 schema 5、compact、1,254 个接口；SHA-256：
`346993265b910c73ba9541c58e22969d2a280913235e9008366b2f4d18aa00f9`。
该数字描述元数据规模，不描述已实现功能数量。
本轮不重新构建原生扩展、不拉取 Chromium、不重生成 baseline、不重新采样性能。

## 已提交的历史里程碑

以下摘要对应 pyv8c 仓库提交；不会将未完成目标推定为已实现。
详细实施记录、当时的文档和测试可通过 `git show <commit>` 查阅。

| 日期 | 提交 | 变更主线 |
| --- | --- | --- |
| 2026-09-08 | `a760520` | 不可变接口结构按 Universe 编译缓存，realm 依赖部分单独绑定 |
| 2026-09-08 | `95cd9b7` | Chromium-like DOM binding 行为完善 |
| 2026-09-07 | `d703d2c` | 清理死代码、重复实现并重新划分模块职责 |
| 2026-09-07 | `1b2e273` | Window 安全模型及直接 Web IDL binding |
| 2026-09-06 | `0fa4878` | 资源拦截与持久磁盘缓存 |
| 2026-09-04 | `2e2e791`、`5c268c4` | DevTools/事件循环竞态、realm 关闭与事务清理 |
| 2026-09-04 | `f2f65ab`、`cedb505` | realm 事件循环、capability 协议、资源作用域、快照与事务 |
| 2026-09-03 | `c81e28f`、`5929d4d`、`cac0aa4` | Universe 协作者拆分、registry 与 PageServices 边界、领域包重组 |
| 2026-09-02 | `0d6da7d`、`2d14cf5`、`e317523` | 环境 realm 边界、DevTools 和首脚本暂停调度 |
| 2026-09-01 | `ca22aeb`、`e0189b1`、`6fdd114` | 环境配置、早期 trace 配置、document.cookie |
| 2026-09-01 | `fc571af`、`be1884a` | DOM 集合和 document.all 兼容行为 |
| 2026-08-31 | `1980864`、`e602b1f`、`4f9e1dd`、`843aec6` | 资源所有权、lazy 开关、Page 控制器及按需物化 |

## 历史数据说明

后续新增条目应包含日期、两个仓库/产物标识、改动范围、验证命令与结果、仍未验证的部分。
未提交改动使用“工作区”标记，不编造提交号；原生测试与 pyv8c 测试分开记录。
