# Window 安全模型

Window/iframe 访问检查随 Browser 自动配置，不需要用户调用额外的底层 API。
这是浏览器行为兼容层，不是完整 Chrome 实现，也不能作为执行敌意代码的安全沙箱。

## Origin

- HTTP(S)、WS(S)、FTP 使用 (scheme, hostname, effective_port) 比较。scheme/hostname 规范化，
  IDN hostname 转为 ASCII；默认端口与显式默认端口相同。
- 路径、query、fragment 不参与同源比较。
- about:blank iframe 继承父页面 origin；顶层 about:blank 使用独立 opaque origin。
- 本 BrowserContext 创建的 blob URL 从注册表取得创建者 origin（包括 opaque origin）；
  其他 blob URL 使用嵌入 origin，blob:null 没有注册项时使用可用的继承 origin。
- data 等 URL 使用唯一 opaque origin，即使 URL 字符串相同也不视为同源。
- 导航提交时更新 origin；WindowProxy 在访问时重新检查权限，不依赖创建时的快照。

## 跨源允许的接口

| 成员 | 行为 |
| --- | --- |
| window / self / frames | 返回同一代理 |
| top / parent | 返回访问者 realm 中的稳定代理；目标是访问者自己时返回当前 window |
| opener | 当前返回 null；尚无 popup opener 模型 |
| length | 当前文档已创建的 iframe 数量 |
| closed | 目标 Page 是否已关闭 |
| location | 返回受限 Location；允许赋值 location、location.href，以及 replace(url) |
| close / focus / blur | 可以调用；沿用当前运行时的 no-op 行为 |
| postMessage | 异步 MessageEvent，检查 targetOrigin，设置 data/origin/source |
| 数字 frame 索引 | 按 DOM 文档顺序访问已创建的子 frame |

document、navigator、history、screen、localStorage、sessionStorage、自定义属性，以及不在表中的
方法/属性，跨源访问抛出 DOMException，name 为 SecurityError，code 为 18。
非允许赋值、删除、敏感属性描述符读取也被拒绝；iframe.contentDocument 跨源返回 null。
location.href、origin、host 等敏感读取被拒绝。replace 当前复用导航队列，尚未单独实现
Chrome 的 session-history replacement 行为。

```javascript
const child = document.querySelector('iframe').contentWindow;
child === document.querySelector('iframe').contentWindow; // true
child.self === child;                                    // true
child.top === window;                                    // 顶层访问直接子 iframe

try {
  child.document; // child 跨源时拒绝
} catch (error) {
  console.log(error.name, error.code, error instanceof DOMException);
  // SecurityError, 18, true
}

child.postMessage({hello: 'world'}, 'https://child.example');
child.location.href = 'https://child.example/next';
```

导航和消息排入目标页面任务队列；Python 调用方应推进目标页面事件循环。

## WindowProxy identity

每个访问者 Window 缓存目标 Window 的代理。同源和跨源使用相同的身份机制，权限动态变化。
顶层页面 top === parent === self === window；直接 iframe 中 top === parent、top !== window；
嵌套 iframe 的 top 指向最外层窗口。document.defaultView、contentWindow 和数字 frame 索引
使用一致的代理映射。

修改 iframe.src 复用已创建的 Page/Window，保持旧 contentWindow 身份。同源属性访问转发到
实际子窗口，不会把 Python Window 的 global-this 标记错误解释为父窗口。

## WindowProperties 与代码组织

Window、WindowProperties、WindowProxy 和受限 Location 代理集中在
pyv8c/web/bom/window.py。其他 BOM 对象仍在 api.py，旧的 api.Window、
api.WindowProperties 和 api.WindowProxy 导入保留为兼容性重导出。

WindowProperties 是 Window.prototype 和 EventTarget.prototype 之间的命名属性对象。
Python 实现专用的 __v8_property_name_get__ / query / enum callback，不与 Window 自身的
named interceptor 混用。原生层将 callback 绑定到 holder 的创建 realm，借用其他页面的原型
不会错误地查找当前页面；跨源文档读取仍执行已有 origin 检查。

当前支持：

- 文档元素的非空 id，以及 embed、form、img、object 的非空 name。
- 命名 iframe 优先于同名元素，返回已有稳定 WindowProxy。
- 唯一匹配返回元素；多个匹配返回按文档顺序更新的 HTMLCollection。
- Window 自身及普通原型属性遮蔽动态名称；普通 window.foo 赋值可覆盖，删除覆盖后恢复动态查找。
- 命名属性描述符 writable/enumerable/configurable 均为 true，但动态名称不出现在 own keys 中。
- 直接对 WindowProperties 定义、删除属性被拒绝；原型不可替换，preventExtensions 返回 false。
- 数字字符串名称和 symbol 区分处理；对象没有 own constructor 属性。

专项回归见 tests/browser/test_window_properties.py。更完整的浏览上下文命名策略（例如独立
window.name 变更、popup target name、跨源导航后的所有名字可见性细节）仍不在当前简化 frame 模型范围内。

## 普通成员绑定与性能

Window 的 postMessage 通过 __v8_idl_spec__.methods 注册，onmessage 通过 __v8_idl_spec__.attributes 注册。
它们使用 baseline 中经过 review 的签名和常规参数校验，不通过通用 named handler 分发。
postMessage 支持 origin + transfer 和 options 两种调用形式；非空 transfer 的现有限制不变。
onmessage 使用 EventHandler 转换，非对象值清空为 null，非函数对象不会作为函数调用。

默认 hook=False 时，Window 不安装普通 __v8_name_get__/set/query/del/enum，
模板装配也不会从 baseline 补回这些 Python stub。命名元素仍由 WindowProperties 处理；
数字 frame 索引、跨源 named/indexed handlers 和 access check 不受影响。
WindowProxy 的跨源保护 handler 与普通 Window handler 是不同路径，仍然保留。

hook=True 是显式调试模式，底层仍会安装调试拦截器；性能测量应使用 hook=False。
tests/browser/test_window_bindings.py 检查绑定配置、普通读写的零 named callback 调用、
成员描述符、消息函数 identity、参数重载和消息投递。

## Native 实现

Window 自动配置 SetAccessCheckCallbackAndHandler。__v8_cross_origin_check__ 比较访问者与
目标 origin；跨源 named/indexed handlers 使用同一 WindowProxy 策略。白名单 accessor 使用
FlagCrossOriginCheck.kDoNotCheck，敏感成员使用 kCheck。

_pyv8 的 access-check callback 不再静默清除 Python 异常。Python DOMException 通过访问者
realm 的 DOMException 构造器转为 JavaScript 异常，保留 name/code。普通 callback 返回 False
仍保持 _pyv8 原有拒绝行为，不改变其他 embedding 的公开 API。

同步同源属性操作在 native 保存的目标 Context 内重入执行，不能在持有 isolate 锁的 callback
中同步等待另一个 executor，否则会死锁。消息和导航使用目标页面任务队列。

## 尚未实现的兼容性和安全机制

- document.domain、sandboxed iframe 完整策略、COOP/COEP、Origin-Agent-Cluster、site isolation、
  进程级安全隔离。
- 完整导航后的 Window/Document realm 替换、旧 Document/Storage 等引用的全套失效规则。
- 完整 Chromium WindowProxy 原型、所有反射描述符、symbol 和 named-frame 查询规则。
- 同源任意 JS 数组/普通对象经 Python bridge 转发时的引用 identity 全保真；基础值、自定义属性
  读写和已实现 DOM 对象访问已覆盖，但不能假设所有对象都具有 Chrome 的零复制引用语义。
- popup/open/opener、真正的窗口关闭/焦点管理、完整 Location API 和历史替换。
- postMessage transfer 列表、循环结构等超出现有 structured_clone 支持范围的数据；拒绝时为
  DataCloneError。接收消息可使用 onmessage 或 addEventListener('message', ...)。
- Blob URL 跨 BrowserContext 注册表的完整校验与撤销后的 origin 生命周期策略。

不要把 pyv8c 用作不受信任脚本的安全边界；它不提供与 Chrome 多进程沙箱等价的安全保证。

## 验证

pyv8c 的 tests/integration/test_window_security.py 覆盖同源/跨源访问、真实 DOMException、
原生 handler、identity、嵌套 frame、索引、导航、origin、postMessage 和 Context 清理。

_pyv8 的 tests/test_window_access_checks.py 覆盖 callback 调用、返回拒绝、同源允许、Python
DOMException 转发和多 Context 的 Python global 在 V8 GC 后释放。

Windows 使用 Python 3.10 ABI3 构建验证。编译前应结束加载该 .pyd 的测试进程，否则 Windows
会阻止覆盖扩展。完整验证分别在两个仓库运行 python -m pytest -q。
