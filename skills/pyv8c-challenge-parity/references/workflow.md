# 加载诊断：从终点追到最早分歧

## 加载时间线

将两侧时间线按因果节点对齐，不要求毫秒时间或所有请求顺序完全相同。先对齐 URL、响应字节摘要、frame/文档、发起方和操作顺序，再比较状态。

| 阶段 | 必须回答 | 优先证据 |
| --- | --- | --- |
| 导航与传输 | 是否发起？经过哪些重定向？最终内容相同吗？ | 请求、方法/请求体改写、状态、Location、响应头及正文摘要 |
| 文档提交/解析 | 哪个 URL/origin 的哪一代文档？解析停在哪里？ | frame 导航、readyState、节点、parser/script 状态 |
| 资源发现/调度 | 元素或脚本是否产生请求？谁阻止/取消？ | 发起节点/调用栈、资源 token/代次、缓存/来源、失败原因 |
| 脚本执行 | 文件是否执行？在哪条语句停止？ | 首个未捕获异常、脚本内容摘要、模块依赖、断点 |
| 异步推进 | 等哪个 Promise、任务、事件或消息？ | resolve/reject、微任务与任务顺序、定时器、队列边界 |
| 渲染与应用 | DOM 已更新但样式/尺寸不对，还是逻辑没到？ | 节点状态、computed style、布局、应用就绪标记 |
| iframe/Worker | 创建、导航、执行、消息和关闭是否对应？ | 创建 Realm、脚本来源、postMessage、接收者 origin、终止与旧回调 |

先保存未注入基线。Chrome 可用 DevTools/CDP 的 Network、Runtime、Debugger、Page 域，或现有 Playwright 事件；原生 trace 主要反映桥接访问，并不覆盖 JS 内部所有控制流。需要 initiator、重定向链、响应头、worker 子目标时按需补采，运行器没输出不代表不存在。

## 从白屏出发的诊断

1. 确认拿到的 HTML/JS 是同一版本；服务端分流内容不同，不能直接归罪 JS 引擎。
2. 确认主脚本的实际请求、响应 MIME、编码和执行；区分“没请求”“请求失败”“拿到但被策略拒绝”“执行后抛错”。
3. 找业务入口等待的第一个条件，沿其生产者继续追踪。后续几百条错误可能都来自一个错误原型或导航后陈旧回调。
4. 状态已就绪但界面不同，再分析 DOM、样式和布局；不要先批量补 navigator 字段。
5. 一次改变一个解释因素；删除复现中的无关代码，确认最小前提仍产生差异。

反向例子：`ready=false` → 入口等待 frame 消息 → frame 未执行脚本 → srcdoc 提交/基址错误。应对照 frame 文档身份和脚本 URL，而非给 `ready` 打补丁。

## 落层定位

以下是仓库根下的起点，不是固定调用链。先用 `rg` 找当前定义和调用方，勿恢复旧 ResourceController 或 resource_loading 模块。

| 领域 | 起点 |
| --- | --- |
| 请求准备、来源、逐跳策略 | `pyv8c/platform/resources/fetcher.py`、`request_headers.py`、`local.py`，以及 `pyv8c/network/` |
| 所有权、取消、回投与缓存 | 同目录 `ownership.py`、`handles.py`、`callbacks.py`、`cache.py` |
| 文档资源与 frame | 同目录 `documents.py`、`discovery.py`、`tracker.py`、`completion.py`、`frames.py` |
| 导航、任务、Realm 生命周期 | `pyv8c/platform/page.py` 及其导航、调度与生命周期调用方 |
| 接口暴露、成员、回调、身份 | `pyv8c/engine/idl.py`、`binder.py`、`bindings.py`、`pyv8c/platform/sessions.py` |
| 浏览器 API | `pyv8c/web/`；继续通过 ResourceServices 等服务访问运行时 |
| 原生模板、转换与生命周期 | 本仓库 `pyv8/src/interfaces/`、`values/`、`isolate/`、`handles/` |

不要直接访问 tracker 私有字典或假定资源记录是四元组。观测优先用现有事件/trace；必须临时包装内部方法时确认签名，原样转发并在 finally 恢复，记录其观察者效应。生产修复放在共享实现，而非 examples/skill 中 monkey patch。

## 挂起和失败

就绪超时、JS 同步死循环、网络超时、Context 关闭挂起分别记录。父进程总超时是最后边界，不应把被强杀当正常释放。不要在另一个线程调用 Realm API；不要用 `os._exit(0)` 清理后伪报成功。

慢任务下反复 `evaluate` 或 `run_until_idle` 本身可能排队。缩减观测、使用独立进程与外部日志/线程转储定位；暂停 DevTools 的时间计入实验条件。关闭时要验证拥有的线程、句柄、游标、资源记录已释放，单纯进程退出只能证明进程边界隔离。
