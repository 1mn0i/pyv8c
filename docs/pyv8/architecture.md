# 架构

**简体中文** | [English](../en/pyv8/architecture.md)

绑定层由哪些部分组成，以及是什么把这些部分连在一起。`source-layout.md`
说明一个文件该放在哪里；本文说明这些部分为什么是现在这个样子。

## 层次

```
Python                 pyv8/            the package: flags, tracing helpers, devtools
                          |
extension              pyv8.cpp         the module: init, shutdown, the free functions
                          |
interfaces             interfaces/      declarations -> templates -> callbacks
                          |
handles                handles/         the Python types that stand for JavaScript values
values                 values/          values and errors crossing the boundary
isolate                isolate/         the isolate, the realm, and their lifetimes
shims                  *.h at the root  ABI3 and V8 API adapters
```

一个文件可以 include 自己所在层以及更低层的头文件。例外以精确的
source/target 相对路径边的形式列在 `pyv8/tools/check_layout.py` 的
`COMPOSITION_EDGES` 中，每条都附有原因：一个聚合头文件、
模块入口点，以及五个负责把各层连接起来的文件（Realm 构建接口，
转换器触达它所生成的句柄类型）。`python pyv8/tools/check_layout.py --enforce`
用这些规则约束目录树，同时也约束两个构建文件：`setup.py` 必须把每个分组
都作为 include 根目录，因为源文件按裸名 include 相邻文件；
`pyproject.toml` 必须声明实际存在的包数据。

## 生命周期：按嵌套顺序

**Isolate** 拥有 V8 堆、驻留字符串（`V8StringCache`，
每个 `V8StringId` 一项）、配对表的所有者以及回调数据。
属于它的一切都在 `IsolateState::shutdown` 中按一个要紧的顺序拆除：
先是报告器（它们会进入 Python，不能在 Isolate 拆解过程中运行），
然后是建立在它之上的 Inspector，然后是 Python 引用，
最后是 `Isolate::Dispose`。

**Realm**（`context_c`）拥有一个 V8 Context、安装进其中的类，
以及必须保活的 Python 对象。这些引用只在 `context_gc_fields` 里列一次，
`context_traverse` 和 `context_finish_close` 都遍历它 —— 这两处过去是手写的，
只差一次编辑就会彼此不一致。

**配对表**让 `Py -> JS` 的标识成立：一个 Python 对象与在同一 Realm 中
代表它的 JavaScript 对象，以 Python 对象的地址为键。
它位于 `ContextLifetime` 中，后者的生命周期长于 Context，
因此稍后关闭的句柄读到的是一张空表，而不是已释放的内存。
它对两侧都不持有强引用 —— 表项*本身*就是 `PythonObjectOwner`，
持有对 Python 对象唯一的强引用，并在释放它的同一步中被擦除。
表既不会让任何一侧保活，
也不会让任何一侧在它不知情时被回收。

**句柄**（`js_object`、`js_function`、`jspromise`、`jstypedarray`、`js_value`）
是代表 V8 值的 Python 对象。每个都持有一个持久句柄，在 `live_v8_handles_` 中计数，
并参与 Context 的句柄注册表：Context 可以被关闭，从而让所有句柄失效；
也可以被回收，而这只在没有剩余句柄时发生。生命周期长于其 Realm 的句柄
会以错误应答 —— "no active V8 isolate" 或 "closed" ——
并且绝不读取 V8。

## 线程

一个 Isolate 可以承载多个 Realm；iframe Realm 可以共享父堆。
同一 Isolate 内的 V8 执行互斥。原生入口可以来自不同线程，
而 pyv8c 单独负责任务/DOM 的线程归属。`ExecutionScope` 是 Python 入口点
声明"我要进入这个 Realm"的方式：它获取 Isolate 锁，并建立工作所需的
句柄作用域与 Context 作用域。`isolate/entry.h` 提供仅涉及 Isolate 的作用域，
用于初始化、拆除以及已进入状态下的句柄构造。它不能替代 Context 进入，
也不能替代等待之后的校验。等待 Isolate 锁会释放 GIL，
V8 实际运行的窗口同样如此：`CallbackBoundary` 在每次 V8 -> Python 进入时
把它取回，这是配对表和所有者清单所依赖的约定
（`context_resources.cpp` 在要紧处有说明）。

## 跨界边界

`values/` 按行进方向拆分：`convert_to_javascript.cpp` 与
`convert_to_python.cpp`。两者之间有两条让边界保持
诚实的规则：

* 往返跨界的值在宿主能区分时保持标识：`ps_from_js`/`js_from_py`
  记住自己建立的配对。
* 失败必须以它本来的样子抵达。JavaScript 异常变成携带原始值的
  Python 异常；回调内部抛出的 Python 异常被重新抛回 JavaScript；
  而本身没有异常的失败会被赋予一条消息，因为 CPython 会把
  "returned NULL without setting an exception" 报成关于绑定层
  而非关于该值的 `SystemError`。

## 错误

绑定层拥有标准错误族：`pyv8.JsError` 及其旁边的六个类型，
每个都派生自它所代表的错误对应的内建类型，
因此捕获该内建类型的宿主仍然捕获它一直捕获的东西。
宿主通过继承其中之一加入错误族，而这个继承就是全部声明 ——
没有需要安装的表，不需要在 Realm 运行前声明任何东西，
也没有需要保持同步的名字。错误族不包含的类属于宿主自己的失败，
任何 Realm 都不会看到它，因为跨界被终止，
异常在发起它的边界处抛出。错误族在一处声明：
`values/error_family.cpp` 中的一张表，由模块自己发布，
因此不存在第二份可能失同步的名字。
不属于标准名字的 Realm 构造函数声明在类自身上，
就像 `DOMException` 用 `__v8_error_shape__` 说明它以
`DOMException` 呈现 —— 一组三元组（构造函数名、
构造函数参数属性、被复制的 (python, JavaScript) 字段对）。
Realm 看到的是错误本身：宿主自己的栈帧与文件路径
不会放到对象上，因此捕获它 —— 或上报它 —— 的脚本
不会得知宿主侧失败的任何信息。未捕获异常与未处理的 rejection
通过 `Context.set_error_hooks` 安装的钩子报告，希望获得页面 `error` 事件的宿主也在这里获得机会。

## 接口

`interfaces/` 把声明变成模板和回调：

```
IdlSpec (Python)   ->  py_class (one per class per context)  ->  FunctionTemplate
                   ->  CallbackData (one per member per context) -> info.Data()
```

`AttributeSpec`/`MethodSpec`/`ConstantSpec` 是宿主的声明；
`InstallAttribute` 和 `InstallOperation` 把它们变成模板；调用时
`IsolateState::callback_target` 把 `info.Data()` 还原成 spec，
`py_class_handler` 通过 `HandlerId` 找到 Python callable。
拦截器是按类的，而 window 自身的名称表是另一套分层的东西：
`pyclass_interceptors.cpp` 实现这两个部分，
`pyv8/tests/test_window_name_table.py` 中的说明解释了一个下标对它的含义。

## 检查项与所在位置

| 决策 | 由谁保证 |
|---|---|
| 层次方向、构建文件 | `pyv8/tools/check_layout.py --enforce` |
| 公开接口面：stub、导出、扩展 | `pyv8/tests/test_public_surface.py` |
| 配对表的标识与生命周期 | `pyv8/tests/test_memory_ownership.py` |
| 属性协议：钩子、错误、下标 | `pyv8/tests/test_jsobject.py` |
| window 名称表与下标 | `pyv8/tests/test_window_name_table.py` |
| 跨界、安装、字符串的成本 | `benchmarks/` |

## 结构化克隆回调的生命周期

`handles/serialization.cpp` 拥有序列化器及其临时 Python 流包装器；
`values/serialization.h` 声明转换协议。流仅在回调的创建线程上有效，
成功与失败时都会失效。序列化器的值与转移项走常规转换路径，
包括已关闭 Context 与外部 Isolate 的检查。
宿主回调可以通过 V8 的显式允许作用域重入 JavaScript。
即使 V8 报告写入或读取成功，Python 回调失败仍会被检查，
从而保留原始异常，
而不是带着已设置的异常返回一个值。


## 2026-10-14 评审后的架构契约

`bridge_base.h` 只导出 Python/V8 ABI shim 以及异常/GIL 边界；
`pyv8.h` 是私有的模块组合。较低层单元 include 它们所消费的每个契约。
`ExecutionScope` 对可能等待的 Context/句柄操作是必需的；
进入之后必须重新校验所有者与句柄状态。仅宿主的诊断读取不需要 V8 进入。
`import_handle` 与跟踪配置使用该契约。

只有 `IsolateLockScope` 修改私有的进入计数器/锁。IsolateState 拥有
Inspector waiter 注册与延迟 Context 关闭，包括引用与分配失败回滚。
ContextResources 的注册表是 Context 生命周期函数与 IsolateState 私有的；
依赖解除链接是该所有者的操作，
HandleRegistration 委托给注册函数。

Isolate 选择声明 DefaultOnly、ActiveOrDefault 或 Explicit。
它不改变公开 API 的既定策略：GC 用 default，Context 查询与回调构造函数用 active/default，
显式所有者被显式校验。pyv8c 保留调度与 Web API 所有权；
这个内核提供进入安全性。

扩展把每个 .h/.def 都声明为构建依赖。其构建清单记录
源码内容标识、ABI、编译器输入、实际编译器与二进制摘要；
构建失败不会发布新清单。源码未变时重新构建必须跳过编译。
架构反向测试必须拒绝新的组合边。


## 显式微任务队列

`MicrotaskQueue` 属于 Isolate 层：它记录创建线程并持有引用计数的 V8 根。
Context 通过 `microtask_queue=` 附着到某个队列，
多个 Context 可以共享一个队列。省略该参数的独立调用
仍使用原来的自动模式，因此不能把 Context 标识
当作微任务队列标识。在显式模式下，普通 V8 入口点
不会自动运行检查点；HTML 清理边界
必须由宿主调度层接上。

队列存储、线程检查与释放位于 `microtask_queue.*`；
类型注册通过 Isolate 接口完成。检查点的异常转换留在
`context.cpp` 的组合边界，这样 Isolate 层不会反向依赖
位于其上方的跨界级异常转换。参见
[显式微任务队列](microtask-queue.md)。
