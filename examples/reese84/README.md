# Orion / Reese84

主入口 `reese84.py`，写法参照 `akm.py`：拦截资源 → 本地执行挑战 JS → 等待 Cookie → POST 数据接口。必要的 iframe / JSON null 兼容修正也放在同一个文件中。

在 `G:\project\pyv8env` 运行：

```powershell
.venv\Scripts\python.exe -B pyv8c\examples\reese84\reese84.py --print-details --save-challenge
```

默认搜索 `NORTHWEST`，第一页 25 条。保留原参数 `--keyword`、`--page`、`--page-size`、`--proxy`、`--timeout`、`--output`、`--print-details`、`--save-challenge`；旧入口 `collect.py` 的命令仍可使用。

输出沿用原结构：

- `output/result.json`：完整实时接口 JSON。
- `output/printed-results.json`：`--print-details` 时保存并打印完整 `reese84`、`___utmvc` 和接口 JSON。
- `output/challenge-01-request.json`：`--save-challenge` 时保存当次本地计算的 `p` 请求体。
- `output/run-summary.json`、`output/project-manifest.json`：实际请求状态、Cookie 写入证据及版本哈希。

每次创建空会话，只执行实时下发的挑战脚本，不读取 HAR Cookie/token，不依赖浏览器自动化或 MCP。`___utmvc` 可能在信标成功后被删除，打印时保留其实际生成并写入的值。接口只有 HTTP 200、code 0、items 结构和关键词均通过检查才算成功。

`resource_interceptor` 只允许主文档、本站 Reese84 SDK、Incapsula 脚本和校验信标；文档只保留挑战脚本，避免加载聊天、业务包和第三方资源。HAR 中两次挑战 POST 分别来自主页面和聊天 iframe；不加载聊天时通常只需一次。两个数字键属于 JSON `performance` 的 SDK 测时字段。

两处案例修正：同源 iframe 返回原生子窗口 handle，避免定时器的错误接收者；Response.json 将 JSON null 转为 pyv8.Null，保证 cookieDomain 的语义。框架源码没有修改。

排查脚本及旧实现归档在 `analysis/`，运行入口不依赖它们。离线回归检查：

```powershell
.venv\Scripts\python.exe -B pyv8c\examples\reese84\analysis\verify_local.py
```

`analysis/`、`output/`、`output-*/` 和 HAR 已由本目录 .gitignore 排除。
