# hCaptcha 本地执行报告

验证日期：2026-11-13，Asia/Shanghai。目标页面为 `https://dashboard.hcaptcha.com/login`，入口为 [hcaptcha.py](hcaptcha.py)。

当前入口通过 pyv8c 的本地 V8、DOM、iframe 和 Web API 执行当次下载的原始 hCaptcha SDK，由 SDK 获取配置、调用 HSW 和提交校验。初始交付版在本机 `http://127.0.0.1:6696` 代理下，连续五个独立新会话均返回 `P1_` 开头的 Token；另一次直接 CLI 调用退出码为 0。之后增加了下述控制台日志。

提交前对当前 INFO/SUCCESS 版本做了两次独立 CLI 复测：第一次返回 `network-error`，第二次返回长度 2476 的 `P1_`，退出码为 0，耗时 10.36 秒。当前复测为 1/2 通过，不能把历史五次成功解释为稳定保证；记录见 [acceptance.json](acceptance.json)。

提交检查另包含 43 项音频与字体回归，以及策略六道门 6/6 通过；无原生扩展的 unit 检查为 378 passed、41 skipped、2 deselected。

运行时不启动浏览器，不读取 HAR、保存的 SDK、浏览器 Cookie、指纹或历史 Token。浏览器及 HAR 只用于分析。这里的本地执行仍需联网下载 SDK 和完成服务端校验，也没有把 HSW 的内部算法重写为纯 Python。

## 运行方式

在工作区 `G:\pyv8env` 执行：

```powershell
.\.venv\Scripts\python.exe -B pyv8c\examples\hcaptcha\hcaptcha.py
```

成功时向标准输出打印完整 Token，退出码为 0。过程日志向标准错误输出，包括 sitekey、api.js 和 hsw.js 地址及字节数、iframe 资源、配置版本 v、pass、挑战类型、当次完整 req、getcaptcha 状态与请求响应字节数，以及最终 Token 长度。失败时向标准错误输出原因，退出码为 1。程序本身不保存源码、Token 或请求记录。

当前采用与其他案例一致的 Loguru 格式 `HH:mm:ss | LEVEL | message`，过程用 `INFO`，取得 Token 用 `SUCCESS`，CLI 失败用 `ERROR`；已移除方括号中的自定义中文分类。

首个日志版独立 CLI 复测通过：退出码 0，返回长度 2475 的 `P1_` Token，耗时 6.71 秒，9 条过程日志均包含预期字段。摘要及该版源码哈希见 [logging-verification.json](logging-verification.json)。随后统一 INFO 和 SUCCESS 样式，仅做离线格式检查，记录及当前源码哈希见 [log-format-verification.json](log-format-verification.json)。

也可以从案例目录调用：

```python
from hcaptcha import run

token = run(timeout=60, proxy="http://127.0.0.1:6696")
assert token.startswith("P1_")
```

`proxy=None` 表示直连；本报告的五次最终验收使用上述代理，不能将其结果直接套用于直连。`timeout` 是页面结果等待时限，HTTP 单次请求超时配置为 20 秒，并非整个函数的硬退出时限。

依赖为当前仓库的 pyv8c、匹配的 `_pyv8` 原生扩展、`_audio_native` 音频扩展，以及 PyCryptodome。当前环境已经补齐音频扩展。迁移环境时应先按[音频构建说明](../../pyv8c/audio_native/README.md)准备该扩展。

## 请求链与参数来源

```text
本地最小页面，origin 为 dashboard.hcaptcha.com
  → 当前 /1/api.js
  → SDK 创建 hcaptcha.html iframe
  → checksiteconfig 返回当次 c.req 等配置
  → SDK 按配置选择并加载当前 hsw.js
  → 本地执行 HSW 与其中的 WASM
  → SDK 生成二进制请求体并 POST getcaptcha
  → SDK 处理及解密响应
  → hcaptcha.execute 的 Promise 返回 Token
  → Python 检查 P1_ 前缀并返回
```

`SITEKEY` 是应用公开配置中的 `userAccessSitekey`，属于案例的静态配置。`c.req`、SDK 版本路径、HSW 地址、运算输入和最终 Token 均由当次线上配置及原始 SDK 取得或生成，入口没有固定历史挑战参数、HSW 内容或成功响应。

HAR 中的 `getcaptcha` 请求体和响应均为二进制内容；因此交付没有自己拼接历史请求或仅按明文 JSON 判断结果，而是让原始 SDK 完成整条处理链。

## 请求范围

资源拦截器只允许以下 HTTP 或 HTTPS 请求，其余返回框架的本地空响应。`blob:`、`data:` 等非 HTTP 资源仍交给框架正常处理。

| 主机 | 允许的路径 | 用途 |
| --- | --- | --- |
| `js.hcaptcha.com` | `/1/api.js` | 当前 SDK 入口 |
| `newassets.hcaptcha.com` | 以 `/hcaptcha.html` 或 `/hsw.js` 结尾 | 验证 iframe 与 HSW |
| `api.hcaptcha.com` | `/checksiteconfig`、`/getcaptcha/…` | 配置与校验，包括相应预检 |
| `*.w.hcaptcha.com` | `/logo.png` | SDK 发出的网络探测请求 |

入口使用最小页面，不加载控制台应用、登录接口、展示图片、字体或业务统计。出现额外挑战时，不扩展为点选求解，也不启动浏览器回退。

## 适配与框架修复

### AES GCM 运算适配

框架原有 `SubtleCrypto.encrypt` 和 `decrypt` 尚未实现 AES-GCM。案例通过 `interface_overrides` 为本 Browser 补充 PyCryptodome 运算，保留当次密钥、IV、可选附加数据和认证标签，并在失败时拒绝 Promise。此适配位于案例文件，没有修改原始 SDK，也没有在通用框架中宣称完整实现 Web Crypto。

已检查 NIST 已知答案向量、加解密往返、ArrayBuffer 返回类型、DataView 的字节范围及偏移，以及篡改密文触发 `OperationError`。

### 音频扩展构建

本机原先缺少 `pyv8c._audio_native`，SDK 创建动态压缩节点时发生宿主导入错误。使用仓库现有 Rust 源码及锁文件，以本机已有 Rust 工具链构建扩展，并放入当前源码包。音频算法源码没有修改，相关 30 项回归通过。

### FontFace 接收者行为修复

SDK 对错误接收者调用 `FontFace.prototype.load` 时，浏览器返回一个以 `TypeError` 拒绝的 Promise；框架原先直接读取接收者的 `_status`，导致 Python `AttributeError`。已在 [fonts.py](../../pyv8c/pyv8c/web/dom/fonts.py) 补充接收者检查和 Promise 拒绝，并在[字体测试](../../pyv8c/tests/browser/test_font_faces.py)覆盖 `window`、普通对象及 `null`。字体模块共 13 项回归通过。

## 验收结果

以下为增加日志前的交付入口的五次调用，每次均新建 Browser、Profile 和 Page。Token 仅保存前缀、长度及 SHA-256，完整值不写入报告。

| 次数 | 结果 | Token 字符数 | 耗时秒 |
| --- | --- | ---: | ---: |
| 1 | `P1_` | 2434 | 19.35 |
| 2 | `P1_` | 2427 | 7.49 |
| 3 | `P1_` | 2450 | 6.29 |
| 4 | `P1_` | 2475 | 5.60 |
| 5 | `P1_` | 2484 | 5.60 |

详细摘要见 [verification.json](verification.json)。其入口文件 SHA-256 为 `dfbfafa70d0234eba8f87953bfa56603b5693ad5321a37e49912f8e3d089c698`。

直接执行交付 CLI 的独立检查也通过：退出码 0，Token 长度 2482，耗时 7.79 秒，标准错误为空；见 [cli-verification.json](cli-verification.json)。这次 CLI 检查独立于上表五次调用。

验证标准是当次原始 SDK 返回非空 `P1_` Token，HTTP 200 本身不足以判定通过。该结果没有进一步用于账号登录或其他业务接口，也不代表所有网络环境或后续 SDK 版本均能通过。

## 分析记录与已知限制

完整排查过程见 [PROCESS.md](PROCESS.md)。原始 SDK 快照、网络摘要、WASM 接口信息及本地探针日志保存在工作区 `artifacts/hcaptcha-20261113`；这些分析工件仅在本机保存，未随仓库提交，交付入口不会读取该目录。

前期存在直连超时、额外挑战和一次收敛阶段的 `network-error`；最终五次成功不能证明这些问题以后不会出现。HSW 地址由当次配置选择，不应仅按固定更新时间或保存的文件名判断版本。应用 sitekey、资源路径或 SDK 契约变化时，需要重新核对配置和请求白名单。

框架的 AES-GCM 能力仍由此案例适配提供；通用的其他加密算法、完整 Web Crypto 兼容性和图形挑战处理不属于本次交付范围。
