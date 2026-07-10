# pyv8 C++ 优化 TODO 报告

最后更新：2026-07-10

这份报告用于跟踪 pyv8 C++ 层的正确性、性能和可维护性优化事项。

状态标记规则：

- `[ ]` 表示未完成
- `[√]` 表示已完成
- `[x]` 表示无法完成

后续完成某项后，直接把该项开头的 `- [ ]` 改成 `- [√]`；如果确认无法完成，则改成 `- [x]`。

## 项目背景

pyv8 主要用于 JS 逆向时补环境，为一比一模拟 Chromium 内置原型对象提供底层 API 支持。因此 C++ 层优化应优先关注：

- Python / V8 双运行时生命周期稳定性
- Chromium-like 原型、构造器、拦截器行为拟真度
- Python 异常与 JavaScript 异常的可靠互转
- 大量模拟浏览器对象时的模板、hook、interceptor 扩展能力

## P0 - 正确性与内存安全

- [√] 修复 `src/convert.cpp:31` 的 bool 转换引用问题。
  `py_from_js()` 返回 `Py_True` / `Py_False` 时没有 `Py_INCREF`，但 Python C API 返回值通常应是 new reference。

- [√] 修复 `src/convert.cpp:196` 的 Python bytes 到 V8 ArrayBuffer 转换。
  当前分支误写成了第二个 `PyUnicode_Check`，应检查 bytes-like 对象；同时 `memcpy` 方向也反了。

- [√] 修复 `src/convert.cpp:58` 的数组自引用转换。
  执行 `PyList_SetItem(list, i, list)` 前应先 `Py_INCREF(list)`，因为 `PyList_SetItem` 会偷引用。

- [√] 修复 `src/convert.cpp:374` 的 `pyexception_to_jsexception()` 引用问题。
  当前存在 `exc_value_str` 重复 DECREF、`exc_cls_name` 未释放、`exc_type` DECREF 后继续塞进 V8 External 的风险。

- [√] 移除 `src/context.cpp:174` 对 borrowed reference 的错误 DECREF。
  `PyTuple_GetItem()` 返回 borrowed reference，不能对 `key` 做 DECREF。

- [√] 修复 `src/context.cpp:488` 的临时对象泄漏。
  `py_from_js()` 返回 new reference，传给 `context_js_obj()` 后应释放。

- [√] 修复 `src/context.cpp:461` 的 `context_set_cached_jsobject()` 潜在泄漏。
  `js_object_weak_new()` 创建的新引用放入 WeakKeyDictionary 后，本地引用应 DECREF。

- [√] 修复 `src/pyfunction.cpp:74` 的 `__v8_constructor_behavior__` 判断。
  不能直接比较 `PyObject_GetAttrString(...) == Py_False`，应保存返回值、判断 truth value，并正确 DECREF。

## P1 - 异常安全

- [ ] 替换散落的 `ToLocalChecked()`、`FromJust()`、`ToChecked()`。
  典型位置：`src/jsobject.cpp:136`、`src/convert.cpp:88`、`src/pyclass.cpp:478`。这些在 V8 返回 empty `MaybeLocal` / `Maybe` 时可能导致 fatal crash。

- [ ] 增加 `TimeoutGuard` RAII 工具。
  用于封装 `context_setup_timeout()` / `context_cleanup_timeout()`，保证早退路径也能清理 timeout。

- [ ] 将 timeout 状态改成每次执行独立持有。
  当前 `src/context.cpp:301` 的 `s_timer_id`、`breaker_id`、`s_timeout` 是全局状态，嵌套执行或多 Context 时可能互相影响。

- [ ] 审计所有 V8 `TryCatch` 活跃时调用 Python 的路径。
  确保 Python 异常只转换一次，不留下 Python 异常和 V8 异常同时存在的混乱状态。

## P1 - Runtime 与生命周期管理

- [ ] 引入 `RuntimeState` 对象。
  统一管理 platform、isolate、allocator、interned strings、template cache 等状态，替代散落的全局变量。

- [ ] 保存并释放 V8 ArrayBuffer allocator。
  `src/pyv8.cpp:91` 里创建了 `ArrayBuffer::Allocator::NewDefaultAllocator()`，但当前没有所有权管理和释放路径。

- [ ] 定义显式 V8 shutdown 流程。
  需要覆盖 Persistent strings、templates、platform、isolate、allocator 等资源。

- [ ] 替换 `src/pyfunction.cpp:55` 的永久模板保活策略。
  当前通过额外 `Py_INCREF(self)` 让函数模板常驻，建议改成可控 cache，并在 runtime/context 销毁时统一清理。

- [ ] 重构 `src/pyclass.cpp:64` 的 `cls_template_dict`。
  当前它是全局缓存，但 `py_class_new(cls, py_ctx)` 会依赖 context 配置，例如 hook 行为。缓存 key 至少应包含 context 配置，或迁移到 context/runtime state。

- [ ] 审计 V8 internal field 中保存的 Python 对象所有权。
  例如 `src/pyclass.cpp:479` 对 `py_object` 做了 `Py_INCREF`，需要确认 JS 对象或 Context 释放时有对应释放。

## P1 - pyclass 与 hook 可维护性

- [ ] 将 `py_class_new()` 中的 class metadata 解析抽成 `ClassConfig`。
  `src/pyclass.cpp:130` 附近大量重复探测 `__v8_*` key，建议一次解析，后续安装模板时直接使用配置。

- [ ] 将 `src/pyclasshandlers.cpp` 中的大型宏改成小函数。
  当前宏过多，refcount、异常传播、return 路径都比较难审计。

- [ ] 模板安装时预解析 getter、setter、query、deleter、enumerator 等 Python callable。
  热路径中应避免反复 `PyObject_HasAttrString` 和 `PyObject_CallMethod`。

- [ ] 给 handler 目标对象增加 internal field 校验。
  `get_self()` 等函数默认对象布局正确，补环境实验时容易因为异常对象导致硬崩。

- [ ] 将 cross-origin handler 拆成独立模块。
  减少普通 named/indexed property handler 文件复杂度。

## P2 - 性能优化

- [ ] 缓存常用 V8 字符串为 Persistent / interned strings。
  例如 `"constructor"`、`"prototype"`、`"message"`、`"length"`、`"name"`、常见 `__v8_*` key。

- [ ] 为 Python/JS 递归转换增加 conversion context 和 visited map。
  当前只处理部分直接自引用，复杂循环引用仍可能无限递归。

- [ ] 优化 `src/jsobject.cpp:121` 的 `js_object_getattro()`。
  避免每次 JS 属性访问都依赖 Python 异常作为普通分支。

- [ ] 避免循环里重复调用 `PySequence_Length()`。
  例如 `src/pyclasshandlers.cpp` 的 enumerator 宏中存在重复长度查询。

- [ ] 小参数数组优先使用栈或 small-vector 存储，减少堆分配。
  典型位置：`src/pyv8.cpp:183`、`src/jsfunction.cpp:73`、`src/v8tools.cpp:21`。

- [ ] 建立 JS 逆向常用热路径 benchmark。
  建议覆盖 property getter、named interceptor、indexed interceptor、function call、constructor call、object wrapping、Python-to-JS conversion。

## P2 - API 与测试补强

- [√] 增加 bytes / ArrayBuffer 转换测试。

- [ ] 增加 Python bool 转换引用稳定性测试。

- [ ] 增加循环 list / dict 转换测试。

- [ ] 增加 Python 异常到 JS 异常的转换测试。

- [ ] 增加重复创建和销毁 Context 的压力测试。

- [ ] 增加大量生成原型类的压力测试。

- [ ] 增加 timeout 嵌套执行和早退异常路径测试。

## 建议执行顺序

- [ ] 第一批：修复 P0 refcount 与转换 bug。

- [ ] 第二批：加入 `PyObject*` RAII helper 和 `TimeoutGuard`。

- [ ] 第三批：建立 V8 `Maybe` / `MaybeLocal` 安全封装层。

- [ ] 第四批：整理 context/runtime state 和 allocator 所有权。

- [ ] 第五批：重构 class metadata 解析与 handler callback。

- [ ] 第六批：基于 benchmark 做热路径性能优化。

## 备注

- 在 P0 内存和引用问题修复前，不建议先做大规模行为重构。
- 新增 helper 时注意保持 Python limited API / ABI3 兼容。
- 这个项目连接 Python 和 V8 两套运行时，建议每次变更都小步提交，并配套最小测试。
