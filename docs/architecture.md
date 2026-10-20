# 架构设计

[文档索引](README.md)

## 模块分层

| 目录 | 职责 |
| --- | --- |
| `pyv8c/foundation/` | 配置、执行器、任务源、时钟、渲染机会、错误与基础值规则 |
| `pyv8c/network/` | 传输注册、网络后端、Cookie、请求响应数据和跳转规则 |
| `pyv8c/engine/` | IDL 解码、绑定编译、Feature 装配和跟踪 |
| `pyv8c/platform/` | Browser、Profile、Page、Realm、导航、宿主服务和生命周期组合 |
| `pyv8c/platform/resources/` | 请求、缓存、资源所有权、文档资源与 iframe 加载 |
| `pyv8c/web/` | DOM、BOM、Fetch、Worker 等 Web API 实现 |
| `pyv8c/idl/` | 接口声明入口及随包 IDL 基线 |
| `pyv8c/tools/` 与 `tools/` | 离线提取、采集、检查及命令入口 |
| 相邻 `_pyv8` 仓库 | Isolate、Context、绑定模板、句柄、微任务、watchdog 和 Inspector |

基础层不依赖 Page；Web API 通过服务接口消费宿主能力，不直接依赖平台控制器。原生层提供执行和绑定原语，不实现页面资源或 DOM 业务。分层约束由架构测试与策略检查维护。

## 对象与状态归属

Browser 持有配置、FeatureRuntime、绑定设施和宿主回调线程池。Profile 始终拥有 CookieJar，并持有网络后端、Blob URL 注册、存储与资源缓存配置。不同 Profile 的可变会话状态应隔离，显式指定相同磁盘缓存目录时仍可能共享持久内容。

Page 组合导航、生命周期、脚本、文档和资源组件，通过 PageServices 向 Web API 提供窄能力。PageRealm 管理 V8Context、绑定会话和拒绝跟踪。Realm 的可引用寿命与文档的活动状态不是同一概念，导航不能靠清空共享 Agent 微任务来撤销旧文档。

独立页面拥有独立 WindowExecutionGroup。执行组拥有 owner 线程、Isolate、多个 Agent 和页面树帧时钟。iframe 与父页共享执行组，使用独立 Realm；Agent 按有效 origin 身份分配，opaque origin 使用身份而不是字符串 `null` 归并。

Worker 拥有独立线程、Isolate 和事件循环。跨执行组或 Worker 交付普通数据及 structured clone 结果，不共享活 JS 句柄。同步跨 iframe 调用在所属执行组的同一线程切换 Context。

## 接口装配

接口形状由 IDL 文档描述，环境档案中的 `bindings` 决定当前环境允许暴露的子集。Python 实现提供数据模型与业务行为，绑定编译器组合元数据和回调生成原生 IdlSpec，Realm 按装配计划安装接口。

FeatureSpec 描述接口组、依赖及实现模块；FeatureCatalog 建立静态索引，FeatureRuntime 在 Browser 范围管理依赖、模块加载和接口缓存。实现模块显式导出 `FEATURE`，可选能力按需加载。不能通过扫描模块全部变量隐式注册接口。

接口表面、迭代语义和宿主数据源是独立职责。未实现接口可按配置暴露为占位成员；缺失 pair/set 迭代源必须作为实现缺口记录，不能假装成空集合。具体格式见 [IDL 格式](idl-format.md)。

## 资源组件

Page 直接组合 `ResourceFetchController`、`DocumentResourceController` 和 `FrameResourceController`，分别承担请求管线、文档资源协调和 iframe 文档生命周期。

请求状态由 Fetcher 的请求对象保存；ResourceOwnership 统一登记活动请求并接入现有 ResourceScope。传输完成与文档资源完成分别处理：iframe 的网络返回不意味着子文档 load 已完成。

文档资源模块由 discovery、tracker、completion 和协调器分工。发现阶段处理节点快照，tracker 管理 token、代次、条目和索引，completion 处理固定资源种类的完成规则。其他生命周期代码通过公开查询和完成入口协作，不读取内部记录表。

## 调度与关闭

普通任务和宿主命令进入类型化任务源，控制入口只处理关闭、终止和 Inspector 唤醒。每次选择一个可运行任务；微任务在合法脚本/回调清理边界检查，任务结束提供兜底检查点。细节见[任务调度](task-scheduling.md)。

异步资源回到 owner 后执行响应处理，交付前验证取消状态及所有者身份。导航采用异步推进，宿主同步 API 等待 Future；owner 不嵌套泵事件循环等待自身结果。

关闭先解除 Inspector 等待或暂停，再由 owner 释放所属资源。子 Page 关闭不销毁仍有成员的执行组，最后成员退出后再关闭微任务队列、Isolate 和执行器。活动原生操作按身份票据终止，过期票据不得影响下一项操作。

JS 终止必须穿透监听器、脚本、资源交付与任务包装，在持票据边界分类。正常关闭的后台取消不报告宿主故障，同步宿主操作和真实执行失败仍保留异常。卸载和清理失败不因关闭状态而被吞掉。

## 当前边界

框架没有完整 Chromium 渲染和多进程体系。虚拟渲染机会、文本测量及部分几何计算不能替代合成器；origin 与 Agent 模型不覆盖完整 COOP/COEP、Origin-Agent-Cluster、OOPIF 和 `document.domain`。任何能力声明都应由对应测试及本地对照支持。
