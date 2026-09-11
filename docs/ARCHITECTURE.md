# 架构与不变量

本文只描述当前源码已经实现的结构。行为以 `tests/` 为最终事实来源。

## 分层

```text
platform          Browser、Page、生命周期、导航、资源、Realm executor、DevTools 宿主
       ↓ capability
foundation        参数原语、错误、URL、任务、生命周期和 Realm 合同
       ↑
web               Feature 实现：DOM、BOM、Fetch、Worker、WebSocket、IndexedDB
       ↑
engine            baseline、FeatureRuntime、IDL 编译、包装器、stub 和 session
       ↓
_pyv8             V8 Context/Isolate、Python 对象桥接、Inspector、trace 原语
```

`web` 只能通过 `foundation` 合同和 `PageServices` 的窄 capability 使用宿主能力；不能导入 `platform` 控制器、保存 `Page`/`Browser` owner 或读取隐式全局页面状态。

## 页面生命周期

`Browser` 持有配置、接口 baseline、网络后端和上下文集合；`BrowserContext` 管理页面、Cookie、Blob URL 和 DevTools 入口；`Page` 组合导航、脚本、资源、任务、生命周期和服务对象。

每个 Page 有一个 `PageRealm`：

- 一个 V8 `Context`；
- 一个 Realm-local `BindingSession`；
- 一个 owner executor，所有页面脚本和普通 Inspector 操作在此执行；
- 一个 `Window` 与 `Document` 对象。

iframe 使用独立 Page/Realm，通过 `WindowProxy` 跨窗口访问。Worker 使用独立 Worker Realm 和执行器。关闭顺序是先停止新任务与网络，再在 owner 线程释放 Context/Realm，最后释放 Page/Browser 资源。

## FeatureRuntime

Feature 是 Web API 装配的唯一概念。

### 声明

`FeatureSpec` 记录：

- Feature 名称和实现模块路径；
- 提供的完整接口名集合；
- Window 槽位；
- Feature 依赖；
- 是否为 Core。

`FeatureCatalog` 启动时建立不可变的名称索引，不导入低频实现模块。

### 运行时

每个 Browser 拥有独立 `FeatureRuntime`。Python 进程可以共享 `sys.modules`，但 Feature 的 loaded 状态、接口类映射和失败状态不能跨 Browser 共享。

实现模块必须显式导出：

```python
FEATURE = FeatureImplementation(
    {"InterfaceName": InterfaceClass},
    {"windowSlot": lambda window: value},
)
```

Runtime 负责依赖先后、循环检测、接口映射完整性、模块加载失败回滚和同一 Feature 的单次加载。IndexedDB、Navigator、WebSocket、Worker 是 Optional Feature；首次访问全局构造器或 Window 槽位时才加载。

### Feature 内对象惰性创建

Feature 模块加载、Realm 接口物化和具体对象实例化是三个独立阶段。加载某个 Feature 只允许准备类和绑定；除页面启动必需的对象外，不能在父对象构造时顺带创建低频子对象。

例如创建 `Navigator` 时不能立即创建 `NetworkInformation`、`BatteryManager`、Geolocation、Plugin 集合或存储配额对象。它们应在对应属性或方法首次访问时创建，后续访问返回同一对象。其他 Feature 中昂贵、低频或可能持有资源的子对象遵循相同规则。

`Window` 槽位和 Feature 内部子对象统一使用 `LazyValueStore`：宿主 getter 只处理可用性并调用 `.value("slot")`，集中式 `_create_value()` 工厂负责构造。需要成组创建的关联值由工厂一次写入同一个 store。不要新增 `_object()` 辅助函数、逐属性 nullable 字段或 `dict.get()` 风格的第二套缓存。

惰性对象工厂不得放在 getter 中重复执行，不得用局部动态 import 代替 Feature 加载，也不得让“未访问”与“已创建但值为空”共享无法区分的缓存状态。构造失败不得留下缓存项，以便后续访问重试。测试必须同时验证首次访问前未实例化、首次访问触发创建和重复访问身份稳定。

### Realm 物化

Feature 模块加载和 Realm 物化是两件事：模块类缓存属于 Browser Runtime，`IdlSpec` 配置属于 Realm Session。Optional Feature 首次使用时，其关联接口组在当前 Realm 一次性提交，避免对象返回时缺少对应绑定。

`_pyv8` 的 lazy factory 只传递接口名称；Python 回调把名称交给 FeatureRuntime，不在原生层复制 Web API 路由。

## 绑定与 baseline

Chromium 生成的 `interface-baseline.zip` 只提供接口结构、继承、成员、模板标志和生成签名状态。IDL 编译在 `engine/assembly.py` 完成，Realm 相关 callback 由 `engine/wrappers.py` 绑定。

参数转换、缺参判断、重载、异常顺序和 Promise 拒绝由每个 Web API 实现直接负责。禁止新增签名驱动的运行时验证器或统一调用 wrapper。

未实现接口可以由 `StubFactory` 生成占位实现；stub 不应模拟真实参数语义。

## 资源与调试

资源加载由 `ResourceController` 统一处理 loader、拦截器、缓存、脚本和网络响应。Trace 是被动观察；`resource_interceptor` 是主动改变请求，两者不能混用。

DevTools 协议、传输、请求路由和 Inspector 会话核心位于相邻 `_pyv8` 的 `pyv8.devtools`；`pyv8c.platform.devtools` 只提供 Page/iframe/Worker 的宿主描述和关闭回调。

## 修改规则

- 新 Web API 先加入 Feature 声明，再实现显式 `FEATURE`，最后添加行为测试。
- Window 值通过 Feature 槽位创建，不在 Window 中导入低频实现类或维护第二套缓存。
- 跨层需求通过 capability 扩展，不增加 owner/backdoor。
- 先改测试和最小实现，再运行专项测试与完整套件。
