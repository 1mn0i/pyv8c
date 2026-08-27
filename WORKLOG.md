# pyv8c 工作记录

本文按时间记录 pyv8c 的需求、实现、验证和遗留问题。它是历史记录，不是当前状态基线；当前库的真实状态以 [HANDOFF.md](HANDOFF.md) 为准。

## 记录规则

- 每次开发完成后在文末追加一个日期章节，不修改或删除既有记录。
- 每条记录说明：目标、主要修改、验证结果、未完成事项。
- 未验证的功能必须明确写成“未验证”或“仍有限制”，不能写成已完成。
- `_pyv8/` 的修改也在这里记录，但代码提交仍在 `_pyv8` 自己的 Git 仓库完成。
- 新增能力完成后，同步更新 `HANDOFF.md` 的当前状态；不要只更新工作记录。

## 记录模板

复制以下结构追加到文件末尾：

```markdown
## YYYY-MM-DD：简短主题

### 目标

### 修改

-

### 验证

-

### 未完成事项

- 无，或明确列出限制。
```

## 2026-08-24：当前文档结构整理

### 目标

将架构说明和开发历史分离，避免 HANDOFF.md 继续膨胀并混入过时过程信息。

### 修改

- `HANDOFF.md` 定位为当前库最新状态说明。
- 新增本文件作为按时间追加的工作记录。
- 在两个文件中说明相互职责和维护规则。

### 验证

- 已确认 `HANDOFF.md` 和本文件位于 `pyv8env/` 项目目录。
- 本次只修改文档，未运行代码测试。

## 2026-08-24：V8 15.2.20.1 Windows 构建环境

### 目标

使用项目源码下载并编译 V8 `15.2.20.1`，不使用旧的 `v8.7z` 快照。

### 修改

- 修复 `_pyv8/v8_build.py` 生成 `.gclient` 时的 `custom_deps` 格式错误。
- Windows 上优先使用 depot_tools 的 `.cipd_bin/vpython3.exe` 执行 GN/autoninja，兼容外层 Python 3.10 与 depot_tools Python 3.11+ 要求。
- 删除 `_pyv8/v8.7z`。
- 在 `_pyv8/README.md` 和 `_pyv8/doc/USER_GUIDE.md` 增加 VS 2026、Windows SDK、depot_tools 和构建环境变量说明。
- 使用的环境变量包括 `PYV8_V8_VERSION`、`PYV8_VS_HOME`、`PYV8_VS_VERSION`、`PYV8_WINDOWS_SDK`、`PYV8_BUILD_ROOT` 和 `DEPOT_TOOLS_WIN_TOOLCHAIN`。

### 提交

- `_pyv8` 已提交构建修复：`5ac3b46`。
- `_pyv8` 文档修改已提交本地：`83d3027`；该提交是否推送以 `_pyv8` 仓库状态为准。

### 验证和限制

- V8 源码已同步到 `15.2.20.1`，GN 已成功生成 `x64 release` 构建目录。
- Ninja 编译已启动并持续运行过；完成状态必须以最终 `setup.py build_v8` 退出码和 `v8_monolith.lib` 产物为准。
- VS 2026 产生的 C++20 标准库弃用警告不是当前已确认的构建错误。

## 2026-08-23 至 2026-08-24：pyv8c 主要能力建设

### 已记录的主要目标和修改

- 创建独立 `pyv8c` 项目，实现 Browser、BrowserContext、Page 和独立 pyv8 Context。
- 建立显式接口注册表和 renderer baseline，固定 DOM/BOM JavaScript 原型链。
- 实现 DOM 树、HTML 解析、集合对象、选择器、事件传播和页面生命周期事件。
- 增加 fetch、XHR、History、iframe、Worker、Blob URL 和 WebSocket 基础能力。
- 接入可选 cyCronet 真实网络后端，保留 resource_loader 优先级和 Page 线程安全任务队列。
- 增加 `error.py`，统一 V8 Error、DOMException 和 DOM/BOM/网络异常映射。

### 历史验证记录

- 曾完成 pyv8c 单元、生命周期、网络、错误和 native smoke 测试组；历史记录中最高一次完整结果为 `21 passed`。
- 曾成功构建 `pyv8c` 纯 Python wheel。
- cyCronet 原生 HTTP/WebSocket 仍需安装实际依赖后使用本地 fixture server 完成端到端验证。

### 当前已知限制

详细限制以 `HANDOFF.md` 文末“真实遗留问题和后续方向”为准。本记录不复制限制清单，避免状态分叉。

## 2026-08-26：基于 baseline 的动态接口桩

### 目标

在不生成大量接口 Python 文件的前提下，让 baseline 中已公开但尚未实现的接口拥有可观察的构造器、prototype 和成员表面。

### 修改

- 新增 `pyv8c/interface_runtime.py`，按父接口拓扑动态生成类，并合并真实实现。
- 缺失方法、属性和构造器使用统一桩，直接通过 `loguru.logger.info` 输出“待实现”。
- 修改 Window、Worker Context 暴露流程，按 `exposed_in` 过滤公共接口；内部对象和 `__pyv8c_*` 不暴露。
- `Browser` 增加 `generate_interface_stubs`、`expose_unimplemented` 和 `stub_logging` 配置。
- `loguru>=0.7` 加入运行时依赖；已实现接口元数据优先，修复 URL/DOMException 构造器回归。
- 新增动态桩和全局隔离测试。

### 验证

- 动态桩专项测试通过。
- URL、DOMException 和既有核心回归测试通过；完整测试需使用项目 Python 3.10、pyv8 和 loguru 环境执行。

### 限制

- baseline 中手工审核字段的返回类型、常量数值和复杂 overload 仍不能自动推导。
- 桩只提供接口表面，不模拟复杂 DOM、布局、渲染或真实业务副作用。

## 2026-08-26：移除全局对象别名注入

### 修改

- 为 `WorkerGlobalScope` 补充标准只读 `self` 属性，返回当前 Worker 全局对象。
- 删除 Page 中对 `window/self/globalThis` 的显式 `context.expose()`。
- 删除 Worker 中对 `self/globalThis` 的显式 `context.expose()`。
- Window 和 Worker 现在统一由 V8 全局模板以及标准接口属性提供全局别名。

### 验证

- Window 验证 `window === self === globalThis`。
- Worker 独立 Context 验证 `self === globalThis`、`typeof window === "undefined"`，且 `self` 不是显式注入的 own data property。

## 历史补录：pyv8c 初始架构和严格原型链

### 目标

在当前目录创建独立的 `pyv8c` 包，以 pyv8 为 JavaScript 执行引擎，提供 Chromium-like BOM/DOM；重点是严格可观察的 JavaScript 原型链，而不是只提供同名 Python 方法。

### 修改

- 创建 `Browser`、`BrowserContext`、`Page` 和独立 Page Context。
- 以 Python DOM 节点作为唯一真相源，JS wrapper 通过 Page 级缓存暴露。
- 建立显式接口注册表，禁止根据 Python MRO 自动推断 JavaScript 继承。
- 固定 `EventTarget -> Node -> Document/Element/HTMLElement`、事件接口链和 Window/Document/BOM 对象模型。
- 增加 `Object.getPrototypeOf`、`instanceof`、`constructor`、`Symbol.toStringTag` 等原型行为的测试基础。

### 验证

- 建立基础 DOM、原型链和 native 生命周期测试。
- 初期测试结果记录为 `8 passed`。

### 未完成事项

- 当时仅覆盖首期接口，完整 renderer Web IDL、描述符、mixin 和所有构造器细节尚未实现。

## 历史补录：DOM 集合、解析器、选择器和事件

### 目标

扩展 DOM 树操作、HTML 解析、集合对象、CSS 选择器和事件语义，使页面脚本能够进行常见 DOM 操作。

### 修改

- 增加 `DocumentFragment`、`NodeList`、`HTMLCollection`、`DOMTokenList`、`NamedNodeMap`、`Attr`。
- 增加 `HTMLUnknownElement`、template、iframe、canvas、media、表单和表格元素映射。
- 实现实时 `childNodes`、`children`、按标签/类名集合以及索引、`item()`、`namedItem()`。
- HTML 解析支持 doctype、注释、属性、void 元素、自动闭合、template 内容和 script/style 等原始文本。
- 选择器改为 AST 表示，支持标签、ID、class、属性比较符、后代/子代/兄弟组合器、分组和常见伪类；非法选择器抛出 SyntaxError。
- 事件监听器记录 capture、once、passive、signal 和顺序，支持捕获、目标、冒泡、停止传播、默认行为和 `CustomEvent.detail`。
- 增加 `tools/extract_interfaces.py` 和 `interface-baseline.json`，保存父接口、构造器、标签、成员归属和 mixin 元数据。

### 验证

- 增加 DOM 集合、选择器、事件和 native smoke 测试。
- 集合、选择器和原型链回归纳入完整测试组。

### 未完成事项

- 完整 Blink 成员描述符、side-effect、Web IDL overload/union 和全部 parser insertion mode 仍未覆盖。

## 历史补录：cyCronet、HTTP、XHR、History 和 WebSocket

### 目标

在保留默认离线行为的前提下，增加可选 cyCronet 真实网络后端和浏览器 WebSocket。

### 修改

- `Browser` 增加 `real_network`、`resource_loader`、`cycronet_options` 配置。
- resource loader 优先于真实网络；Context 隔离 Cookie、连接和 WebSocket session。
- 网络线程只通过 `Page.post_task()` 投递结果，不直接访问 V8。
- 增加 `fetch`、`Request`、`Response`、`Headers`、异步 XHR、CookieJar、基础 CORS、redirect 和 opaque Response。
- 增加 `History` 的 state、push/replaceState、back/forward/go 和 popstate 基础能力。
- 登记并实现 `WebSocket`、`MessageEvent`、`CloseEvent`、`ErrorEvent` 的原型链和状态事件。
- Page、Context、Browser 关闭时清理请求、WebSocket 和 Cronet session。
- `pyproject.toml` 增加可选 `network` 依赖组。

### 验证

- 增加 fake Cronet、Page 队列、fetch、CORS、WebSocket 和资源 loader 优先级测试。
- 历史完整测试结果记录为 `13 passed`。

### 未完成事项

- cyCronet 原生依赖在当时未完成本机端到端安装验证，真实 HTTP/WebSocket 需要本地 fixture server。
- WebSocket Blob/ArrayBuffer 只有基础映射，不是完整 Blob 和二进制 structured clone。

## 历史补录：Worker、Blob URL 和结构化克隆

### 目标

将 Worker 从基础表面扩展为可执行脚本源、独立 Context 和消息生命周期。

### 修改

- Worker 使用独立线程和独立 pyv8 isolate/Context，不共享页面 DOM wrapper。
- 支持普通 URL 和 `URL.createObjectURL(new Blob([...]))` 生成的 Blob URL 脚本源。
- Worker 全局对象支持 `self`、`globalThis`、`onmessage`、`onerror`、`postMessage()` 和 `close()`。
- 页面与 Worker 之间通过线程安全队列传递消息；`terminate()` 停止 Worker。
- 普通对象、数组、TypedArray、ArrayBuffer 和 Blob 二进制数据执行复制，不跨 isolate 传递句柄。
- Blob URL 撤销或不存在时转换为 NetworkError。

### 验证

- 增加 Worker Blob URL、双向消息、TypedArray/ArrayBuffer、独立 Context 和 terminate 测试。
- 历史完整测试结果记录为 `15 passed`。

### 未完成事项

- Worker 仍不是完整浏览器调度器；定时器、fetch、`importScripts`、模块 Worker、Map/Set/循环引用 clone 和 MessagePort 未完整实现。

## 历史补录：页面生命周期和加载事件

### 目标

让 `set_content()`、资源加载、脚本执行、iframe 和 Page close 具备稳定且接近浏览器的生命周期事件顺序。

### 修改

- 引入 navigation generation 和资源等待集合。
- `Document.readyState` 支持 `loading -> interactive -> complete`。
- 通过 Page 队列派发 `readystatechange`、`DOMContentLoaded`、资源 load/error、Window load 和 pageshow。
- 导航/关闭时派发 `beforeunload -> pagehide -> unload`；beforeunload 可取消导航。
- 外部/内联经典脚本、link、img、media、iframe 进入资源跟踪；旧 generation 回调丢弃。
- 增加 `HTMLImageElement`、`HTMLScriptElement`、`HTMLLinkElement`、`HTMLStyleElement` 基础属性和事件。

### 验证

- 增加 lifecycle 专项测试，覆盖 readyState、事件冒泡、资源事件、脚本异常和导航取消。
- 历史完整测试结果记录为 `17 passed`。

### 未完成事项

- async/defer/module script、preload scanner、完整 HTML parser insertion mode、bfcache、布局和渲染仍未实现。

## 历史补录：Error 和 DOMException

### 目标

让 BOM/DOM/网络/Worker/WebSocket 的非法参数和非法状态以 JavaScript 可识别的标准异常呈现。

### 修改

- 新增 `pyv8c/error.py`，包含 V8 Error 家族、DOMException、DOMError 和标准 DOMException 工厂。
- 增加 `as_js_error()` 作为 Python、Page 队列、网络线程和 Promise 的统一异常转换边界。
- 将 DOM 节点关系、属性名、选择器、History、XHR、WebSocket、Worker、Blob URL 和关闭状态错误迁移到标准 TypeError、SyntaxError 或 DOMException。
- 登记 DOMException 接口并测试 constructor、length、name、message、code、instanceof 和 toStringTag。

### 验证

- 增加 `tests/test_errors.py`，覆盖构造器、DOM 非法调用、History SecurityError、fetch reject 和 Python/V8 桥接。
- 历史完整测试结果记录为 `21 passed`。

### 未完成事项

- 复杂 Web IDL overload/union、精确 receiver 错误文本、完整异常源码栈和 renderer 中 DOMException 成员描述符仍有限制。

## 2026-08-25：全局暴露隔离与 URL 浏览器接口

### 目标

移除 pyv8c 内部桥接对象在 `window`、`globalThis` 和 Worker 全局中的泄漏，并把 `URL` 从临时脚本补丁改为正式浏览器接口。

### 修改

- `interfaces.py` 增加接口的 Window/Worker 暴露范围和 interface 级方法声明，`expose_interfaces()` 按上下文过滤公共接口。
- `WindowProperties` 不再作为页面全局构造器，Worker-only scope 只在 Worker Context 暴露。
- 删除 Blob URL 全局桥接函数；Promise resolver 和 Worker 序列化改为闭包/函数对象调用。
- `URL` 实现组成部分属性、`searchParams`、`toString()`、`toJSON()`、`canParse()`、`parse()`、`createObjectURL()` 和 `revokeObjectURL()`。
- `interface-baseline.json` 增加 URL 成员、构造器、暴露范围和 core/modules source；提取工具支持合并生成 binding 并记录成员位置。
- 新增全局泄漏、URL 原型链、静态方法和 Blob URL 生命周期测试。

### 验证

- `24 passed`。
- URL 和 Blob URL 静态方法通过 V8 Context 验证。
- `Object.getOwnPropertyNames(globalThis)` 不包含 `__pyv8c_*`，`WindowProperties` 不再可见。

### 未完成事项

- WHATWG URL 的全部编码、协议特殊性和边界错误文本仍未完全覆盖。
- MediaSource Blob URL、完整暴露条件和复杂 Web IDL overload 仍需继续补齐。

## 2026-08-25：Blob URL 使用浏览器 origin 和 UUID 格式

### 修改

- `BrowserContext.create_blob_url()` 改为生成 `blob:<serialized-origin>/<uuid>`。
- origin 从创建 URL 的 Page URL 计算，默认端口省略，凭据不进入 origin，非网络/opaque origin 使用 `null`。
- UUID 改为标准带连字符的 UUID v4，不再使用 `blob:pyv8c/` 和无连字符的 `.hex`。
- `URL.createObjectURL()` 将当前 Page 传给 Context，确保页面、iframe 和 Worker 使用正确的创建 origin。

### 验证

- `https://github.com/path/index.html` 页面生成的 URL 匹配 `blob:https://github.com/<uuid-v4>`。
- 全量测试保持 `24 passed`。

## 2026-08-25：全量 Web IDL 参数校验

### 目标

为当前已经暴露的 DOM、BOM、URL、网络、XHR、Worker、WebSocket、事件和集合接口建立统一的 JavaScript 参数校验边界。

### 修改

- 新增 `pyv8c/webidl.py`，登记构造器、方法和属性 setter 的 `ParamSpec`/`MethodSpec`。
- 接入 `interfaces.py` 装饰器；已暴露操作没有显式签名时，运行时注册校验失败。
- 实现必需/可选/variadic 参数、`undefined`/`null`、字符串、布尔、数字、接口、字典、序列、枚举、callback 和 union 的基础转换。
- 普通额外参数按 Chromium 常见行为忽略；DOMTokenList 的 `add/remove` 保留 variadic 语义。
- `fetch()` 参数错误转换为 Page 队列中的 rejected Promise；同步 DOM 方法继续抛标准 `TypeError`/`DOMException`。
- 修正 `Headers`、`Storage`、`requestAnimationFrame`、URL/WebSocket setter 等签名登记。
- 扩展 `tools/extract_interfaces.py`，提取 renderer callback 中的 `NotEnoughArguments` 和 `NativeValueTraits` 信息，并在 `interface-baseline.json` 记录签名来源和人工审核策略。
- 新增 `tests/test_webidl_validation.py`，覆盖缺参、接口类型、variadic、字典、序列、构造器、setter、枚举和 fetch Promise reject。

### 验证

- `python -m pytest -q tests`：`30 passed`。
- 已验证项目可正常创建 Page、解析 DOM、执行脚本和运行现有 native smoke 回归。

### 未完成

- 复杂 Web IDL overload/union、完整 dictionary 字段和全部 renderer 成员仍需逐接口人工复核。
- 复杂异常文本、完整 Blob/ArrayBuffer structured clone、真实 cyCronet 端到端行为仍按原有遗留限制处理。

## 2026-08-25：完整 Chromium 接口基线提取

### 目标

修复 `interface-baseline.json` 中只有 URL 成员较完整、其他核心接口属性和方法为空的问题。

### 修改

- 重写 `tools/extract_interfaces.py`，从全部 renderer core/modules 生成 binding 提取完整接口快照。
- 使用平衡括号扫描器解析 C++ 表项，支持 `unsigned(...)` 包装、嵌套初始化、多个安装阶段和 callback 函数体。
- 提取属性 getter/setter、位置、描述符标志、receiver、cross-origin、side-effect、cached accessor。
- 提取操作 callback、length、位置、receiver、side-effect、参数转换和 `NotEnoughArguments` 信息。
- 增加 `ConstantValueConfig`、core/modules 重复来源合并、alias canonical interface 和生成报告。
- 重新生成 `interface-baseline.json`，覆盖全部 renderer 接口；URL 不再特殊处理。
- 调整运行时基线校验：完整快照不会自动暴露未实现接口；已实现接口校验父链、tag、暴露交集、继承成员和显式 Web IDL 签名。
- 新增 `tests/test_interface_extractor.py`，覆盖真实 binding 与嵌套 C++ fixture。

### 验证

- 提取报告：1173 interfaces、10362 members、1081 interfaces with members、602 interfaces with signatures、2351 manual-review-required、0 parse failures。
- `python -m pytest -q tests`：`33 passed`。
- wheel 构建已通过：`pyv8c-0.1.0-py3-none-any.whl`。

### 未完成

- 复杂 overload/union、完整 dictionary 字段、构造器 allow_call/allow_new 精确推导和常量真实值仍需人工审核。

## 2026-08-26：可替换、多版本并存的接口基线

### 修改

- 将默认基线移动到包内 `pyv8c/baselines/interface-baseline.json`，并通过 package-data 随 wheel 发布。
- `Browser` 新增 `interface_baseline_path`，并公开 `interface_baseline_path` 与 `interface_baseline_info`。
- 新增 `BaselineRepository` 和 Browser 级 `InterfaceUniverse`；基线按路径和内容哈希缓存只读快照，动态类、成员配置和校验状态按 Browser 隔离。
- 动态接口不再写入全局 `IMPLEMENTATIONS`，缺失成员不再修改真实 Python 类的 `__v8_method__` 或 `__v8_attribute__`。
- `_pyv8.Context` 新增 `class_configs` 参数，C++ class template 创建时优先读取 Context-local overlay；overlay 支持构造器长度/行为、`Symbol.toStringTag`、成员表、回调 owner 和显式 JS 父接口。
- Window、iframe 和 Worker Context 均使用所属 Browser 的 Universe；Browser 关闭不再关闭进程级 V8 runtime，避免破坏同进程中的其他 Browser 或后续 Browser。
- 修正 overlay 桩回调执行路径，以及 Python helper 继承但 Web IDL 无父接口时的集合成员安装。
- 补充 `HTMLIFrameElement` 的 `iframe` 标签映射别名。

### 验证

- `_pyv8` 使用 V8 `15.2.20.1` 执行 `python setup.py build_ext --inplace`，编译通过。
- 多版本专项覆盖包内默认基线、相对/绝对自定义路径、内容冻结、非法文件、动态类和模板隔离、真实 DOM identity、Window/Worker/iframe 归属及关闭隔离。
- `python -m pytest -q tests`：`48 passed`。
- wheel 构建通过；清单仅包含 `pyv8c` 包和元数据，包内 baseline 已核验存在且未混入 `build/` 或 `tests/`。

### 当前边界

- 外部 baseline 必须是完整 renderer 快照，不支持运行中热切换；切换版本需创建新 Browser。
- 复杂常量、overload、union 和完整 descriptor 仍受 baseline 的 `manual-review-required` 字段限制。

## 2026-08-27：Baseline 驱动的 Web IDL 绑定

### 修改

- 删除运行时 `_install_signatures()`、`SIGNATURES` 和 `CONSTRUCTORS`；`webidl.py` 只保留通用 BindingPlan 编译、类型转换、overload 选择和异常生成。
- `InterfaceUniverse` 按 Browser baseline 独立编译真实方法、属性 setter 和构造器，并通过 Context-local callback owner 执行，不再修改真实 Python 类。
- `_pyv8` class config overlay 增加 `__v8_constructor_adapter__`，构造器 adapter 由 class template 持有并在 Context 销毁时释放。
- 将 `__idl_stub__` 全量改名为 `__v8_idl_stub__`；新增 `__v8_direct_callback__`，彻底分离“IDL 桩身份”和“callback 调用约定”。
- baseline 升级为 schema 3；签名状态统一为 `generated`、`manual-reviewed` 和 `unresolved`。
- 新增仅供生成器使用的 `tools/webidl-reviews.json`。审核项固定 renderer callback/source 证据，证据漂移时拒绝生成；最终审核结果嵌入 baseline，运行时不加载审核 sidecar。
- 已实现成员存在 unresolved 签名时 Browser 初始化失败；未实现动态桩允许宽松调用并继续输出待实现日志。
- 修复 `HTMLIFrameElement` 的 `iframe` 大小写映射别名，并将接口参数错误改为包含接口、方法和参数位置的浏览器式诊断。

### 验证

- `_pyv8` 使用 V8 `15.2.20.1` 重新编译成功；constructor adapter 和 direct callback 专项测试通过，全量测试为 `163 passed`。
- pyv8c 专项覆盖真实类不变性、双 Browser 签名隔离、unresolved 严格/宽松策略、审核证据漂移和 V8 元数据标记职责。
- renderer 提取报告：1173 interfaces、10362 members、3458 generated signatures、220 reviewed signatures、2900 unresolved signatures、0 parse failures。
- pyv8c 全量测试为 `56 passed`；静态扫描确认生产代码不存在 `_install_signatures`、模块级 `SIGNATURES`/`CONSTRUCTORS` 和旧 `__idl_stub__`。
- wheel 构建通过：`pyv8-2.1.2-cp310-abi3-win_amd64.whl` 与 `pyv8c-0.1.0-py3-none-any.whl`；pyv8c wheel 包含 schema 3 baseline，不包含生成期审核 sidecar。

### 当前边界

- unresolved 签名集中在尚未实现的动态桩；复杂 dictionary 字段、union 分支和 overload 选择仍需继续增强提取器或完成生成期审核。
## 2026-08-27：代码审查与生命周期修复

## 2026-08-27：增量 HTML 解析与脚本调度

### 变更

- 增加 `IncrementalDOMParser`，解析过程可在 parser-blocking 外部脚本处暂停并在资源任务完成后恢复。
- 解析期脚本资源从开始标签即发现；经典、async、defer 和 module 使用独立调度路径。
- 增加静态 module import 的递归依赖优先执行；动态脚本插入默认异步，`async=false` 保持有序执行。
- 增加 parser、async/defer、动态脚本和 module 顺序回归测试。

### 验证

- `tests/test_parser_scripts.py`、`tests/test_module_scripts.py`：3 passed。
- pyv8c 全量回归：63 passed。

### 边界

- 未实现完整 V8 ES Module API、动态 `import()`、import maps、preload scanner、document.write 和完整 HTML insertion mode。

针对审查发现的六项高优先级问题完成修复：Context/Page 关闭状态、基于 monotonic 的定时器、DOM parentNode 一致性、严格 Worker structured clone、Worker 生命周期计数和统一 Page 异步资源队列。同时修复 Cookie path 边界、多 Set-Cookie、cyCronet timeout 重试风险，并为 `_pyv8.Context` 增加幂等 `close()` 与 expose 错误路径清理。

新增 `tests/test_runtime_invariants.py`，覆盖关闭后 API、定时器、DOM reparent、fixture 异步、clone 错误和 Cookie path。`tests/test_next_stage.py` 的 XHR fixture 断言已调整为异步时序。

验证：pyv8c `63 passed`，`_pyv8` `164 passed`；V8 `15.2.20.1`，Windows MSVC 2026，`build_ext --inplace` 成功。未完成项包括完整 transfer list、复杂 structured clone、CORS 预检、完整 Cookie 属性、cyCronet 真机兼容性和接口模板惰性加载。
