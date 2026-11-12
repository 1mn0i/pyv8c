# 源码布局

**简体中文** | [English](../en/pyv8/source-layout.md)

`src/` 按所有权和依赖方向对原生绑定进行分组。在仓库根目录运行
`python pyv8/tools/check_layout.py --list` 以获取当前的文件清单；编译
源文件、头文件以及被包含的 `.def` 宏表都会被检查。文件数量
是生成的，而不是在本文档中维护的。

这里讲的是*放在哪里*。至于*为什么* —— Isolate、Realm、配对表
和句柄类型如何相互支撑，以及每一层各自的用途 —— 参见
[architecture.md](architecture.md)。

## 分组

| 分组 | 层次 | 职责 | 文件 |
| --- | --- | --- | --- |
| `src/` | 0 | 可移植性 shim 与私有模块入口 | `pyv8.h` `pyv8.cpp` `polyfill.h` `abi3.h/.cpp` `v8_compat.h` `boundary.h` `callback_boundary.h` `bridge_base.h` |
| `src/isolate/` | 1 | V8 平台、Isolate、Realm 及它们的生命周期 | `platform_state.*` `v8_string_cache.*` `isolate.*` `isolate_state.*` `isolate_scope.*` `entry.h` `execution.*` `runtime.*` `context.*` `context_names.cpp` `context_resources.cpp` `watchdog.*` `error_hooks.*` `microtask_queue.*` |
| `src/values/` | 2 | 跨界的值、错误映射、句柄注册 | `convert.h` `convert_to_python.cpp` `convert_to_javascript.cpp` `error_family.*` `serialization.h` `js_exception.*` `jsvalue.*` `jsnull.*` `jsundefined.*` `handle_resource.h` |
| `src/handles/` | 3 | 代表 JavaScript 值的 Python 类型 | `jsobject.*` `jsfunction.*` `jspromise.*` `jstypedarray.*` `script.*` `serialization.cpp` |
| `src/tools/` | 3 | 跟踪 —— 接口层将其入口点包裹其中的钩子 —— 与 devtools 桥接 | `trace.h` `debugger.*` |
| `src/interfaces/` | 4 | 从声明到模板再到回调 | `idl_spec.*` `pyclass.*` `pyclass_handlers.*` `pyclass_interceptors.cpp` `pyclass_cross_origin_handlers.*` `v8tools.*` `pyclass_callbacks.cpp` `pyfunction.*` |

检查器报告当前的数量，并校验构建的 include 根目录。

`tools/` 与 `handles/` 处于同一层，而不在 `interfaces/` 之上。跟踪
钩子*被*接口层（`pyclass_handlers.cpp`、
`v8tools.cpp`、`pyclass_callbacks.cpp`、`pyclass_interceptors.cpp`、
`pyfunction.cpp`）以及 Realm 的所有者与其名称安装器所 include，因此它是
这些文件使用的基础设施，而不是使用它们的东西。devtools
桥接只能从下面的组合点到达。

## 允许的 include 关系

文件可以 include 本层及更低层的头文件。向上
引用正是让一个层不再成其为层的原因。有少量文件的存在是为了把各层
连接起来。表格描述它们的角色；`COMPOSITION_EDGES` 枚举的是
精确允许的源/目标配对，而非豁免整个文件：

| 文件 | 可 include | 原因 |
| --- | --- | --- |
| `pyv8.h` | 精确声明的组合边 | 私有模块组合；不是更低层单元的依赖 |
| `pyv8.cpp` | 精确的模块注册组合边 | 模块入口注册每一个类型 |
| `context.cpp` | 贯穿到第 4 层的已声明组合边 | Realm 的所有者构建接口模板、创建 Realm 的句柄、转换显式检查点异常并接通 devtools 桥接 |
| `context_names.cpp` | 1-4 | 向 Realm install 名称并维护其背后的注册表，正是 `install`、`expose` 与惰性工厂协议所做的事，因此这是 Context 生命周期中了解接口层的那一部分；它还向错误转换提供已注册的构造函数，并在 Realm 进入处 install 访问失败回调 |
| `context_resources.cpp` | 1-3 | 配对表为 JavaScript 对象实体化一个 Python 句柄 |
| `convert_to_python.cpp` | 1-3 | 转换器的一个方向：它为传入的对象构建句柄，而这些句柄由 handles 层定义 |
| `convert_to_javascript.cpp` | 1-4 | 另一个方向，它还额外需要了解类与函数的机制 |
| `isolate_state.cpp` | 1-4 | 销毁是把建立在 Isolate 之上的东西拆解开的地方，包括共享的接口模板；而 inspector 属于创建它时所针对的那个 Isolate，因此它必须在 `Isolate::Dispose` 之前进行 |
| `runtime.cpp` | 1-2 | 关闭时，只要仍有序列化的 WebAssembly 模块存活，就拒绝销毁 V8 |
| `wasm_streaming.cpp` | 1-2 | 中止流式编译时，会把它的 Python 原因转换成一个 JavaScript 值或错误 |
| `error_hooks.cpp` | 1-2 | 报告 Isolate 的错误意味着把 JavaScript 值交给 Python，而这属于转换 |
| `error_family.cpp` | 2-3 | 构造一个错误会让跟踪静默，而跟踪由跟踪钩子掌管 |

其余一切，包括头文件在内，都遵循这条简单规则。检查器
会报告而非隐瞒两件事：

* `bridge_base.h` 只包含基础契约。`pyv8.h` 为模块组合
  单元所私有；更低层的文件不能 include 它。仅用于 Isolate 的宏位于
  `isolate/entry.h`，跨界宏位于 `values/crossing.h`。
* 失效 include 是没人有意写下的违规。第一轮检查发现了
  `isolate_state.cpp -> pyclass.h`、`exception.cpp -> script.h` 以及
  `context.cpp -> jsfunction.h` / `pyfunction.h`；扁平化 `tools/`
  的那一轮又发现了一个 `context.cpp -> trace.h`：这些头文件所
  声明的符号并未被 include 它们的文件使用。去掉它们属于触及那些目录的
  那次移动，而由编译器来判定。

## 新文件放在哪里

1. 它描述的是哪一层：Isolate 及其生命周期、一个值、一个句柄、
   一个接口，还是位于接口之下的跟踪与分发？
2. 哪些文件会 include 它？它所落入的层之下的任何文件都不可以。
3. 如果答案是“它必须向上触及”，它就是一个组合点：把它写进
   本文档，把它加入 `pyv8/tools/check_layout.py`，并让这份清单保持简短。

## 历史目录迁移

按阶段安排，使每一步都是一次单纯的移动加上一处构建配置改动，且
各自单独验证。

| 阶段 | 移动内容 | 同步改动 |
| --- | --- | --- |
| S1 | `isolate/` (21 files) and `tools/` (7 at the time, `faker/` included) | `setup.py` 中的两处、`faker/Faker.cpp` 的三处 `../` include、`doc/devtools.md`、MSVC 工程路径 |
| S2 | `values/` (11) and `handles/` (10) | - |
| S3 | `interfaces/` (13) | 把 `binding_callbacks.cpp` 重命名为 `pyclass_callbacks.cpp`；把 `pyclass_handlers_internal.h` 重命名为 `pyclass_handlers.h`，并把拦截器进入声明移出 `pyclass.h`；把内部字段布局从 `pyclass.h` 移到 `values/handle_resource.h`，让使用它的句柄无需向上触及即可到达它；去掉上述失效 include；让检查器强制执行 |
| S5 | nothing moves | 拆分那三个超过一千行的文件：把 `convert.cpp` 按方向各拆成一个文件，把 `pyclass_handlers.cpp` 拆成机制部分与 V8 调用的入口点，把 `context.cpp` 拆成 Realm 的生命周期与它 install 的名称。每次拆分都单独落地，采用与移动相同的验证，因为拆分只是重写目录树的结构，无意改变其行为 |

两件事让这些移动成本很低。`setup.py` 通过递归遍历 `src` 来收集
其源文件 —— 新的分组不必在那里新增 glob —— 而 `include_dirs` 逐一列出
每个分组，因此改变分组的文件保留它原本已有的裸 include。

最后一个子目录比扁平化走得更远：`tools/faker/` 被拆解，
它所实现的功能也被移除了。原生绑定不再拥有的东西，按原先
可触达的顺序，是：

* `faker` 对象本身 —— JavaScript 可见的辅助函数 `print`、`run_js`、
  `run_js_file`、`structuredClone`、`get_arg`、`set_arg`、`new_document_all`、
  `native_wrapper` 以及 `v8_version` 字符串 —— `pyclass.cpp` 过去会把它们
  install 到每个声明 `IdlSpec.global_this` 的类的全局对象上；
* 让用 JavaScript 实现的成员得以存在的分发机制：
  `CallbackType` 与 `callback_type` 字段（在 `AttributeSpec`/`MethodSpec` 上）、
  那三个 `FakerJsBindingCallback` 入口点（把回调名解析到
  `globalThis.faker`），以及惰性常量路径（`IdlSpec.
  constants_callback` 和 `ConstantCallbackSpec`），它以同样方式
  解析名称；
* `js_function_repr` 的 `faker.raw_tostring` 钩子，对象一旦消失就
  再没有什么能定义它 —— 函数的 repr 又回到它自己的转换；
* 只有那些代码路径会读取的字符串缓存 id：`Faker`、`RawToString`、`Name`、
  `Length` 和 `Callback`。

`IdlSpec.global_this` 保留：它指明哪个 Python 类支撑 JavaScript
全局对象。跟踪钩子也保留，并且不属于这一切 —— 它
只是*在* `faker` 命名空间里，所以对象消失后它便归于
`trace::`，这正是它的调用点现在的写法。留在 `tools/` 里的是
`trace.h` 与 devtools 桥接。

以下命令与计数记录的是原始重构，不代表当前
仓库布局或当前测试总数。每次移动之后：删除 `build/temp*`（MSVC 按源文件
路径命名目标文件，否则一次移动会链接到过时的目标文件），重新构建，然后运行

```
python -m pytest tests -q                          # 338 passed
python -m pytest tests/unit tests/integration -q   # 372 passed, 1 environment failure
python -m pytest tests/browser -q                  # 210 passed
python benchmarks/bench_object_pairs.py
python benchmarks/bench_strings.py
```

唯一的那处单元测试失败是 `test_idl_extractor.py` 的渲染器快照测试，
它需要一个不属于本目录树的 `_pyv8env/renderer` 检出。

这个方案里没有任何东西改变行为，因此判定标准是那些数字
不超出其自身的运行间波动：一次改变了测试结果的
移动，就是这次移动里的缺陷。


## 当前强制检查

表格中的组合清单描述的是角色，而非整个文件范围的豁免。
`COMPOSITION_EDGES` 按相对源路径与目标路径固定每一条当前允许的
依赖。新增的 include、其它目录中的同名文件，以及更低层
对模块组合头文件的 include，都无法通过强制检查。头文件
的使用方必须显式声明其运行时/跨界/进入依赖。用
`python pyv8/tools/check_layout.py --list` 获取当前的清单；上面的迁移计数
与测试结果是历史记录。


检查器把引号路径相对于发起 include 的文件和字面量 Extension 的
include 根目录做精确解析。等价的引号/列表/元组写法都被接受；
非字面量的根目录会失败并给出诊断信息。目录树中别处存在的
同名基名，并不会让一个错误的 include 目录变得有效。buffer_range.h
属于 handles，且不拥有任何 Context 或 cache；它的调用方提供受保护的进入。
