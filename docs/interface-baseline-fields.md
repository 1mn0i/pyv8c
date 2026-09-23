# Interface baseline：格式、来源与生成

[返回文档索引](README.md)。运行时读取 [interface-baseline.zip](../pyv8c/baselines/interface-baseline.zip)，加载逻辑见 [baseline.py](../pyv8c/engine/baseline.py)。
baseline 记录接口形状和签名，不包含完整浏览器行为实现。

## 当前快照与版本边界

2026-09-09 实读：schema=5、compact=true、1,254 个接口条目。
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
| constructor.signature | 构造参数与转换计划 |
| template | immutable_proto、undetectable、global_this、as_object、unconstructable、array 等模板信息 |
| template.call_handler / access_check | 可调用对象与访问检查元数据 |
| template.handlers | named、indexed、cross_origin.named/indexed 的角色声明 |
| members.instance / prototype / interface | 安装在实例、原型、接口对象上的成员 |
| constants | 常量，避免重复放入 interface 成员列表 |

handler 角色包含 get/set/query/del/enum/definer；compact 产物用 1 等存在性标记代替 C++ 符号。
`named`/`indexed` 是接口自己的拦截器，只来自 plain 的 `*PropertyHandlerConfiguration`；由
`SetAccessCheckCallbackAndHandler` 构建的那两份属于 `cross_origin.*`。抽取器用
`_webidl.report.handler_findings` 守住这条：plain 的 named 家族必须能在接口 IDL 里找到 named getter。
这些标记不是 Python callable；InterfaceCompiler 结合手写 IdlSpec 决定最终 handler。

operation 常含 name、length、flags、receiver、cross_origin、side_effect、signature；
attribute 常含 name、setter、flags、receiver、cross_origin、side_effect 和可选 setter_signature。
setter 为 1 表示有 setter，null 表示只读；安装位置由外层分组决定，不重复编码为位置元组。
常量包含 name、resolved、value、location；未成功解析的常量不能凭空补值。

signature.status 为 generated 或 unresolved；source_evidence 记录生成依据。
参数按顺序记录 type/idl_type、optional、nullable、default、variadic、enum 等信息。
签名状态与业务实现是否存在是两个独立维度。

## runtime 与 audit

默认提取器删除 C++ callback 符号、常量表达式、默认 world 和 provenance 等非运行时信息。
--audit 会保留审计来源并设置 compact=false；这种输出供人工检查，不能直接交给运行时加载。
Runtime ZIP 和 audit JSON 应分别生成，不能通过手改 compact 标记把审计文件冒充有效运行产物。

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
