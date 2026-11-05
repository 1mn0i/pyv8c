# 显式微任务队列

`MicrotaskQueue` 用于让宿主明确表达 Agent 的微任务归属。同一 Isolate 内的多个 Context 可以共享队列，也可以使用互相隔离的队列。创建 Context 时省略 `microtask_queue`，仍使用既有的 V8 自动队列模式。

```python
import pyv8

isolate = pyv8.Isolate()
queue = pyv8.MicrotaskQueue(isolate)
left = isolate.context(object(), microtask_queue=queue)
right = isolate.context(object(), microtask_queue=queue)
try:
    left.exec_js('globalThis.done = false; Promise.resolve().then(() => done = true)')
    assert left.exec_js('done') is False
    queue.checkpoint(timeout=1.0)
    assert left.exec_js('done') is True
finally:
    left.close()
    right.close()
    queue.close()
    isolate.close()
```

## 线程与所有权

- 队列固定属于创建它的 Isolate 和 Python 线程；Context 构造、脚本执行、显式关闭和检查点必须在该线程执行。
- 队列强引用 Isolate，并持有计数的 V8 根。关联 Context 强引用队列。队列仍有关联 Context 时，`close()` 抛出 `RuntimeError`。
- Context 关闭先解除 V8 队列关联，再释放 Python 引用。共享队列中的其他 Context 不受影响。
- 重复关闭队列无副作用；关闭后不能执行检查点，也不能创建新的关联 Context。
- 跨 Isolate 使用队列抛出 `ValueError`。自动模式的线程约束保持原行为，不能据此认为它已经具备新的执行组所有权模型。

## 检查点

`checkpoint(timeout=0)` 排空队列，包括检查点过程中追加的微任务。V8 负责抑制同一队列检查点重入。`timeout` 必须是有限非负秒数；零表示不额外设置时间限制。正数复用已有 watchdog，超时抛出 `JavaScriptTerminated`。终止影响整个 Isolate 活跃调用栈。

显式模式下，普通属性访问和脚本执行不会自动排空队列。宿主必须在正确的脚本、回调及任务清理边界调用检查点；这个对象本身不实现 HTML 事件循环，也不选择任务、不派发拒绝通知、不推断文档代次。

队列超时不能中断纯 Python 阻塞回调。宿主仍需将阻塞 I/O 移出 owner。一次终止不能被当成对已经发生的副作用进行回滚。

## 构建

当 V8 使用 cppgc 管理微任务队列时，绑定使用跨线程持久根持有队列，不用 `delete` 释放队列对象。非 cppgc ABI 使用 V8 对应的 `unique_ptr`。两种 ABI 由现有构建探测结果选择，不根据 Python 版本猜测。

探测不到 V8 构建元数据时，必须根据构建方提供的证据设置 `PYV8_CPPGC_MICROTASK_QUEUE=1` 或 `0`；设置与可识别的构建元数据冲突会报错。错误的配置可能造成原生 ABI 不匹配。

异常转换复用 Context 边界；队列类型注册通过 Isolate 模块接口接入。源码布局检查没有增加越层依赖豁免。

## 切换队列

`context.set_microtask_queue(queue)` 仅用于宿主在安全边界重新指定显式队列。新旧队列必须属于同一 Isolate 和当前 owner 线程，Context 必须仍有效。任何 Context 已进入、微任务检查点正在执行或存在微任务作用域时都拒绝切换；不能从 JS 调用的 Python 回调内切换。不能传入 `None` 恢复自动模式。

切换不搬迁旧队列已排入的微任务，也不负责文档导航。pyv8c 仅在首次宿主 `set_content` 保留 Context、同时改变有效 origin 时使用；真实导航创建新 Context。

## 操作预算与控制

`context.begin_operation(timeout)` 在 owner 线程建立 watchdog 票据，返回操作身份；有限非负秒数中，零表示无限预算。`context.end_operation(ticket)` 释放该票据。宿主必须用 `finally` 配对释放。

```python
ticket = context.begin_operation(1.0)
try:
    result = context.exec_js('1 + 1')
finally:
    context.end_operation(ticket)
```

另一线程可以调用 `context.terminate_operation(ticket)` 请求终止指定的活动操作；它不在调用线程进入 V8。过期、已释放或关闭 Context 的票据返回 False，不影响下一项操作。尚未进入 V8 时收到的终止请求绑定该票据，释放票据也会清除该请求。Context 在活跃栈中延后关闭时，操作票据保持有效直到实际释放；旧 `set_deadline` 的预算仍在逻辑关闭时撤销。

终止针对整个 Isolate 的活跃栈，不能隔离栈中的某个 Context，也不能回滚副作用。Inspector 暂停冻结已经存在的执行预算；宿主自身的等待截止时间继续计时。暂停期间建立的新预算独立计时。`_suspend_deadlines` 和 `_resume_deadlines` 是 Inspector 的内部协作入口，不是页面 API。

## 拒绝通知的宿主记账

`Context._promise_rejection_state(promise, action)` 是浏览器运行时的内部接口，供 V8 拒绝 hook 和通知任务使用。它只操作原生私有元数据，不执行 JavaScript、不运行属性 getter，也不建立额外的执行或微任务边界。

| action | 结果 |
| --- | --- |
| 0 | 获取该 Context 内唯一、稳定的 Python 整数身份，避免 V8 identity hash 碰撞 |
| 1 | 标记拒绝已通知；仅接受 rejected Promise |
| 2 | 取出并撤销已通知状态，返回 `(bool, 原始 reason)`；未标记时返回 `(False, None)` |

只有在通知事件及其监听器清理完成后仍未安装 handler，宿主才执行 action 1。是否已安装 handler、何时排队及 DOM 事件派发仍由浏览器运行时协调，这个接口不实现事件循环。

元数据附着于 Promise，包含整数身份和布尔标记，没有从全局登记表到 Promise 的强引用。私有 key 使用计数的 V8HandleSlot，随原生 Context 的资源释放；元数据不出现在 Reflect.ownKeys 中。action 2 从 Promise.Result 读取原始 reason，不枚举或复制用户对象。

跨 Context、跨 Isolate、非 Promise、无效 action、关闭或错误 owner 线程的访问会报错。Isolate 正在终止时返回 None，表示本次记账被中断；宿主必须停止该次记账，不能把它当作正常身份。不要在拒绝 hook 内用辅助 JavaScript 模拟该接口：重入执行会增加终止与检查点方面的风险。

## 忙碌期间的 Python 回收

Python 的循环 GC 和引用计数析构可能发生在任意线程，包括 Inspector 传输线程。Context、JSObject（含函数和 Promise）、JSValue、Script 和 PromiseResolver 的清理使用 `IsolateCleanupScope`：可以立即进入所属 Isolate 时正常释放；已有活跃调用占用 Isolate 时，在持有 GIL 的状态下登记待清理对象并增加强引用，然后立即返回。

外层 `IsolateLockScope` 退出前，在仍持有 Isolate 和 GIL 时排空该登记表，调用原清理入口并归还强引用。重复登记同一对象不会重复持有；正在析构的对象若被登记，不能提前释放其 Python 存储。Context 本身的嵌套关闭继续沿用已有的延后关闭机制。

这解决了“调试传输线程在对象分配时触发 GC → 回收等待 Isolate → 暂停中的 owner 等待传输线程提交 resume”的等待环。它不允许其他线程进入页面执行，也不释放暂停栈持有的 V8 锁。显式 `Context.close()` 仍按原同步契约进入 Isolate；显式队列的 owner 线程限制保持不变。原生回调若永久阻塞，最终回收也会延后，不能宣称强制结束任意 Python 调用。
