# 任务调度

**简体中文** | [English](../en/pyv8c/task-scheduling.md)

[文档索引](../README.md)

## 执行所有权

独立 `new_page()` 创建 WindowExecutionGroup，独占 owner 线程和 Isolate。iframe 使用同一执行组；同有效 origin 的 Realm 共享 Agent，其他 Agent 独立排队但仍在该 owner 线程运行。opaque origin 按身份 token 区分，初始 about:blank、srcdoc 和 Blob 的来源按创建关系继承。

每个 Agent 持有任务源队列与显式 V8 微任务队列，Dedicated Worker 使用独立线程、Isolate、Agent 和事件循环。同步 iframe 访问无需跨线程等待；不相关执行组之间不允许直接传递活 JS 对象。

## 任务源与执行入口

任务记录包含来源、所属 Realm/文档、代次、顺序和取消信息。任务源包括 parser、navigation、networking、timer、posted-message、user-interaction、DOM manipulation、media-element 和 embedder。

OfflineAudioContext 的完成通知使用 media-element 来源，避免已完成的渲染被无关 DOM 任务积压。该任务仍受所属文档代次与关闭状态约束；complete 事件先于渲染 Promise 的兑现回调。

普通宿主命令进入 embedder 来源，不无限优先清空。调度公平选择可运行来源，并保持来源内要求的 FIFO；parser 阻塞只影响对应文档。关闭、终止和 Inspector 唤醒使用受限控制入口，不能借该入口执行任意页面 JS。

跨来源不存在一条应被固定的 Chrome 全序，测试约束必要因果顺序和不饥饿。当前不实现完整加载提级、后台预算或动态输入优先级。

## 微任务与拒绝通知

Promise、queueMicrotask 和 MutationObserver 使用所属 Agent 的显式微任务队列。普通属性读取、对象转换和嵌套跨 Realm 调用不应意外启动检查点；脚本与回调清理在 JS 栈条件允许时检查，任务结束兜底，并防止重入。

检查点排空至稳定，不用人为微任务配额改变顺序。无限微任务由操作预算和终止机制处理。Promise 拒绝在检查点收集候选，再通过任务通知 `unhandledrejection`、`rejectionhandled`，不在任意属性调用结束时直接派发。

导航代次失效只取消相应文档的操作，不能清空整个 Agent 队列并误伤兄弟 Realm。

## 网络、导航与等待

外部网络完成投递所属 networking 任务；返回 owner 后的响应转换、跳转和内部 continuation 可在该任务内继续。缓存、本地 URL 和 inline loader 的异步最终结果同样通过队列交付，不因当前在 owner 上就同步调用最终用户回调。

导航不在 owner 阻塞等待主文档网络。同步宿主 `goto()` 等待操作 Future，owner 继续处理可运行工作；owner 内部不能同步等待尚未完成的操作，也不能嵌套驱动事件循环完成等待。

| 等待入口 | 判断条件 |
| --- | --- |
| `goto(..., wait_until="commit")` | 当前导航提交 |
| `wait_for_load_state("domcontentloaded")` | 目标文档的 DOMContentLoaded 状态 |
| `wait_for_load_state("load")` | 加载完成流程正常结束 |
| `wait_for(expression)` | 只读 JS 条件成立 |
| `run_until_idle(timeout)` | 目标 Agent 可运行任务排空并完成检查点 |

远期 timer、在途网络、长连接和 Worker 存活不直接阻止可运行队列空闲。`timeout=0` 的空闲调用只查询当前状态；网络或应用就绪必须使用对应等待入口。

## 定时器与渲染机会

定时器堆管理截止时间，到期后进入 timer 来源。取消的堆顶条目必须跳过，不能遮挡后续到期工作。定时器具有嵌套层级和相应最小延迟规则；interval 不是固定最低 1ms 的独立模型。

rAF 属于独立渲染机会，页面树共享帧时钟，默认虚拟 60Hz，可用 Browser 的 `frame_rate` 设置。同一帧使用统一时间基准，回调中新增或取消的 rAF 按当前批次规则处理，隐藏状态影响帧机会。Worker 有独立渲染调度设施，不与页面共享线程。

虚拟帧时钟不代表真实屏幕刷新、绘制或完整 Chromium 渲染流水线。

## 预算、取消与关闭

宿主操作使用提交时计算的绝对截止时间，排队、执行及检查点共享预算。`evaluate(timeout=30)` 默认有限预算；执行器的普通调用默认预算由 `PYV8C_CALL_TIMEOUT` 控制，未设置时为 60 秒，非正配置表示不设置该默认期限。

原生操作用身份票据绑定 watchdog。排队任务可取消；已经开始的工作不能承诺回滚副作用。Isolate 终止作用于整个活跃调用栈，不保证只停止跨 Realm 栈中的一个 Context。纯 Python 阻塞不能靠 V8 watchdog 中断，应移出 owner。

执行终止向外展开时，事件状态和资源登记仍需复位，但不得再运行页面 JS、错误事件或微任务检查点。只有原生接受且匹配当前票据的关闭请求才能被归为预期取消；其他终止仍报告失败。

load 开始与完成分别记录。加载事件中断后，不继续发送后续 pageshow、pagereveal 或成功回调，load 等待者收到失败；文档替换重置旧状态。每次调用页面代码后检查关闭、文档代次和身份。

Inspector 暂停不消耗 JS 执行预算，宿主等待期限仍有效。关闭先唤醒暂停/启动门，再在 owner 完成释放。窗口关闭、Worker 终止和资源过期分别处理，不混同为清空共享队列。
