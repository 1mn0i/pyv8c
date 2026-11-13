# hCaptcha 分析与验证过程

日期：2026-11-13，Asia/Shanghai。最终运行方式与结果见 [README.md](README.md)。本记录保留排查顺序、失败现象及证据归属，便于 SDK 改版后继续核查。

## 分析约定

分析使用 `hello_js_reverse_skill` 3.9.0 和 Camoufox MCP 1.9.4，浏览器为 Firefox 152.0.4-beta.29。交付使用当前工作区的 Python 3.13.16、pyv8c 0.1.0 和内置 V8 扩展，不调用浏览器、MCP、HAR 或分析脚本。

分析产物位于 `artifacts/hcaptcha-20261113`，仅在本机保存，未随仓库提交。浏览器中的 Token 只作为对照信号；最终验收来自交付程序的新会话。分析浏览器已关闭。

## HAR 与浏览器基线

先检查案例目录内的 HAR，确认页面使用的公开 `userAccessSitekey`，并关联 SDK、配置、HSW、校验及后续应用请求。HAR 的应用请求中包含 `P1_` 前缀 Token；该历史值没有写入运行程序。

HAR 中可观察到 `api.js → hcaptcha.html → checksiteconfig → hsw.js → getcaptcha` 链。`getcaptcha` 使用 `application/octet-stream`，响应也为二进制。因此仅查找明文 JSON 字段无法完整解释 SDK 的最终结果。

浏览器首先加载真实登录页面，观察未经业务修改的资源和配置请求。随后显式调用页面已有的 `hcaptcha.execute`，得到 `P1_` 前缀、长度 2478 的对照结果，同时观察到 `getcaptcha` POST 返回 HTTP 200。不能将这一结果写成“首次打开页面就自动完成”：本次浏览器的通过结果是在显式调用后取得的。

捕获结束时共有 25 个请求，未报告丢弃请求或待完成请求。摘要见 `artifacts/hcaptcha-20261113/browser-network-summary.json`；捕获在首次导航后才启动，因此它不是该页面所有请求的完整清单。

## HSW 源码与 WASM 核查

浏览器捕获的完整 `hsw.js` 已保存为 `artifacts/hcaptcha-20261113/browser-hsw.js`，大小为 1,638,472 字节，SHA-256 为：

```text
43f69d81709c0b725015524b1eeb9b28a49ec8d27b6e07dba373ec11e30be61d
```

对应路径包含版本目录 `a4d8bab5a6d640ffc420104fd1bd82e3f1ace50377f1a8efe86ad622baaca3b9`。早期直连本地探针取得过另一个目录 `f55bf4a59e9accfad8ed13bb00440390c0d38107cf866f64661caa3d3f3eb974`，其脚本大小为 1,651,789 字节。最终通过的代理探针使用前一个目录。这说明应跟随当次配置选取资源，不能凭这些差异单独断言服务端拒绝由 IP、TLS 或脚本版本中的某一项造成。

源码中存在 `window.hsw` 入口，除处理配置中的 req 外，还包含数字模式分支。交付没有直接调用这些内部约定，而是通过公开 SDK 执行入口，让 SDK 提供完整输入并处理返回值。

最初按 `AGFzbQ` 特征截取 Base64 内容，虽然得到了 WASM 魔数，但交给 `WebAssembly.Module` 后出现代码段越界。该片段不能作为已还原的可执行 WASM：魔数和文件后缀不足以证明模块完整。

随后在独立的离线分析页面中执行当时本地保存的 HSW，在 `WebAssembly.instantiate` 调用边界取得实际交付给引擎的字节。该次使用的是早期本地分支脚本，与最终保留的浏览器快照不能混同为同一个版本。提取结果为：

| 项目 | 值 |
| --- | --- |
| 字节数 | 1,100,710 |
| SHA-256 | `d045225493c119e3520b1e61e4941f0b9a4a491e29917370c5ff7680e64ada95` |
| imports 数量 | 196，模块名为 `a` |
| exports 数量 | 23，包含函数和 memory |
| 独立模块检查 | Node 的 `WebAssembly.Module` 接受该模块 |

完整文件为 `artifacts/hcaptcha-20261113/runtime-hsw.wasm`，接口清单为 `artifacts/hcaptcha-20261113/wasm-interface.json`。这项检查证明实际模块可以构造及其接口可枚举，没有证明已经逆向出全部内部算法。源码、WASM 和浏览器快照均不参与交付运行。

## 本地探针与缺口定位

先构建最小页面，在目标 origin 下加载当前 `api.js`，渲染 invisible widget，显式执行并观察结果。完整探针保存在证据目录；它带有诊断输出和源码保存功能，最终入口已删除这些辅助逻辑。

| 阶段 | 实际现象 | 处理及结论 |
| --- | --- | --- |
| 初始探针 | Python 日志序列化 `pyv8.Null` 报错 | 改为在 JS 中 `JSON.stringify` 后读取。属于诊断程序问题，不能算 SDK 失败 |
| 第二次探针 | `AES-GCM` 的 `encrypt` 返回 `NotSupportedError` | 确认框架该操作未实现，增加真实运算适配 |
| 第三次探针 | SDK 创建音频压缩节点时缺少 `_audio_native` | 构建仓库现有音频扩展，保留原始音频实现 |
| 第四次探针 | `api.js` 连接超时，脚本加载失败 | 单独记录为网络失败；分析探针对 GET 增加有限重试 |
| 第五次探针 | `FontFace.load` 使用错误接收者时发生 Python `AttributeError` | 与浏览器最小用例对照，修正为拒绝的 Promise |
| 第六次探针 | 直连存在 GET 重试，结束时没有 Token | 不作为通过样本，也不据此判断加密算法一定错误 |
| 第七次探针 | 使用本机 6696 代理，`getcaptcha` HTTP 200，SDK 返回长度 2448 的 `P1_` | 首次本地通过，随后收敛交付并独立复测 |

对应日志为 `probe.log`、`probe-2.log` 至 `probe-7.log`。分析探针存在额外挑战分支，不属于交付白名单。最终入口没有继承探针的 GET 重试逻辑，也没有应用层自动重放校验 POST。

SDK 返回过额外挑战，前期还有宿主异常和网络失败。上述修复消除了各自可复现的异常，但本次没有做足以隔离所有因素的服务端对照，不能宣称任一修复单独保证通过。

## AES GCM 适配验证

交付在本 Browser 范围内覆盖 `SubtleCrypto.encrypt` 与 `decrypt`，由 PyCryptodome 执行 AES-GCM。其他算法仍交给框架原入口。适配保留 key usage 和算法检查，读取当次 IV、可选 additionalData、tagLength 及 BufferSource，认证失败转换为 Promise 拒绝。

排查时曾看到 `_options(algorithm).keys()` 为空。该对象是框架的惰性 Dictionary，通过 `.get()` 或成员读取访问 JS 属性；空的 Python 字典键集合并不代表 SDK 没有传入 IV，不能据此添加固定参数。

已知答案检查使用 128 位全零密钥、12 字节全零 IV 和 16 字节全零明文，期望的密文及标签为：

```text
0388dace60b6a392f328c2b971b2fe78a
ab6e47d42cec13bdf53a67b21257bddf
```

在真实本地 JS 调用中检查了该结果、ArrayBuffer 返回类型、带偏移的 DataView、解密往返，以及修改密文后返回 `OperationError`。接口语义参考 [Web Crypto AES-GCM 操作](https://www.w3.org/TR/webcrypto/#aes-gcm-operations)。这些检查针对本案例适配，不等于整个 Web Crypto 测试集通过。

## 音频扩展构建

本机没有可导入的 `_audio_native`，但 V8 构建目录中已有 Rust 工具链，因此没有另装 Rust。使用仓库 `pyv8c/audio_native/Cargo.toml` 和 `Cargo.lock` 执行 release 构建，目标目录放在证据目录的 `audio-build`。

构建时设置 `PYO3_PYTHON` 指向当前 `.venv\Scripts\python.exe`，工具链路径为 `G:\pyv8-build\versions\15.4.68\v8\third_party\rust-toolchain\bin`，核心命令为：

```powershell
cargo build --release --locked --manifest-path pyv8c/audio_native/Cargo.toml
```

将生成的 `_audio_native.dll` 作为 `_audio_native.pyd` 放入源码包后，成功导入并创建 `CompressorDSP(44100)`。未修改 Rust 音频源码，没有用常量代替音频计算。相关单元测试 30 passed。

## FontFace 行为修复与回归

用浏览器对 `FontFace.prototype.load.call(window)`、普通对象和 `null` 做最小探针，三者均返回 Promise，随后以 `TypeError` 拒绝。本地原先在 Python 的 `fn_load` 中读取 `_status`，导致宿主异常穿出调用链。

修复位于 [fonts.py](../../pyv8c/pyv8c/web/dom/fonts.py)：当接收者不是 FontFace 时，使用当前 Context 的 PromiseResolver 创建 Promise，以页面 TypeError 拒绝。正常 FontFace 的加载路径保留。

新增三个接收者回归，检查返回对象为 Promise、异步结果为 `TypeError`；加上原有字体测试，共 13 passed。音频和字体两组总计 43 passed。

在仓库根目录可以重跑：

```powershell
..\.venv\Scripts\python.exe -B -m pytest pyv8c/tests/unit/test_audio_native.py pyv8c/tests/browser/test_font_faces.py -q -p no:cacheprovider
```

## 交付收敛与最终验收

初始交付保留 AES-GCM 适配、请求白名单、最小 HTML、结果等待和异常处理，源文件共 109 行。它每次新建 Browser、Profile、Page，在 `finally` 中关闭 Browser，检查 SDK Promise 返回的 Token 前缀。没有读取分析目录，也没有把观察到的 req、时间、随机数、指纹或成功响应填入运行输入。后续应用户要求增加了轻量过程日志。

收敛阶段的一次调用报告 `network-error`，另一次带传输观察的调试调用返回 `P1_`。这两次不计入五次最终验收。随后对最终文件连续发起五次独立新会话，五次均通过；再用实际脚本路径单独执行 CLI，退出码为 0。结果及耗时见 [verification.json](verification.json) 和 [cli-verification.json](cli-verification.json)。

初始交付入口 SHA-256 为 `dfbfafa70d0234eba8f87953bfa56603b5693ad5321a37e49912f8e3d089c698`。源码、框架修复、音频二进制及主要分析产物的初始基线见 `artifacts/hcaptcha-20261113/project-manifest.json`。原始基线保留在工作区，没有用报告生成替换旧证据；后续日志版的源码哈希与此次历史基线不同。

首次补充报告仅复核已有文件及脱敏结果，没有重新发起线上校验。文档不保存完整 Token 或动态挑战值。

## 日志输出调整

应用户要求，在传输层增加只观察请求响应的 CurlCffiTransport 子类。打印关键 JS 和 iframe 地址、HTTP 状态及字节数；配置返回后打印 v、pass、挑战类型和当次完整 req；校验返回后打印请求响应大小。run 开始打印 sitekey，通过后打印 Token 长度。日志进入标准错误，CLI 的标准输出仍为完整 Token。

该观察层返回原始响应，不修改 SDK、配置、请求体或响应内容，不新增网络请求，也不保存当次 req 或 Token 到文件。

首个日志版直接 CLI 验证返回 `P1_`，退出码 0，Token 长度 2475，耗时 6.71 秒。标准错误中的 9 条日志覆盖开始、资源、配置、req、校验和通过状态；标准输出仍只有完整 Token。脱敏验证结果和该版源码哈希保存在 [logging-verification.json](logging-verification.json)，未覆盖初始五次验收记录。

随后参考其他案例改用 Loguru 的 `HH:mm:ss | LEVEL | message` 格式：过程使用 INFO，成功使用 SUCCESS，CLI 异常使用 ERROR。移除了自定义中文分类前缀，关键参数保持输出。离线检查确认 INFO 和 SUCCESS 格式及传输层返回的响应对象身份；此项检查使用本地替身数据，不计入线上通过样本，也没有发起线上请求。记录和当前源码哈希见 [log-format-verification.json](log-format-verification.json)。

## 提交前验收

当前 INFO/SUCCESS 版本的 SHA-256 为 `340eedc003a6cd9e25caf9920a0f383b8f09bb39d62a7e2685ae9f313548230e`。提交前两次独立 CLI 验证均使用本机 6696 代理，未使用浏览器产物：

| 次数 | 结果 | 退出码 | Token 字符数 | 耗时秒 |
| --- | --- | ---: | ---: | ---: |
| 1 | `network-error` | 1 | 0 | 9.55 |
| 2 | `P1_` | 0 | 2476 | 10.36 |

第一次未出现成功的 getcaptcha 日志；第二次观察到 getcaptcha HTTP 200、8 条 INFO 和 1 条 SUCCESS。第一次的记录不足以确定网络或服务端失败的具体根因，两次样本也不构成稳定性证明。失败结果保留在 [acceptance.json](acceptance.json)，与历史成功记录分开。

本次重跑音频与字体回归，43 passed。框架源码仅修改 FontFace 的错误接收者处理；AES-GCM 仍是案例内的接口适配，音频仅构建现有扩展，不将其描述为新实现的框架算法，也不提交二进制、HAR 或 SDK 快照。

完整策略检查六道门 6/6 通过：架构分层、原生目录布局、实现与测试配对、启动预算、无原生扩展运行及重复语料检查。启动中位数 399.3 ms，预算 834.5 ms；无原生扩展的 unit 检查为 378 passed、41 skipped、2 deselected。脱敏汇总一并写入 acceptance.json。
