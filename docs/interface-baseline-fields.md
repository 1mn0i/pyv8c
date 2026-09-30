# Interface baseline：格式、来源与生成

[返回文档索引](README.md)。运行时读取 [interface-baseline.zip](../pyv8c/baselines/interface-baseline.zip)，加载逻辑见 [baseline.py](../pyv8c/engine/baseline.py)。
baseline 记录接口形状（成员签名只在审计产物里），不包含完整浏览器行为实现。

## 当前快照与版本边界

2026-09-30 实读（154 产物在这一天用本地 154 检出重生成，运行时默认也换成了它）：

| 产物 | 接口 | 成员 | 形态 |
| --- | --- | --- | --- |
| `pyv8c/baselines/interface-baseline.zip`（运行时默认） | 1,254 | 10,636 | 新形态：无签名、有 `main_world` |
| `baselines/154.0.8030.0.zip`（版本副本） | 1,254 | 10,636 | 与运行时逐字节同内容 |
| `baselines/145.0.7632.162.zip`（版本副本） | 1,224 | 10,166 | 同上 |

生成命令（两个输出各跑一次；`--chromium-root` 用相对路径，`renderer_snapshot` 才留相对形式）：

```powershell
python tools/extract_interfaces.py baselines/154.0.8030.0.zip --chromium-root .chromium-build/chromium --report
python tools/extract_interfaces.py pyv8c/baselines/interface-baseline.zip --chromium-root .chromium-build/chromium
```

两种形态运行时都能读：签名是审计字段，`main_world` 缺席时按主世界解释（这正合旧产物省略 world 的语义）。
归档内成员名随输出文件名（`154.0.8030.0.json` / `interface-baseline.json`）。

同一份实测窗口面（`data/chrome-environment.json` 的 `window_surface` 段，Chrome 153，1,233 个名字）下，
`Object.getOwnPropertyNames(window)` 的结果：

| 产物 | 装出的名字 | 缺 |
| --- | --- | --- |
| 重生成后的 154（现为运行时默认） | 1,233 | 1（`Temporal`） |
| 重生成前的 154（`1bf15cf` 的产物） | 1,207 | 27（`NodeFilter`、`QuotaExceededError`、`ReadableByteStreamController` 等） |
| 145 副本 | 1,226 | 8（`AudioPlaybackStats`、`HTMLCameraElement`、`NodeRange` 等） |

`Temporal` 两份都缺，属平台自身缺口；145 副本缺的 8 个是 145 与 154 之间新增的接口。

换成这份产物后曾有一处待定，现已解决：冷启动预物化 25 个接口，而 `plan.staged` 只有 13（骨架），
多出的 12 个是祖先与窗口别名的目标（`webkitRTCPeerConnection` → `RTCPeerConnection`、`WebKitCSSMatrix` →
`DOMMatrix` 等）。`plan.startup` 现在把这三类都算进来，是"启动前建好哪些"的唯一答案；
`test_the_plan_names_every_interface_startup_builds` 守着它与实测物化集合相等的等式。
仍待定的是语料用例 `a_file_input_has_no_capture_member`：产物为 `HTMLInputElement` 声明了 `capture`，
而实测 Chrome 153 对 `type=file` 不暴露它。

metadata.report 仍包含 unresolved 签名；接口数量、成员数量和提取成功都不能当作功能兼容率。

CHROMIUM_VERSION 当前为 154.0.8030.0，它是源码获取工具的默认目标。
ZIP 的 renderer_snapshot 是生成目录来源信息，不含足以独立证明 Chromium release 的版本字段。
不要从目录名 Cronet145、默认 UA 中的 Chrome/152 或原生 V8 版本反推该 ZIP 的准确 Chromium 版本。
更新快照时同时记录源码 commit、生成命令和输出摘要。

## 根结构

下面是结构示意，不是已经实现的 Example 接口：

```json
{
  "_webidl": {"schema": 5, "compact": true},
  "Example": {
    "parent": null,
    "tag": "Example",
    "exposed_in": ["Window"],
    "constructor": {"exposed": true, "allow_new": false, "allow_call": false, "length": 0},
    "template": {},
    "members": {"instance": [], "prototype": [], "interface": []},
    "constants": []
  }
}
```

接口名就是顶层键，不另设 identity 对象。成员分组是列表，成员自身通过 kind 区分 attribute/operation。
所有父接口必须存在，继承不能成环；运行时拒绝非 schema 5 或 compact 不为 true 的输入。

## 字段职责

| 路径 | 含义 |
| --- | --- |
| parent / tag / exposed_in | 父接口、toString tag、允许的 realm 类别 |
| constructor.exposed | 是否允许暴露接口对象，与是否能 new 不相同 |
| constructor.allow_new / allow_call / length | 构造/普通调用许可与 JS Function.length |
| template | immutable_proto、undetectable、global_this、as_object、unconstructable、array 等模板信息 |
| template.call_handler / access_check | 可调用对象与访问检查元数据 |
| template.handlers | named、indexed、cross_origin.named/indexed 的角色声明 |
| members.instance / prototype / interface | 安装在实例、原型、接口对象上的成员 |
| members[].main_world | 成员是否属于主世界；其他世界的成员只在查表排序时让位 |
| constants | 常量，避免重复放入 interface 成员列表 |
| audit 独有 | constructor.signature、成员的 signature/setter_signature/overloads/raw/review、常量的 expression、world 原名、provenance |

handler 角色包含 get/set/query/del/enum/definer；compact 产物用 1 等存在性标记代替 C++ 符号。
`named`/`indexed` 是接口自己的拦截器，只来自 plain 的 `*PropertyHandlerConfiguration`；由
`SetAccessCheckCallbackAndHandler` 构建的那两份属于 `cross_origin.*`。抽取器用
`_webidl.report.handler_findings` 守住这条：plain 的 named 家族必须能在接口 IDL 里找到 named getter。
这些标记不是 Python callable；InterfaceCompiler 结合手写 IdlSpec 决定最终 handler。

compact 的成员只留运行时读的字段：name、kind、length、flags 或 property_flags、setter、
receiver、cross_origin、side_effect、main_world。
`property_flags` 是 `flags` 的整数形式（`V8Attribute` 位掩码），运行时优先用它；抽取器读不出
这段表达式时不给该字段，运行时回落到解析 `flags` 文本。compact 快照在有整数时才删掉 `flags`。
setter 为 1 表示有 setter，null 表示只读；安装位置由外层分组决定，不重复编码为位置元组。
常量包含 name、resolved、value、location；未成功解析的常量不能凭空补值。

## runtime 与 audit

默认提取器删除 C++ callback 符号、成员签名与重载、常量表达式、world 原名、审查用原始字段和 provenance。
编译期用的签名信息（status 为 generated/unresolved、参数类型与转换计划）只在 audit 产物里，运行时不需要。
--audit 会保留这些并设置 compact=false；这种输出供人工检查，不能直接交给运行时加载。
Runtime ZIP 和 audit JSON 应分别生成，不能通过手改 compact 标记把审计文件冒充有效运行产物。

## 运行时怎么读它

两层，顺序是读出 baseline → 装到 realm 上：

- `engine/baseline.py`：加载与缓存快照、解码每个接口（父链、成员、接口对象、按 realm 类别的暴露名单）
  并把「一个 realm 类别要装什么」算成一份计划，首次读时算一次并缓存。成员保留快照里的简写
  （`flags`/`receiver` 等），翻成绑定位是编译器的事。
- `engine/assembly.py`：把这些声明编译成原生 IdlSpec，包括成员属性位、占位实现与 realm 绑定回调。
- `platform/realms.py`：一份计划一次 `Context.install(names)` 装到全局上，接口对象的属性统一按
  Chromium 的属性位安装；realm 自己的注册状态（`platform/sessions.py`）回答原生侧对某个名字的问询。

baseline 列出的是 Chromium 能装的全部成员与接口，而参照浏览器只装其平台与设置允许的子集。
这部分事实在 `data/chrome-environment.json` 的 `window_surface` 段：由 `tools/collect_environment.py`
在采集环境的那次会话里从本机 Chrome 实测（`Object.getOwnPropertyNames(window)` 与 `Object.keys(window)`），
与环境的 `source` 共用同一个浏览器与时间戳；`engine/baseline.py` 读这一段，
`InterfaceTable` 用它收窄 Window 的成员与接口全局。
换成别的 Chrome 版本时重采集即可，不手写名单。

## 缓存与可信输入

ZIP 首次加载可在旁边展开同名 JSON；另外使用 .pyv8ccache 二进制缓存跳过重复解析和结构验证。
进程内还缓存已加载快照。源码发布与包数据以 ZIP 为准，本地 JSON/二进制缓存不应当作新的权威 baseline。
BaselineSnapshot.digest 是加载内容摘要，记录时不要未经核对就当作 ZIP 容器字节的 SHA-256。

二进制缓存使用 pickle，只能来自可信本地构建；不要接收外部提供的缓存文件。
损坏缓存可以回退重建，但这不构成对恶意 pickle 的安全隔离。排障时可移走明确对应的本地缓存，保留原始 ZIP。

## 生成流程

元数据工具可以在没有原生 pyv8 的环境下使用；仍需要 Python 包依赖以及已生成的 Chromium binding 源码。
先用 --help 核对本地工具参数：

```powershell
python tools/fetch_chromium.py --help
python tools/extract_interfaces.py --help
```

源码获取流程会下载 Chromium、处理依赖并生成 binding，不只是读取一个版本号；可能消耗大量时间和磁盘空间。
以下是手动维护命令，不会在普通安装/测试中自动执行：

```powershell
python tools/fetch_chromium.py --version 154.0.8030.0 --root .chromium-build/chromium
python tools/extract_interfaces.py pyv8c/baselines/interface-baseline.zip --chromium-root .chromium-build/chromium --report
```

已有生成目录时可传 --bindings-dir；指定 GN 输出用 --build-dir。
输出名为 .zip 或传 --zip 时生成压缩包；--audit 输出应使用单独文件，不覆盖运行时 ZIP。

更新后运行 [提取器测试](../tests/unit/test_interface_extractor.py)、[baseline 版本测试](../tests/integration/test_baseline_versions.py)、
[IDL 编译测试](../tests/unit/test_idl_compilation.py)、抽取器测试与完整浏览器回归共同验证 baseline 更新。
