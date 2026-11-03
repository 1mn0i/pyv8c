# 架构设计

[文档索引](README.md)

## 模块分层

| 目录 | 职责 |
| --- | --- |
| `pyv8c/foundation/` | 配置、执行器、任务源、时钟、渲染机会、错误与基础值规则 |
| `pyv8c/network/` | 传输注册、网络后端、Cookie、请求响应数据和跳转规则 |
| `pyv8c/engine/` | IDL 解码、绑定编译和跟踪 |
| `pyv8c/features/` | Feature 通用运行机制、内置声明和 Web API 核心组装 |
| `pyv8c/platform/` | Browser、Profile、Page、Realm、导航、宿主服务和生命周期组合 |
| `pyv8c/platform/resources/` | 请求、缓存、资源所有权、文档资源与 iframe 加载 |
| `pyv8c/web/` | DOM、BOM、Fetch、Worker 等 Web API 实现 |
| `pyv8c/idl/` | 接口声明入口及随包 IDL 基线 |
| `pyv8c/tools/` 与 `tools/` | 离线提取、采集、检查及命令入口 |
| 相邻 `_pyv8` 仓库 | Isolate、Context、绑定模板、句柄、微任务、watchdog 和 Inspector |

基础层不依赖 Page；Web API 通过服务接口消费宿主能力，不直接依赖平台控制器。原生层提供执行和绑定原语，不实现页面资源或 DOM 业务。分层约束由架构测试与策略检查维护。

## Web API 归属

DOM 目录保存节点树与 DOM 接口。文本编码、权限策略与 AI 接口位于 `pyv8c/web/` 的相应模块；媒体能力位于 `web/media/`，Canvas 绘图接口和 WebGL 位于 `web/graphics/`；设备运动与方向事件位于 `pyv8c/web/bom/motion.py`。这些模块由 Feature 注册表组合，不因页面调用就归入 DOM。

| 目录或模块 | 主要职责 |
| --- | --- |
| `web/dom/` | DOM 节点、文档、事件和布局 |
| `web/html/` | HTML 标签、标签注册和 HTML 解析 |
| `web/css/` | CSSOM、样式表、选择器和层叠计算 |
| `web/svg/`、`web/mathml/` | SVG 和 MathML 元素 |
| `web/graphics/` | Canvas 绘图接口、图像数据和 WebGL |
| `web/media/` | 媒体格式判断和 MediaSource |
| `web/workers/worker.py` | Worker、SharedWorker 和 Worker 全局作用域接口 |
| `web/workers/runtime.py` | Worker 执行、消息传递和生命周期 |
| `web/bom/`、`web/fetch/`、`web/fileapi/` | 浏览器对象、网络接口与文件接口 |

这些模块路径是内部实现路径；Web API 的安装仍由 Feature 注册和 IDL 绑定共同决定。

## HTML 与 DOM 元素

通用 `Element` 位于 `pyv8c/web/dom/element.py`。HTML 标签接口、标签注册表和 HTML 文档及片段解析器位于 `pyv8c/web/html/`；文档创建元素和 HTML 解析共用注册表。`DOMParser` API 留在 DOM 层，根据文档类型调用对应解析器。SVG 和 MathML 元素分别位于 `pyv8c/web/svg/` 和 `pyv8c/web/mathml/`，继承通用 `Element`。HTML Canvas 标签位于 HTML 包，绘图上下文、ImageData、ImageBitmap 和 OffscreenCanvas 位于 graphics 包。

## CSS 与布局

CSS 的属性表、声明解析、值计算、选择器、层叠和 CSSOM 位于 `pyv8c/web/css/`。DOM 元素持有样式声明，文档管理样式表版本；布局模块读取计算样式并维护几何快照。`getComputedStyle()` 通过调用方提供的布局快照获取需要使用值的属性，CSS 子包本身不导入布局模块。节点、样式表和视口变化仍通过原有版本机制使快照失效。

## 对象与状态归属

Browser 持有配置、FeatureRuntime、绑定设施和宿主回调线程池。Profile 始终拥有 CookieJar，并持有网络后端、Blob URL 注册、存储与资源缓存配置。不同 Profile 的可变会话状态应隔离，显式指定相同磁盘缓存目录时仍可能共享持久内容。

Page 组合导航、生命周期、脚本、文档和资源组件，通过 PageServices 向 Web API 提供窄能力。PageRealm 管理 V8Context、绑定会话和拒绝跟踪。Realm 的可引用寿命与文档的活动状态不是同一概念，导航不能靠清空共享 Agent 微任务来撤销旧文档。

独立页面拥有独立 WindowExecutionGroup。执行组拥有 owner 线程、Isolate、多个 Agent 和页面树帧时钟。iframe 与父页共享执行组，使用独立 Realm；Agent 按有效 origin 身份分配，opaque origin 使用身份而不是字符串 `null` 归并。

Worker 拥有独立线程、Isolate 和事件循环。跨执行组或 Worker 交付普通数据及 structured clone 结果，不共享活 JS 句柄。同步跨 iframe 调用在所属执行组的同一线程切换 Context。

V8 前台任务由执行组或 Worker 的 owner 线程自动执行；V8 后台线程只修改原生通知状态，由进程共享的等待线程唤醒执行器。任务结束后检查该 Isolate 的存活 Agent 微任务队列。执行器通过内部接口 `Context._run_foreground_task()` 运行任务，页面代码无需调用。`run_until_idle()` 会等待已就绪的前台任务，但不等待尚未投递完成任务的后台编译或未来网络 I/O，需等待具体结果时使用 `wait_for()`。

`compileStreaming` 和 `instantiateStreaming` 检查 Response 的状态、MIME 和已消费状态，并将数据交给 V8 的 streaming 编译器。用户构造的默认 ReadableStream 按读取到达的顺序逐块交付；Realm 导航或关闭时中止其未完成的原生编译会话。网络 Fetch 目前仍先缓冲响应体；ReadableStream 的 BYOB 和完整背压语义也尚未实现。

在本机 Chromium 154.0.8030.0 的 20 次离线小模块探针中，同步代码和微任务均先执行，定时器均先于 WASM 完成；pyv8c 同条件探针中这两种异步回调的先后次序有变化。定时器与后台编译完成没有固定相对次序，这个样本不能证明顺序错误，也不能替代更多时序和负载验证。

## 接口装配

接口形状由 IDL 文档描述，环境档案中的 `bindings` 决定当前环境允许暴露的子集。Python 实现提供数据模型与业务行为，绑定编译器组合元数据和回调生成原生 IdlSpec，Realm 按装配计划安装接口。

`pyv8c/features/runtime.py` 提供不依赖 Web 的通用机制，`pyv8c/features/catalog.py` 只声明内置 Feature，`pyv8c/features/core.py` 组装核心 Web API；FeatureSpec 描述接口组、依赖及实现模块；FeatureCatalog 建立静态索引，FeatureRuntime 在 Browser 范围管理依赖、模块加载和接口缓存。实现模块显式导出 `FEATURE`，可选能力按需加载。不能通过扫描模块全部变量隐式注册接口。

接口表面、迭代语义和宿主数据源是独立职责。未实现接口可按配置暴露为占位成员；缺失 pair/set 迭代源必须作为实现缺口记录，不能假装成空集合。具体格式见 [IDL 格式](idl-format.md)。

## 全局属性安装顺序

Window 的 `Object.getOwnPropertyNames(window)` 顺序由 Blink 的安装阶段决定，不是按名字排序。`pyv8c/data/window-order.json` 记录一个 Chromium 版本的顺序：legacy factories、模板阶段的接口构造器（降序）、context 阶段的接口构造器，以及 Window 成员组。成员组按安装先后为 `early`（普通 attributes）、`operations`、`late_attributes`、`late_operations`、`late_template`、`tail`，条件构造器则分为 `postinstall` 与 `postinstall_late`。该文件由 `pyv8c/tools/extract_window_order.py` 从与 `idl.json` 同一版本的 IDL 源生成，版本不一致时按 `baseline_mismatch` 报告或失败。

`RealmPlan` 把顺序分成 `preinstall`、`postinstall`、`postinstall_late`、`legacy_factories` 和 `member_groups`。`preinstall` 在 `pyv8.V8Context` 创建前写入 global 模板，因此接口构造器排在 Window 成员之前；`IdlSpec` 的 `late_attributes` 由 `context.install_late()` 在宿主 `chrome` 之后安装，`late_methods` 与 `late_template_attributes` 由 `install_late_methods()` 在条件构造器之后安装，`tail_methods` 最后安装。顺序数据缺失时回退到按名字排序。V8 内置全局段由相邻 `_pyv8` 使用的 V8 版本决定，不在该数据范围内。

## 资源组件

Page 直接组合 `ResourceFetchController`、`DocumentResourceController` 和 `FrameResourceController`，分别承担请求管线、文档资源协调和 iframe 文档生命周期。

Worker 使用同一资源目录中的 ResourceRequestSession，冻结资源配置与有效 origin，共用来源、安全和跳转规则；传输线程只交付普通数据，不调用父 Page。入口源码通过 WorkerSource 携带最终响应 URL，供 Worker location 与相对导入共同使用。

请求状态由 Fetcher 的请求对象保存；ResourceOwnership 统一登记活动请求并接入现有 ResourceScope。传输完成与文档资源完成分别处理：iframe 的网络返回不意味着子文档 load 已完成。

文档资源模块由 discovery、tracker、completion 和协调器分工。发现阶段处理节点快照，tracker 管理 token、代次、条目和索引，completion 处理固定资源种类的完成规则。其他生命周期代码通过公开查询和完成入口协作，不读取内部记录表。

## 调度与关闭

普通任务和宿主命令进入类型化任务源，控制入口只处理关闭、终止和 Inspector 唤醒。每次选择一个可运行任务；微任务在合法脚本/回调清理边界检查，任务结束提供兜底检查点。细节见[任务调度](task-scheduling.md)。

异步资源回到 owner 后执行响应处理，交付前验证取消状态及所有者身份。导航采用异步推进，宿主同步 API 等待 Future；owner 不嵌套泵事件循环等待自身结果。

关闭先解除 Inspector 等待或暂停，再由 owner 释放所属资源。子 Page 关闭不销毁仍有成员的执行组，最后成员退出后再关闭微任务队列、Isolate 和执行器。活动原生操作按身份票据终止，过期票据不得影响下一项操作。

JS 终止必须穿透监听器、脚本、资源交付与任务包装，在持票据边界分类。正常关闭的后台取消不报告宿主故障，同步宿主操作和真实执行失败仍保留异常。卸载和清理失败不因关闭状态而被吞掉。

## 当前边界

框架没有完整 Chromium 渲染和多进程体系。虚拟渲染机会、文本测量及部分几何计算不能替代合成器；origin 与 Agent 模型不覆盖完整 COOP/COEP、Origin-Agent-Cluster、OOPIF 和 `document.domain`。任何能力声明都应由对应测试及本地对照支持。

Window 顺序数据与当前 IDL/环境档案版本不一致时，由 `baseline_mismatch` 报告或直接失败，不会静默混用不同版本的安装顺序。
