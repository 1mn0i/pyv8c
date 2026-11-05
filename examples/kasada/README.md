# OTTO `/tl` 编码前样本

## 目录约定

- `otto.py`：未插桩复现入口。
- `scripts/`：其余可运行脚本。`scripts/otto_compare.py` 做 pyv8c/Chrome 对照；`scripts/otto_chrome_boundary_capture.py` 用 CDP 断点抓 Chrome 的编码前数组。
- 顶层 `*.json`：一次 `/tl` 的编码前样本，供分析参考，不是脚本依赖。
- `www.otto.de.har`：成功请求的 HAR 参考。

这些 JSON 文件是一次请求中交给 `TextEncoder.encode()` 的字符串解析结果，位于最终 POST 二进制内容生成之前。数组有 461 项，其中包含环境值和仍需进一步分析的编码字符串；它不是带字段名的完整解密结果，也不适合重放。数组顺序每次可能变化。

| 文件 | 采样环境 | 同一次 `/tl` 响应 |
| --- | --- | --- |
| `otto_tl_pyv8c_closure_empty.json` | pyv8c，闭包采样，不向 `window` 添加采样属性 | HTTP 200，空正文 |
| `otto_tl_chrome_success.json` | 本机真实 Chrome，独立启动后通过 CDP 连接，闭包采样 | HTTP 200，`{"reload":true}` |

Chrome 成功样本对应同一次真实请求的响应；Camoufox 样本仅作为另一浏览器的成功请求参考。样本包含会话相关的动态数据及浏览器环境信息，分享前应自行检查；它们不是 `otto.py` 的运行依赖。

2026-10-29 重新采集了 `otto_tl_pyv8c_closure_empty.json` 和 `otto_tl_chrome_success.json`：两者各包含 461 项，同次 `/tl` 分别返回 HTTP 200 空正文和 HTTP 200 `{"reload":true}`。Chrome 使用本机独立启动的 Chrome 156、临时配置目录和代理 `127.0.0.1:7890`，`navigator.webdriver` 为 `false`。两次采样检查均未发现辅助名称成为 Window 自有属性；不过新的 Chrome 样本中有两个异常堆栈包含 `__patchCalls`，这是插桩痕迹，比较堆栈时需要考虑，不能将其当作原生 Chrome 行为。

同日的权限接口对照确认，Chrome 对 `permissions.query({name: 'push', userVisibleOnly: true})` 返回通知权限状态，pyv8c 之前无条件拒绝。框架修复后相关测试通过；未插桩的 `otto.py` 复测仍返回 `/tl` HTTP 200 空正文，没有证据表明该修复改变了本站检测结果。

同安全上下文下逐个对照默认权限名时，Chrome 156 和 pyv8c 的状态一致；带 `allowWithoutGesture` 的剪贴板描述符另有差异，已修复该选项、属性读取顺序和 Chrome 观察到的共享状态名称。最近一次针对站点查询来源的插桩未进入权限查询或 `/tl`，无法将该修复归因于数组里的权限分组差异。

进一步追踪 VM 对 `PermissionStatus.state` 的读取，确认权限分组来自逐项查询 Permissions API。成功 Chrome 的实际同源挑战 iframe 中，`background-fetch` 和 `storage-access` 为 `prompt`，而旧 pyv8c 为 `granted`；本地同源 iframe 和 about:blank iframe 均复现 Chrome 的结果，顶层两项为 `granted`。修复后重新采样，`bf` 和 `sa` 均进入 `prompt` 分组；未插桩 `/tl` 仍返回 HTTP 200 空正文。`ln`、`lna`、`lon`、`tsa` 等其余候选权限差异涉及当前 Chrome 156 与框架环境档案的名称集合不同，尚未统一基线验证。

对单项 `[null]` 的追踪：成功 Chrome 样本为 22 项，新一次 pyv8c 采样为 29 项。VM 记录显示部分空值位于读取 `navigator.brave`、`navigator.duckduckgo`、`navigator.globalPrivacyControl`、`navigator.dateTimeString`、`navigator.mobileNetworkHash` 等属性之后；本机 Chrome 对这些属性也返回 `undefined`，不能据此判定缺陷。JSON 数组中的 `null` 也可能由 `undefined` 或非有限数序列化而来。更深的 Chrome VM 插桩运行未能到达 `/tl`，因此尚不能逐项配对剩余空值或证明其数量差异的原因。

之后采用闭包内轻量 VM 插桩，Chrome 同次 `/tl` 仍返回 `{"reload":true}`，捕获到空值写入及邻近的属性读取。部分空值邻近 `canPlayType` 检测；从 pyv8c 实际站点执行中提取出的 36 个媒体类型及编解码参数，与同机 Chrome 逐项对照，36 项结果均相同。另有若干空值邻近针对未连接 iframe 的 `document.body.contains()` 检查；目前仅确认执行路径，尚不能证明其在 Chrome 中产生不同结果。VM 空值写入事件包含中间数组和重复写入，数量不能直接等同最终明文中的 `[null]` 数量。

进一步对照成功 Chrome 的轻量 VM 记录，四类涉及 `body.contains()` 的空值路径在 Chrome 中也出现，包含 `srcdoc`、临时 iframe 和跨文档检查；不能把这些空值当作 pyv8c 独有差异。另从站点执行提取到 16 次 WebGL2 `getParameter()` 调用，在同机、相同 GPU 的 Chrome 中逐项比较，结果全部一致。Shadow DOM 的一处邻近调用为对 `p` 元素调用 `attachShadow()`，两边基本操作都成功，仍不能据此解释空值数量差额。

进一步在分析副本中观测 VM 字符串解码与空值写入的位置，将已确认的浏览器 API 输入整理为 `scripts/otto_null_observed_probes.js`，包括可选 Navigator 属性、`plugins` 自有描述符、`createEvent('TouchEvent')` 和未连接 `srcdoc` iframe。相同安全上下文下，Chrome 156 与当前 pyv8c 四组探针结果一致。该文件是读取操作的隔离复现，不是 VM 检测函数的完整反编译；实际分支、值包装与最终数组索引尚未恢复。VM 指令位置和混淆名随脚本版本变化，不应跨采样直接配对。

Spider King 证据复核：早期页面全局插桩的解码记录出现 `__vmStrings`，因此该运行受到目标读取分析变量的污染，不能视为干净的逐项证据。后来尝试闭包内同时记录解码和数组写入，未在 Window 上暴露标识，但代理数组检测生成异常长的 `length` 访问序列，采样正文从约 19 KB 膨胀到约 135 KB；该实验同样不用于判定差异。单次诊断中，仅将 pyv8c 的 `user_agent` 文本改为成功 Chrome 的 156 值，461 项中单元素 `[null]` 仍为 29 项，`/tl` 仍为空；这是单次跨会话观察，不能排除其他版本、客户端提示或传输身份差异。

随后尝试 Chrome 调试器函数调用断点，不替换页面的 `JSON.stringify` 或 `TextEncoder.prototype.encode`。一次 `JSON.stringify` 断点运行暂停 194 次，局部作用域检查未找到 461 项数组，同次 `/tl` 为 `{"reload":true}`；一次 `encode` 断点运行暂停 37 次，检查到的局部作用域也未找到目标字符串，同次 `/tl` 为 `{"reload":true}`。这些是受限作用域检查，不足以证明目标调用没有经过这些函数。增加检查量的一次运行发生超时，说明断点频率和检查成本仍可能干扰流程；目前没有获得可关联最终字段的原始空值类型。

同日进一步追踪尺寸字符串：检测创建 `type=button` 的 input，设置 `all: revert; font-size: 100px; margin: -9999px`，分别把字符和文本或表情变体选择符写入 `value`，读取两个 input 的 `clientWidth/clientHeight` 并拼接。同条件本机 Chrome 156 对 `\u2b06\ufe0e` 返回 112×147、对 `\u26aa\ufe0e` 返回 96×135；pyv8c 分别返回 71×119、149×135。对照中的另外八个字符变体尺寸相同。这是字体回退候选选择差异，而非仅凭视口不同推断；尚未确认其对 `/tl` 响应的影响。

随后按本机 Windows 的对照调整了文本变体的字体回退顺序，保留页面指定字体的优先级；上述两种尺寸在本机回归中已与 Chrome 一致。未插桩的 `otto.py` 仍收到 HTTP 200 空正文。该回退顺序是基于当前环境的近似实现，尚未覆盖 Windows DirectWrite 的完整字体回退机制。

## 采集方法

以下记录本次分析方法，不是交付程序，也不能保证对站点后续版本继续有效。采样会修改脚本，可能改变检测结果或执行时序，因此必须同时记录同一次 `/tl` 的实际响应，并与未插桩运行比较。`otto.py` 本身保持不插桩。

1. 启动新的浏览器上下文或 pyv8c Profile，访问 `https://www.otto.de/`，使用需要的网络代理；观察动态挑战 iframe 的 `ips.js` 和 POST `/tl`。不要使用 HAR 中的响应、旧 cookie 或请求体重放作为当前样本。
2. 分析时在 `ips.js` 响应进入 JS 引擎前临时插桩。本次版本把虚拟机指令处理器包装在动态生成的 `Function` 中，其生成表达式可以用下列模式定位：

   ```regex
   new Function\((\w+)\+(\w+)\((\w+)\.slice\(0,-1\),(\w+)\)\+"\}"\)\(\)
   ```

3. 只在分析副本中，对生成的处理器源码中形如 `fn.apply(receiver,args)` 的调用插入观测函数，保留原调用和返回值。当被调用函数名是 `encode`、首个参数是长度大于 10000 且以 `["2:` 开头的字符串时，保存完整字符串。这个条件是本次样本的特征，不是协议保证。

   成功的 Chrome 样本使用闭包而非 `window.__vmStrings`：把观测函数和源码改写函数声明在 `ips.js` 自身的 IIFE 内，并将原来的 `new Function(源码)()` 改为 `new Function('__observe', 改写源码)(__observe)`。由此生成的处理器通过词法闭包访问 `__observe`；观测函数通过控制台消息向调试器输出捕获的字符串。采样时检查了 `window` 的属性名，未出现 `__vmStrings` 或 `__patchCalls`。这是减少全局对象污染的分析手段，不保证脚本改写或调试器不会被检测。

   pyv8c 的闭包样本采用相同的词法闭包思路，但将处理器源码的改写直接写入生成表达式，避免辅助函数名进入异常堆栈。观测函数创建带标记的临时文本节点，由仅供分析的 `Document.createTextNode` 接口覆盖将字符串交给 Python，并继续调用原实现；不启用全量 trace。该样本采集时，iframe 的 `window` 属性名未出现 `__vmStrings` 或 `__patchCalls`，原始编码前字符串也未包含这些采样标记。接口覆盖和脚本改写仍可能影响检测，不能推断其不可见。
4. 改写后先尝试 `new Function(改写后的源码)`；编译失败就保留原源码。本次正则曾错误命中 `Function.bind.apply(...)`，导致处理器无法编译，必须回退。插桩可能改变函数身份、异常或时序，即使编译成功也不能据此保证语义完全不变。
5. 在 `/tl` 发出后、挑战 iframe 被销毁前，从控制台消息或分析用执行上下文导出采样字符串，使用 `JSON.parse` 或 Python `json.loads` 解析并保存 UTF-8 JSON；不要仅凭 HTTP 200 判定通过。同一次响应正文必须实际是 `{"reload":true}`，才能标为成功样本。
6. 记录浏览器类型和版本、代理、是否插桩、请求与响应状态；比较时先建立检测值的来源映射，不能按数组下标直接对比。需要验证某字段来源时，在分析副本中一次只替换一个读取值，确认该变化进入编码数组，并检查是否干扰请求。

本次 pyv8c 通过自定义分析用 `CurlCffiTransport` 改写该次 `ips.js` 响应；浏览器样本在分析上下文中拦截该次脚本响应。Chrome 成功样本来自本机 Chrome 独立启动的临时配置目录，调试端口不是 0，采样时 `navigator.webdriver` 为 `false`。浏览器、调试器和插桩仅用于采样，不属于最终案例的运行依赖。

### 2026-10-29 同版本 `/tl` 对照与 `Option` 修复

新增 `scripts/otto_compare.py`：同一代理、同一拦截策略下分别跑 pyv8c 与本机 Chrome，记录 `/tl` 正文、`x-kpsdk-*` 头长度和响应。同一 SDK 版本（`j-1.2.940`）的读数：

| 侧 | `/tl` 正文 | `x-kpsdk` 头长度 | 响应 |
| --- | --- | --- | --- |
| 本机 Chrome 156 | 19,714 | ct=164, dt=56, im=91, v=9 | `{"reload":true}` |
| pyv8c | 19,466 | ct=164, dt=56, im=91, v=9 | 空 |

修正一处早先的误读：HAR 里 `/tl` 的 29,483 是二进制正文被按 UTF-8 二次编码后的长度，实际 `content-length` 是 19,706。因此当前差距是 **约 248 字节（1.3%）**，只在正文，头长度已一致；`dt` 会随会话在 52–56 之间变化，不是稳定差异。

已排除的方向：

- **缺失 API**：只 trace `ips.js`，没有宿主 getter 返回 `undefined`（仅 `window.undefined` 与 `window.event`，Chrome 相同）；绑定失败只有 4 条，且与 Chrome 行为一致（`postMessage(PluginArray)` 的 `DataCloneError`、`createEvent('TouchEvent')`、`chrome.app.runningState`、不可迭代 `Blob`）。
- **`window` 自有属性**：Chrome 1234 项、pyv8c 1233 项，12 个 `webkit*` 名字与类型完全一致。
- **属性列表字符串**：两份样本里的 11 条 `g:` 列表全部一致。

已修复：

- `new Option(text, value, defaultSelected, selected)` 的 `text` 以前没有写入：`_fill` 里 `element.textContent = text` 在 Python 侧只是设置了一个普通属性，改为调用 DOM setter `set_textContent`。正文因此增加 8 字节；四条构造用例（空参、单参、`defaultSelected`、`selected`）与 Chrome 逐项一致，回归见 `tests/browser/test_legacy_factories.py`。

仍存差异（已修）：`Option.prototype` / `Image.prototype` / `Audio.prototype` 在 Chrome 中**就是**对应接口的原型（`Option.prototype === HTMLOptionElement.prototype`，其 `constructor` 指向 `HTMLOptionElement`），pyv8c 原来是子类原型，自有属性只有 `constructor`。

修复照 Blink 的 `CreateLegacyFactoryFunctionFunction`（`bindings/core/v8/generated_code_helper.cc:298`）：

1. 工厂模板照常 `FunctionTemplate::New(...)`，但**不再调用 `ReadOnlyPrototype()`**，让 `prototype` 保持可定义；
2. 实例化后从父接口函数取 `prototype` 对象，用 `DefineOwnProperty("prototype", 该对象, ReadOnly|DontEnum|DontDelete)` 写回，与 Blink 的 `PrototypeForType` + 同名调用一致；
3. 新增接口级覆盖键 `shares_parent_prototype`（`IdlSpec` 字段 + 字段表 + `INTERFACE_FIELDS` 白名单），由 `legacy_factories.py` 声明。

同一环境对照已 `equal`：三对工厂的 `prototype` 身份、`instanceof`、原型自有属性数量全部一致；`tests/browser/test_legacy_factories.py` 覆盖这两类断言。

下一步：在 Chrome 侧用 VM 闭包插桩抓取 940 版本的编码前数组。当前在页面里挂 `TextEncoder.prototype.encode` 没有命中，说明 SDK 走内部 `encode` 或 Worker 路径；拿到同版本数组后即可把 248 字节定位到具体字段。

同日晚些时候继续定位，补充三条事实：

1. `ips.js`（703,585 字节）里唯一的 `new TextEncoder().encode(...)` 只处理一个 **12 项**数组的完整性校验（`s.length===12`），序列化方式是 `s.join(",")`，不是 `JSON.stringify`；461 项 payload 没有在这条路径上。
2. `p.js`（154,090 字节）不含 `TextEncoder`、`join(`、`encode(`、`stringify`、`new Function(` 等任何字样，是自解密加载器，payload 组装代码在运行时才出现。
3. 在页面上下文分别挂 `TextEncoder.prototype.encode` 与 `Array.prototype.join`（长度 400–600、结果 > 10,000 字符）都没有捕获到数组；两次 Chrome 运行 `/tl` 仍返回 `{"reload":true}`，说明钩子本身没有破坏流程，但 461 项数组确实不在主 frame 里生成。

因此下一次尝试应直接针对 **Worker / 挑战 iframe 的脚本响应**：拦截该脚本请求并就地改写，或通过 CDP 附加到 worker 目标后再布钩。采样前先确认 `/tl` 仍返回 `{"reload":true}`，否则样本只能用于结构对照，不能作为成功基线。

继续尝试的三条结果：

1. **`/fp` 的 429 不是差异**：HAR 里 Chrome 的 `/fp` 同样是 `429`（839 字节，pyv8c 836 字节），随后 `ips.js` 200、`/tl` 200 `{"reload":true}`。这条阶段不再作为候选。
2. **Blob 源码注入是观察者有毒的**：在页面里包装 `Blob` + `URL.createObjectURL`，给每个 worker 源码前置 `join`/`encode`/`stringify` 采样钩子后，三次 Chrome 运行都**没有发生 `/tl`**（`TL null`）。该路径放弃。
3. **ips.js 处理器改写**：按 README 的正则定位到 1 处 `new Function(...)` 并注入 `__observe`，`PATCHED 1`，但 `/tl` 同样没有发生。原因是替换正则 `([A-Za-z0-9_$]+)\.apply\(` 也会命中 `a.b.apply(` 这类成员链（README 已记录过 `Function.bind.apply` 的误命中），生成的处理器无法正常工作。

结论：采样脚本必须在改写后**先验证 `/tl` 仍返回 `{"reload":true}`**，并在 `new Function(改写源码)` 编译失败时回退原始源码；替换正则要排除成员链（`(?<![.\w$])`）或只作用于裸标识符。当前没有拿到同版本的 Chrome 编码前数组，264 字节差异仍未定位。

后续按上述三点重试的结果：

- 裸标识符正则 + 回退后，改写**不再打断流程**（`PATCHED 1`、`/tl` 仍为 `{"reload":true}`），但观测函数一次都没有被调用（`TAP 0`）。说明这一版 handler 并不通过裸标识符的 `.apply(` 调用目标函数。
- 一旦在改写里写入额外状态（`self.__srcs.push(...)`，无论存全串还是只存长度与前 120 字符），`/tl` 立刻不再发生（`TL null`）——SDK 对 `window` 的新属性敏感。
- 改用不写 `window` 的 `console.log` 通道后，两次运行都在 `page.goto` 超时（60s）且 `TL null`，页面没有加载完成。

因此这一版 SDK 对 handler 改写是**可观察且会反制**的：改写后即使 `/tl` 仍成功，也拿不到目标数组；加状态则直接中断流程。下一步只剩 CDP 调试器路线（`Debugger.setBreakpointOnFunctionCall` 或 `Target.setAutoAttach(waitForDebuggerOnStart)` 在 worker 启动前挂载），不再走源码改写。

同日用仓库里的 `scripts/otto_chrome_closure.py`（上一轮成功过的 Chrome 闭包插桩）复跑两次，结果一致：

```
webdriver: False
instrumented closure: 1 factory: 1
navigation/wait TimeoutError
```

即插桩后 `/tl` 不再出现；而同一时间**未插桩**的本机 Chrome 仍正常通过（`/tl` 200、正文 19,682、`{"reload":true}`）。说明上一轮可用的闭包插桩在当前版本上已经失效，目标又收紧了一层。

关于样本对比的结论：同版本（940）的两份 461 项数组，**同下标只有 10 项相同**，短项序列相似度 0.09——顺序随运行变化，按位置或序列对齐都不成立。多重集差异（Chrome 独有 106 项）绝大多数是会话相关的时序、哈希与编码 blob，不能当作框架缺陷证据。抽查其中两个 Chrome 独有错误文案（`Function.prototype.toString requires that 'this' be a Function`、`Class extends value #<MimeTypeArray> is not a constructor or null`），两侧生成的文案完全一致，说明差异来自 VM 走了不同分支，而不是错误语义不同。

改用 CDP 直连（`websockets` + `Target.setAutoAttach(waitForDebuggerOnStart=true, flatten=true)`）后的结果：

- 页面目标可以正常附加并加载，`/tl` 仍返回 `{"reload":true}`，`Network.getResponseBody` 读到的正文与 Playwright 一致；
- 但 Playwright 报出的三个 blob worker **没有以 `worker` 类型出现在自动附加里**，只出现一个无 URL 的 `other` 目标；在该目标里注入 `TextEncoder.prototype.encode` 钩子返回 `hooked`，捕获计数仍为 0；
- 也就是说，这一版 Kasada 的 payload 生成不在可自动附加的 worker 目标里，或 worker 在附加完成前就已经跑完。

到目前为止可确定的结论：**不经浏览器调试器无法拿到 Chrome 的 461 项数组**；`/tl` 的差异仍锁定在正文（pyv8c 少约 264 字节），源改写、worker 注入、CDP 自动附加三条路径都被目标阻断。

同日用仓库里的 `scripts/otto_chrome_closure_control.py`（对照组：注入 `__observe`、包装 `new Function`，但 `__patchCalls` 原样返回源码、不改写 `.apply`）做了 A/B：

| 变体 | `/tl` |
| --- | --- |
| 不改写 ips.js | `{"reload":true}` |
| `new Function(src.replace(...))`（全局 `__observe`，无额外形参） | `{"reload":true}`，但观测函数从未被调用 |
| `new Function('__observe', __patchCalls(...))`（多一个形参） | /lt 不再出现（control 组同样 `navigation/wait TimeoutError`） |

结论：打断流程的是**给生成函数加形参**这一动作，而不是 `.apply` 的改写；而全局观测函数那条虽然 `/tl` 仍通过，却一次都没被调用，说明这一版 handler 根本不通过裸标识符的 `.apply(` 调用目标函数。两条合起来看，SDK 在检查生成函数的形状（形参/源码），README 里记录的钩子点在当前版本上已不存在。

最后试了不修改任何脚本的 CDP 函数调用断点（`Debugger.setBreakpointOnFunctionCall`，下在 `TextEncoder.prototype.encode`、`JSON.stringify`、`Array.prototype.join` 上，覆盖 page target 的所有执行上下文）：断点机制本身正常（18 个断点、13 次暂停），但每次暂停处检查 `arguments[0]`，既没有长度 >5000 的字符串，也没有长度 400–600 的数组。也就是说，payload 既不在 page/同源 iframe 里被 `JSON.stringify`/`join` 序列化，也不经过这些上下文的 `TextEncoder.encode`。这与 worker 目标无法自动附加的结论一致：payload 在拿不到的那个 Realm 里生成。

在 `%TEMP%` 里找到上一轮留下的边界探测脚本（只给 `/fp` iframe 的上下文下 `encode` 断点，可复现版本见 `scripts/otto_chrome_boundary_capture.py`，暂停时沿作用域链找 `["2:` 字符串）。它命中过一次，拿到了 payload 进入 `encode` 时的**调用形状**：

```
ENCODE_BOUNDARY ['u[0]']
scope [('l','function','function encode() { [native code]'), ('u','object','Array(1)'), ...]
source ...function(){return arguments}.apply(void 0,u)];for(var t=0;t<u.length;t++)n.r.push(u[t])}
       else n.r[2]=l.apply(r,u)}
```

即：VM 先构造一个只含该字符串的数组 `u`（`u[0]` 就是 `["2:...`），再通过 `l.apply(r,u)` 调用原生 `encode`，命中时 `/tl` 仍返回 `{"reload":true}`。这证明 payload 确实在 **`/fp` iframe** 里进入 `TextEncoder.encode`，而不是 worker。

但这条命中不稳定：随后 6 次运行（补丁版、放宽到 closure 作用域、只求值 `arguments[0]`）都是 `pauses` 44–48、`found 0`。说明同一个 encode 断点在不同运行里停到的是不同调用形态（`u[0]` 只是其中一种），目前的检测条件覆盖不到其余形态。

继续把扫描放宽到"每次暂停都检查 local + closure 作用域里的字符串和单元素数组"后，第 3 次尝试命中：

```
CAPTURE_ARR u 0 19711
TL 200 '{"reload":true}'
STATS {'armed': 1, 'pauses': 31} captures 1
SAVED 461
```

成功抓到 **Chrome 的 461 项 payload（19,711 字符）**，且同一次 `/tl` 仍返回 `{"reload":true}`。样本存为 `otto_tl_chrome_boundary_940.json`，抓取脚本存为 `scripts/otto_chrome_boundary_capture.py`（通过官方 CDP，不修改页面脚本）。

与 pyv8c 同版本（`j-1.2.940`）样本逐项对比：

| 指标 | Chrome | pyv8c |
| --- | --- | --- |
| 项数 | 461 | 461 |
| `[null]` | **22** | **30** |
| 紧凑 JSON 字符数 | 19,711 | 19,552 |
| 同下标相同项 | 18 / 461 | — |

只在 Chrome 出现（各 4 次）：`webkitRequestFileSystem`、`webkitResolveLocalFileSystemURL`、`requestResize`、`ongamepaddisconnected`、`1020`，另有 2 次是完整 UA 串。

只在 pyv8c 出现（各 2–4 次）：`chrome`（4）、`Option`、`Audio`、`showSaveFilePicker`、`showDirectoryPicker`、`0`、`1080`，以及 7 个额外的 `[null]`。

顺带核对了 top window 与同源子 frame window 的自有属性名：两者都只差一个 **`Temporal`**（Chrome 有、pyv8c 没有），其余 1234 个名字完全一致。所以上面那些 window 成员差异不是"属性缺失"，而是 VM 在不同运行/不同枚举路径下记录的条目不同，尚需结合暂停位置进一步定位。

### 根因：window 自有属性的安装顺序不同

上一段"枚举路径不同"的猜测不成立。用同一 fixture 取未排序的 `Object.getOwnPropertyNames(window)` 后，结论明确：

- 集合几乎相同（只差 `Temporal`：Chrome 有、pyv8c 没有）；
- **顺序只有 24/1234 个位置相同**，第一个分歧在索引 23（Chrome `SuppressedError` vs pyv8c `globalThis`）。

关键位置对照：

| 名字 | Chrome 索引 | pyv8c 索引 |
| --- | --- | --- |
| `Option` / `Image` / `Audio` / `webkitURL` | 65 / 66 / 67 / 68 | 1231 / 1230 / 1232 / 1228 |
| `chrome` | 897 | 1229 |
| `crashReport` / `cookieStore` | 898 / 899 | 241 / 242 |
| `showDirectoryPicker` / `showSaveFilePicker` | 1151 / 1153 | 295 / 297 |
| `ongamepaddisconnected` | 1164 | 240 |
| `requestResize` / `webkitRequestFileSystem` / `webkitResolveLocalFileSystemURL` | **1231 / 1232 / 1233** | 289 / 290 / 291 |

pyv8c 的中段是**按接口名字母序**安装的（897–899 是 `ReportingObserver`、`Request`、`ResizeObserver`），Chrome 是 IDL/定义序，并把 legacy factory 与 window 扩展成员插在特定位置。payload 里 Chrome 独有的三个名字正好是 Chrome 的最后三个；pyv8c 的最后三个是 `Image`、`Option`、`Audio`。这说明 VM 是按**索引**读取 window 属性名的，顺序不同就直接体现为 payload 不同。

可行性：1233 个自有属性中 **1226 个 `configurable: true`**，只有 `Infinity`、`NaN`、`undefined`、`window`、`document`、`location`、`top` 7 个不可配置，且它们本来就在头部。因此可以在 realm 初始化末尾按 Chrome 的顺序做一次 `delete` + `defineProperty` 重排（复用原描述符，访问器仍是实时的），把顺序对齐。

### 重排实验：不可行，问题在 IDL 数据顺序

按上面的思路做了 `delete` + `defineProperty` 重排原型（复用原描述符，先缓存 `defineProperty`/`getOwnPropertyDescriptor`，否则删除 `window.Object` 会连带删除全局绑定）：

```
ORDER 1234
RESULT ms/total/same [417.4, 1233, 190]
```

两个结论：

1. **代价不可接受**：单个 realm 417 ms，iframe 还要各来一次；
2. **无效**：1233 个位置只对齐 190 个——V8 全局对象的自有属性顺序不是简单的插入序，删除再定义无法可靠重排。

继续查数据源：`pyv8c/idl/idl.zip` 里的 `idl.json` 共 1254 个接口，**接口表是按字母序排列的**（`ANGLEInstancedArrays` 开头、`XSLTProcessor` 结尾），而 Chrome 按 IDL/定义序安装。这才是"pyv8c 中段像字母序"的直接原因。

因此正确的修法不在运行时，而在构建期：把接口表与 `Window` 成员表按 Chrome 的 IDL 顺序排好再生成模板。可用的顺序数据就是本次抓到的 `Object.getOwnPropertyNames(window)`（1234 项），从中可以反推接口构造器的相对顺序。这属于 IDL 数据与 binder 安装顺序的改动，范围比运行时重排大，但方向明确。

### 改 `build_plan` 的顺序：相对顺序可以对齐，但存在固定偏移

`build_plan`（`pyv8c/engine/idl.py:800`）用的是 `tuple(sorted(...))`，是接口全局字母序的来源。按抓到的 Chrome 顺序（`XPathResult` → `WebSocketStream`，本质是逆字母序）排序后测量：

| 名字 | Chrome | pyv8c（改序后） |
| --- | --- | --- |
| `XPathResult` | 74 | 303 |
| `Request` | 282 | 511 |
| `ReportingObserver` | 283 | 512 |
| `Node` | 358 | 587 |
| `Event` | 535 | 764 |
| `WebSocketStream` | 1225 | 1217 |

两件事：

1. **相对顺序已经与 Chrome 一致**（`XPathResult` < `Request` < `ReportingObserver` < `Node` < `Event` < `WebSocketStream`）；
2. 但每个接口全局都**整体后移 229 位**——pyv8c 先创建 Window 模板的 188 个属性（`window`/`self`/`document`/…，连同内建对象），之后才由 `context.install` 安装接口全局。

也就是说：接口的**排序**是 Python 侧能控制的，位置偏移是"原生模板 + Python 安装"两阶段结构造成的。要完全对齐，接口构造器必须作为 Window 模板的一部分、按 IDL 顺序与属性交错安装——属于 binder/原生改动。本轮实验已回退，结论保留。

---

## 2026-11-03 对齐结论：请求层与 API 层已一致，剩余差异在 VM 执行路径

### 已对齐的部分

当时基于实测顺序数据，`Object.getOwnPropertyNames(window)` 与 Chrome 154 逐位一致（1235/1235，含 `Temporal`）。当前顺序由环境档案的 `bindings.own_order` 驱动；比较结果取决于所选环境和上下文。

本轮另修掉 7 个真实缺陷，每项都有探针或回归：

| 缺陷 | 验证 |
| --- | --- |
| `getContext('bitmaprenderer')` 缺失 | 探针 `equal`，`tests/browser/test_canvas_bitmap_renderer.py` 2 个回归 |
| `userAgentData.getHighEntropyValues().uaFullVersion` 恒为空 | 现为 `154.0.8030.0` |
| 未实现 `Accept-CH` | `/tl` 请求头与 Chrome HAR 逐项一致（含 `sec-ch-ua-full-version-list`/`-arch`/`-bitness`/`-model`/`-platform-version`） |
| 缺 `local-network` / `local-network-access` / `loopback-network` | 46 项权限面 0 语义差异 |
| CSS 系统字体关键字未解析 | `caption`/`menu`/… 10 个关键字 `equal` |
| 数字字重被折叠（`100`/`900`） | 12 个 VM 实际字体串 `equal` |
| `crypto.getRandomValues` 无 65536 字节配额 | 5 个用例 `equal`（含 `QuotaExceededError`） |
| `getContext('webgpu')` 返回 null | 现返回 `GPUCanvasContext`，5 种上下文类型探针 `equal` |

### 调用级对照（Chrome `/fp` 帧 vs pyv8c `/fp` 帧）

| 调用 | Chrome | pyv8c |
| --- | --- | --- |
| `toDataURL` / `fillText` / `measureText` | 80 / 72 / 67 | 80 / 72 / 67 |
| `permissions.query`（跨 reload 累计） | 56 | 56 |
| `enumerateDevices` / `storage.estimate` | 4 / 3 | 4 / 3 |
| `getImageData` | 6 | 7 |
| `WebGL.getParameter` | 86（单文档口径，会被 reload 低估） | 51 |

**口径修正**：`/fp` iframe 在一次流程里会**重载 7 次**（`sessionStorage.__loads = 7`）。按单文档读取只会拿到最后一次的计数，例如 `permissions.query` 会被读成 9，而跨 `sessionStorage` 累计的真实值是 **56**，与 pyv8c 逐项相同。因此上表中除 `toDataURL`/`fillText`/`measureText` 外的差异大多是测量假象，不能据此判断分支不同。

WebGL 的值、299/560 个常量、35 个扩展名、`UNMASKED_RENDERER_WEBGL` 完整字符串，以及 `WEBGL_debug_renderer_info` / `KHR_parallel_shader_compile` 的返回，两侧**逐项相同**。

### 仍未解决

```
[null]   29 vs 22
/tl      200 空正文
```

跨 reload 累计后，权限查询次数、返回结果、以及 WebGL 的值/常量/扩展/渲染器串在两侧都一致；此前记录的 `getParameter 51 vs 86`、`permissions.query 9 vs 56` 均为单文档读取造成的低估，不能作为分支不同的证据。

两侧的 opcode 轨迹都已能导出（Chrome 经 CDP `Fetch` 改写 `ips.js`，pyv8c 经 transport 改写），但字节码每次会话随机化，逐位对齐只有 2 条，无法按位置比较；要定位分支需要先建立"宿主调用 ↔ 指令"的关联。

### 结论与下一个突破口

请求层（头、UA-CH、cookie、`/tl` 正文长度）与 API 层（canvas、WebGL、字体、权限、crypto、属性顺序）都已与同版本 Chrome 等价。**剩余差异在 VM 的控制流**：同一段字节码在 pyv8c 里跑的探测次数与顺序不同，进而产生 7 个额外 `[null]`。

下一步做 opcode 级对照，工具已就绪：

- `scripts/otto_vm_dump.py`：在 pyv8c 侧改写 `ips.js`，导出解码后的 VM 程序（349,408 个值）与动态指令轨迹（单次会话 58 万条，取指规则 `opcode = 值 >> 5`、低 5 位为标签）；
- `scripts/chrome_call_sequence.py`：在 Chrome 侧用 CDP 后台 reader 采集每个执行上下文的有序调用序列（`/fp` 帧 373 条）；
- 对照方法：以调用序列做最长公共前缀，向前回溯到对应 opcode 段，定位触发分支的那次探测。
