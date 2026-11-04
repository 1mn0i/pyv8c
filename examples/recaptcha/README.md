# Check-in Pax / reCAPTCHA

入口是 `recaptcha.py`。它使用 pyv8c 的本地 V8、DOM 和 Worker 执行当次原始 Google SDK，生成验证请求，再将当次 Google `rresp` 返回的 `reCaptchaToken` 提交到登录接口。

已实测得到 HTTP 200，响应为：

```json
{"success":false,"data":{"error":"Unauthorized"},"message":"Invalid email or password."}
```

这是案例的成功条件：验证通过后，专用无效凭据触发账号密码错误。脚本不会尝试登录真实账号。

## 运行

在 `pyv8c` 仓库根目录运行，使用已安装本项目及相邻 `_pyv8` 的环境：

```powershell
..\.venv\Scripts\python.exe -B examples\recaptcha\recaptcha.py --proxy http://127.0.0.1:6696
```

需要原生 `pyv8`、本仓库 `pyv8c`、`curl_cffi` 和 `loguru`。可用环境变量 `RECAPTCHA_PROXY` 代替参数；未配置代理时直连。默认总流程预算 90 秒，`--timeout` 可调整；CLI 额外限制退出和清理时间。

程序只向终端打印以下内容，不提供文件保存功能：

- 基本请求状态：方法、主机、路径、HTTP 状态码；请求失败时显示重试情况。
- 完整的 `reCaptchaToken`。
- 登录接口的原始响应。
- 验收结果 `verified=True/False` 和耗时。

`verified=True` 且退出码为 0，表示登录响应状态码为 200，且消息精确等于 `Invalid email or password.`。响应内部的 `success: false` 是预期的业务登录失败。

程序不创建输出目录，不保存抓包、脚本、Token 或登录响应，已移除 `--output-dir` 参数。运行不读取 HAR、原有 `check_in.py` 或 `.analysis` 中的文件，也不调用 MCP、Chrome、Playwright、Camoufox。分析用浏览器生成的 Token、Cookie 和指纹采样不进入交付程序。

## 请求与参数来源

1. GET 首页，要求 HTTP 200；读取当前应用脚本，从中取得公开 site key。
2. 在应用 origin 下加载当次 `api.js`、其指定版本的 SDK，以及当前 anchor HTML。
3. 调用原应用相同的 `grecaptcha.execute(siteKey, {action: "submit"})`。iframe 和 Worker 在本地执行，SDK 自己生成 reload 的二进制请求体；不拼接抓包中的旧环境字段。
4. Google `/recaptcha/api2/reload` 的反 XSSI JSON 中，`rresp[1]` 是 Token。脚本核对 execute 结果确实来自本次成功 reload；如果 SDK 只返回错误回退字符串，立即失败。
5. 将 Token 与固定无效测试凭据提交到 `https://api.checkinpax.com/api/login`，检查实际响应。

公开静态资源只在当次运行的内存里复用。动态 anchor、reload 和登录响应不做历史缓存；POST 不自动重试。SDK 版本或服务端策略变化可能导致验证失败，脚本会保留失败状态，不把 HTTP 200 或任意 Token 字符串当作成功。

## 本案例暴露的框架差异

- **Worker `importScripts` 原先只有占位函数。** 最小用例中 Chrome 观察到 `start, imported, after`，本地原先只有 `start, after`。现已支持在 Worker 中同步导入、按入口 URL 解析相对地址，并传播加载和脚本异常。
- **CSS 声明按分号拆分，截断 data URL。** `.rc-anchor-pt` 的背景图原先只读到 `url("data:image/png`，SDK 正则解析失败。现在分隔符识别避开字符串、转义和函数内部，保留完整 data URL；对应语料与 Chrome 一致。
- **`Document.hasStorageAccess()` 原先返回 undefined。** SDK 对其调用 `.then`，导致 iframe 返回 TypeError，顶层最终生成错误回退 Token。现在返回 Promise，查询默认无授权的存储访问状态；别名和非活动文档拒绝行为也有语料覆盖。算法依据 [Storage Access API 草案](https://privacycg.github.io/storage-access/#dom-document-hasstorageaccess)。当前沿用框架网络层已有的同站判断，其注册域计算未采用完整公共后缀表。
- **fetch / XHR 二进制请求体未在原 realm 中取快照。** `Uint8Array` 直接到达加载线程，无法作为协议字节发送。现于调用时复制 BufferSource 的可见字节，保留 DataView、子视图偏移；原数组之后的修改不影响请求体。reload 已实际发送并取得有效响应。

回归位于 `tests/repros/corpus.py`、`tests/browser/test_worker_import_scripts.py` 和 `tests/browser/test_fetch_binary_body.py`。分析阶段曾出现 3130/3160 字符的错误回退字符串，其登录响应为 reCAPTCHA 校验失败；这些不是有效 Token，也不作为交付样本。

## 验证范围

- 独立 CLI 两次实测：`verified: true`、退出码 0，耗时 15.92 秒 / 23.97 秒；每次都重新读取应用配置、下载 SDK、生成验证参数和请求登录。两次均得到上述预期响应。
- 本次实测 SDK release：`4EDjxXf4knsx6UBM1L2V6cv8`，原始 JS SHA-256 为 `c9204b3e785344cc4b448daa9e44d42c9b3db1ecf11c06fa3e6065048848b465`。入口没有写死这个版本或散列。
- 专项串行回归：11 passed。包含三个 Chrome 对照语料的两侧运行，以及 Worker 相对导入、HTTP 失败、module Worker 拒绝和 fetch / XHR 的字节快照。
- `python -m pytest tests -q -n auto --ignore=tests/repros -p no:randomly`：1267 passed、1 skipped、2 failed。失败分别是 WGSL 特性集合读数，以及当前 Python 环境未安装可选 `cycronet`。
- Chrome 对照按文件独立串行运行：合计 609 passed、10 skipped、7 failed。主语料失败涉及 Client Hints、WebGL 清屏像素和 Permissions Policy；其余失败涉及请求头与 Navigator 成员。当前本机 Chrome 为 155，仓库默认基线为 154。测试清理另有 Playwright / greenlet 线程警告；同进程连跑所有模块会使后续 Sync API 夹具受到事件循环残留影响。
- `python tools/check_policy.py`：5/5 通过；`git diff --check` 通过。

本案例已达到目标接口验收条件，但没有将整个仓库的上述测试差异标记为已解决。未修改相邻 `_pyv8`，未提交或推送 Git，保留了原有 AKM 修改和 DataDome 样本删除。
