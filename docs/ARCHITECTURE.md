# 架构与运行时不变量

[返回文档索引](README.md)。本页说明代码如何装配，不把模块名称当作完整浏览器能力承诺。

## 分层

依赖从装配层流向基础层，不允许基础层反向调用平台实现：

| 层 | 职责 | 主要入口 |
| --- | --- | --- |
| foundation | 环境、错误、任务、executor、资源作用域、capability 协议 | [realm.py](../pyv8c/foundation/realm.py)、[executor.py](../pyv8c/foundation/executor.py)、[tasks.py](../pyv8c/foundation/tasks.py) |
| engine | baseline、接口身份、IDL 编译、回调适配和 realm 注册 | [universe.py](../pyv8c/engine/universe.py)、[assembly.py](../pyv8c/engine/assembly.py) |
| web | BOM/DOM、fetch/network/workers 的浏览器可观察语义 | [web](../pyv8c/web/) |
| platform | Browser/Page 组合根、realm、导航、脚本、资源和 DevTools | [browser.py](../pyv8c/platform/browser.py)、[realms.py](../pyv8c/platform/realms.py) |

包根通过惰性导出加载运行时；导入 metadata/extractor 工具不应先要求可用的原生扩展。
engine 不导入 platform；web 通过 services 与平台协作。边界由 [架构测试](../tests/unit/test_architecture.py) 约束。

## 对象与缓存的所有权

| 作用域 | 对象/状态 | 约束 |
| --- | --- | --- |
| 进程 | BaselineRepository 缓存、默认已冻结 InterfaceRegistry | 只共享元数据/注册信息，不共享页面可变状态 |
| Browser | BrowserEnvironment、InterfaceUniverse、接口类/编译/签名缓存 | 多页面复用结构，不绑定到某个 Page |
| BrowserContext | CookieJar、Blob URL、网络客户端、页面和 DevTools server | Context 隔离；磁盘缓存目录由使用者额外隔离 |
| Page/realm | Document、Window、TaskQueue、PageServices、BindingSession、V8 Context | 可变状态属于本 realm，生命周期随页面关闭 |
| Worker | WorkerRuntime、executor、WorkerRealm、Isolate | 启动异步；与页面通过任务和消息交互 |

BaselineSnapshot 是 frozen record，metadata/interfaces 提供顶层只读视图；不要将它描述为所有嵌套字典均深冻结。
默认 registry 在 [bootstrap.py](../pyv8c/platform/bootstrap.py) 中构建并冻结。InterfaceUniverse 才是每个 Browser 独立持有的编译组合根。

## Binding 数据流

```text
Chromium 生成的 v8_*.cc + Web IDL + review 数据
    → compact schema 5 ZIP
    → BaselineRepository / InterfaceCatalog
手写接口 + InterfaceRegistry
    → InterfaceUniverse
    → StubFactory + BindingWrapperFactory + InterfaceCompiler
    → 不可变 IdlSpec（结构编译缓存）
    → BindingSession（绑定当前 realm、准备批次）
    → native Context(class_configs=...) / update_configs(...)
    → V8 模板与已有 Python/JS 业务回调
```

Catalog 负责继承与 exposure 索引；StubFactory 补未实现成员；WrapperFactory 缓存签名分析并适配参数；Compiler 合成原生描述。
首次结构编译发生在 Universe；只有依赖 realm 的静态回调与构造适配器需要按 realm 绑定。
Session 的配置先准备后提交，native 接受整批配置之后才发布本地 materialized 状态。
父接口通过原型链继承；不要把全部父成员复制到每个子接口。

默认创建核心接口模板并声明其余 lazy 名称；首次读取 lazy 构造器时编译/注册必要接口及祖先。
接口存在、构造器存在和实际方法已实现是三个不同概念。

## Page 执行路径

```text
调用者 → Page 公共方法 → RealmExecutor.call
    → owner 线程上的控制器
    → native Context / DOM / TaskQueue
异步 transport/Worker 完成 → 投递任务 → owner 线程处理结果
```

PageRealm 为 Page 建立 executor 与 V8 Context。iframe 是子 Page realm，不是独立浏览器进程。
WorkerRuntime 的启动任务在自己的 executor 中创建 WorkerRealm 和独立 Isolate，避免构造时同步等待造成锁互等。
V8 isolate 的线程进入由原生桥管理；Python 侧的单 owner 约束仍必须遵守。

## 六个 capability facet

PageServices 组合而不是继承一个万能 Page 接口：

- realm_services：realm 身份、Promise 与错误值、任务投递。
- task_services：定时器、微任务和异步工作计数。
- resource_services：资源、iframe 和 Worker 源码加载。
- document_services：Document/Window、解析器及元素操作。
- worker_services：Worker 配置、创建与所有权。
- context_services：Context 级 Blob URL 等服务。

协议定义见 [foundation/realm.py](../pyv8c/foundation/realm.py)，实现见 [platform/services.py](../pyv8c/platform/services.py)。
TaskQueue 只接收窄 TaskHost；不要重新让 foundation 持有 Page 来访问任意私有状态。

## 调度、导航与清理

TaskSource 区分 command、parser、posted、timer、microtask；队列和 executor 通过信号唤醒，不依赖调用者固定频率轮询。
未开始执行的超时命令可以取消；已开始的任意同步 Python 代码不能据此强制终止。
异步任务错误经队列错误出口上报；Page 将其转成窗口错误事件。

NavigationController 负责 generation、文档提交及增量解析；ScriptController 管理阻塞/defer/module 脚本与 JS helper；
DocumentLifecycle 管理 readyState、DOMContentLoaded/load；ResourceController 管理加载、资源完成与 iframe。
导航更新文档/origin 并清理旧资源、旧脚本队列，但不意味着重新创建整个 V8 Context 或完整 Chrome 导航环境。

资源先登记后启动，完成或关闭后注销；完成通知检查 generation、cancelled、closed。
ResourceScope 与 Ownership 覆盖不同清理职责，不应通过删除其中一个来“简化”关闭。
生命周期回归见 [runtime invariants](../tests/integration/test_runtime_invariants.py)、[race regressions](../tests/integration/test_race_regressions.py) 和 [lifecycle stress](../tests/integration/test_lifecycle_stress.py)。

## Trace 与业务拦截器的区别

业务 named/indexed handler 和跨源安全 handler 决定操作是否允许或已处理。
Trace 只在已存在的 binding/handler 执行边界观察数据，不改变 handler 的返回值，不发起第二次 Get/Set。
回调派发集中于 native Context；pyv8c 仅装配开关和 Python sink。详见 [TRACE](TRACE.md)。

## 修改时不能破坏的不变量

1. V8/JS 回调留在 owner 线程；后台 transport 只投递结果。
2. realm 无关编译缓存不捕获 Page；realm 相关回调不串页。
3. Session 状态只在 native 批提交成功后更新。
4. 资源登记顺序与 generation 检查保持完整；关闭可清理多个资源，不因一个失败遗漏其他项。
5. WindowProxy 身份稳定但权限动态检查；不能把创建时的同源结论永久缓存。
6. 开启 trace 不改变模板、执行次数、返回值或原异常；自定义 sink 本身的主动副作用不由框架撤销。
