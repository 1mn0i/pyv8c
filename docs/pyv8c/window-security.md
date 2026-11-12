# 窗口与同源访问

**简体中文** | [English](../en/pyv8c/window-security.md)

[文档索引](../README.md)

## Window 与 iframe

Page 表示一个文档及宿主状态，iframe 创建子 Page 和独立 Realm。父子同步互访在同一 WindowExecutionGroup 的 owner 线程完成，跨窗口访问通过 WindowProxy 路由，而非将子窗口固定为父 Realm 的普通对象。

初始 about:blank 在 iframe 建立上下文时同步可见；src、srcdoc 的后续提交与资源完成遵循各自生命周期。contentWindow 的代理身份、当前文档、构造器及原型身份分别管理，导航不应简单复制父窗口对象替代子 Realm。

iframe 移除会失效所属文档资源登记，并异步释放子上下文；移除重插及 srcdoc 替换必须防止旧回调完成新文档。传输结束不代表子文档加载屏障已经满足。

## 来源身份

有效 origin 由文档创建和导航规则维护。about:blank、srcdoc 以及 Blob 文档涉及创建关系继承；sandbox 可产生独立 opaque origin。序列化结果为 `null` 的两个 origin 不一定相同，不能按字符串相等授予访问权限。

同源判断不重新从页面 URL 猜测。导航后的新文档使用新身份，仍被引用的旧 Realm 与活动文档分开处理。资源发起方和跨窗口访问方都需使用真实 Realm origin。

## 跨源访问

跨源 WindowProxy 只开放允许的窗口操作及属性，其他访问使用对应拒绝行为。属性读取、写入、枚举、descriptor 和原型访问不能各自绕过访问检查。window、self、frames、parent、top、location、postMessage 等成员按其规则处理，而非开放整个对象。

`postMessage` 通过任务源异步交付，携带发送方身份和目标来源约束，并按实现的 structured clone 规则复制数据。窗口名、数字索引和 WindowProperties 命名访问需要同时遵守 DOM 身份、存活与访问边界。

## 关闭与保留引用

子页面关闭只释放其资源，不停止兄弟页面仍使用的 owner 或 Isolate。导航及移除不能允许迟到回调操作新文档；保留旧 Realm 引用也不能绕过同源规则。原 Context 最终关闭后，关联活 JS 句柄失效。

Isolate 终止会展开整个活跃跨 Realm 调用栈，不承诺仅中止某一个窗口。资源代次取消与微任务队列归属是不同问题，不能通过清空共享 Agent 队列解决旧文档回调。

## 当前边界

当前模型不覆盖 OOPIF、完整 COOP/COEP、Origin-Agent-Cluster、`document.domain` 和浏览器级安全沙箱。跨源行为应使用同一页面、origin、响应头和操作序列与本机 Chrome 对照，不以原型表面或单次 contentWindow 读取推断完整兼容。
