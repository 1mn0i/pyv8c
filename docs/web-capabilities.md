# Web 能力与实现范围

[文档索引](README.md)

## 二进制与消息

Request、Fetch、XMLHttpRequest 和 Blob 在所属 Realm 的 owner 上读取 BufferSource。ArrayBuffer、TypedArray 和 DataView 按偏移及长度复制，包含空视图；提交后修改原缓冲区不会改变已提交内容。原生判断不读取页面可改写的 constructor 或 transfer 方法。

Window、Worker 与 MessagePort 共用结构化克隆，支持循环引用、Map、Date、共享缓冲区视图及已实现的 Blob、File、DOMException。接收值由接收 Realm 创建。transfer 列表先验证类型、重复身份和分离状态，再序列化及准备接收值，最后提交缓冲区分离和端口移动。失败不提交框架所有权变更；getter 自身的副作用不回滚。消息内的 MessagePort 必须列入 transfer。SharedArrayBuffer 转移不受支持。

## Worker

DedicatedWorker 与 SharedWorker 提供 atob、btoa、structuredClone，Base64 与 Window 共用字符串转换和错误规则。

经典 Worker 的 importScripts 先转换全部 URL，再相对入口脚本解析并依次同步执行；嵌套导入仍相对入口解析。前一项加载或执行失败后不执行后续项。module Worker 调用抛 TypeError。依赖脚本通过独立请求会话加载，详见[资源加载](resource-loading.md)。完整模块加载、CSP、MIME 和 Fetch 预检兼容仍需按场景验证。

## DOM、事件与文档

importNode 创建目标文档所属的节点，支持深浅复制、DocumentFragment 和 SVG 命名空间；Document 与 ShadowRoot 不可导入。脚本的已开始执行状态随克隆保留，移动或重插已执行脚本不会重复执行。

复选框点击监听器看到预激活后的 checked 状态，取消默认动作后恢复 checked 与 indeterminate。宿主输入、元素 click 和 label 转发共用激活流程。click 使用 PointerEvent，srcElement 跟随事件目标，layer 坐标使用当前布局模型。

文档解析结束后先进入 interactive，再执行 defer 队列，完成后派发 DOMContentLoaded。回调关闭页面或替换文档后，旧流程停止推进。

## 样式与布局

声明解析识别字符串、URL 和函数内的分隔符。计算入口支持自定义属性继承、var 回退、数值 calc/min/max/clamp、部分颜色及变换计算。层叠规则、字体表、HarfBuzz 测量及样式失效保持统一。only-child 和 only-of-type 按元素兄弟区分。

getClientRects 返回矩形快照，隐藏或未连接元素返回空列表。目前布局提供单一边框盒，不提供完整行内碎片。支持嵌套 inline-block 的内边距及 min/max-width 约束。Windows 系统色按浅色主题常量验证，不代表深色、高对比度或动态主题兼容。

hasStorageAccess 与 hasUnpartitionedCookieAccess 返回所属 Realm 的 Promise。非活动文档拒绝；有效 origin、安全上下文和祖先站点决定默认状态。当前没有完整存储权限授予流程。

## Window 自有属性顺序

`Object.getOwnPropertyNames(window)` 的 Blink 安装段对齐到项目基线 Chromium 154.0.8030.0：legacy factories 在接口构造器之前，模板阶段构造器按接口名降序，Window 成员按 attributes、后置 template attributes、operations 的顺序排列；context 阶段先安装宿主 `chrome`，再安装 context-dependent 成员（`crashReport`、`cookieStore`、`caches` 等），最后安装条件接口构造器。顺序由随包的 `pyv8c/pyv8c/data/window-order.json` 描述，不依赖 Chromium 源码。

V8 内置全局段（`Object`、`Function`、`SuppressedError` 等）由本仓库 `pyv8/` 使用的 V8 版本决定，不在该数据范围内；换 V8 或换 Chrome 版本时该段会偏移。当前 `_pyv8` 以 `v8_enable_temporal_support=true` 构建 V8 15.2.20.1，`Temporal` 及其 `PlainDate` 等可用；`SuppressedError` 在 V8 15.2 位于内置段末尾，Chrome 154 的 V8 15.4 在其第 23 位，这是版本差异，不是 Blink 顺序问题。runtime-enabled 接口的默认启用状态也可能随版本变化，跨版本对照只比较双方共同安装的名字及其相对顺序。

## Canvas 与 SVG

Canvas/OffscreenCanvas 的 2D 上下文支持线性、径向渐变和颜色节点、rect 路径、尺寸重置及上下文属性。Pillow 提供绘制表面，字体测量使用现有文本能力。尺寸赋值重置像素与绘图状态，保留上下文对象和创建选项。框架绘制不等于 Chrome 完整光栅化、色彩管理或 GPU 渲染。

SVGPoint、SVGRect 提供可变单精度数值，SVGMatrix 保留双精度；这些属性拒绝非有限值。getCTM 组合祖先 transform、viewBox 和已实现的 preserveAspectRatio 对齐。getBBox 与 isPointInFill 的验收范围为矩形及矩形后代，不代表完整 path/text 几何和滤镜效果。

## 离线音频完成通知

OfflineAudioContext.startRendering 提供 Promise 结果，并通过队列派发 complete 事件，
支持 oncomplete 和 addEventListener。OfflineAudioCompletionEvent.renderedBuffer 与
Promise 的结果保持同一 AudioBuffer 身份；事件构造器要求有效的 renderedBuffer。
完成事件的顺序与标志已与 Chrome 156 对照，覆盖同步返回后替换 handler 的情形。
底层渲染仍使用框架现有的 Python 音频图，不代表与 Chrome 完整音频实现逐样本一致。
