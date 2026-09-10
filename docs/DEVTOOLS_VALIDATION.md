# DevTools 重构验证记录

日期：2026-09-10。工作区：`D:\src\pyv8env\_pyv8` 和 `D:\src\pyv8env\pyv8c`。
没有创建 Git commit，没有修改 IndexedDB、trace 或其他原有无关工作区改动。

## 基线与最终结果

重构前基线包含上一轮“编译后首句暂停”修补；这不是干净 HEAD 的测试结果。
该补丁解决的无 debugger 首语句场景继续保留在原生和真实 CDP 测试中。
旧 API 的测试按本次允许的破坏性迁移更新，因此测试数量差值不等于净新增场景数。

| 验证 | 重构前基线 | 最终结果 |
| --- | --- | --- |
| `_pyv8` 全量，Python 3.12.9 | 240 passed，1 failed | 258 passed，1 failed |
| `pyv8c` 全量，Python 3.12.9 | 360 passed | 370 passed |
| 原生/核心/独立 Context 专项，Python 3.10.11 | — | 23 passed |
| Browser 真实 CDP 专项，Python 3.10.11 | — | 20 passed |
| Browser/CDP、race regressions、lifecycle stress，Python 3.12.9 | — | 37 passed |
| Browser 真实 CDP 连续重复 10 次 | — | 200 passed |

唯一原生全量失败保持不变：
`tests/test_trace_passive.py::test_observation_never_reads_or_stringifies_js_objects[default]`。
测试期待 caplog 捕获 12 条记录，而默认 sink 使用 loguru，caplog.records 为 0；stderr 有对应日志。
本轮不修改该 trace 实现或测试。
Python 3.10 的 Browser 测试有一条配置警告：该环境未安装 pytest-timeout，无法识别 timeout 配置项；测试本身全部通过。

## 已覆盖的关键场景

- 无 debugger 首句暂停、仅一次、禁用暂停、自定义 reason、取消、编译失败重试、预编译 Script。
- Context 关闭顺序、多 Context 共享 Isolate、多原生 session、断点配置相互隔离。
- 原生忙 Isolate 非阻塞进入及释放通知、暂停时跨目标处理命令、嵌套邮箱暂停。
- Console 不消耗首句暂停、URL 断点、单步及立即恢复、同编号 session 请求归属。
- HTTP 三个发现路径、目标级 WebSocket、第二客户端拒绝、端口和线程释放。
- 既有 iframe 自动附加、新 iframe/Worker 启动前登记和等待、父页面在子目标等待/暂停中关闭。
- 就绪、宿主暂停及未完成 Console 请求中断线，快速重连，旧响应/旧队列不进入新连接。
- 显式启动超时不执行脚本、不关闭页面，关闭唤醒两个 wait，重复关闭。
- 原生回调内直接 disconnect/Context.close 被明确拒绝，避免销毁仍在执行栈上的 Inspector。

## Windows 扩展

实际构建命令，在 `_pyv8` 仓运行：

```powershell
& D:/scoop/apps/python310/current/python.exe setup.py build_ext --inplace
```

使用 Python 3.10.11 头文件、MSVC 14.44.35207、项目 V8 15.2.20.1。
最终构建不使用 PYV8_ALLOW_NEWER_ABI3_BUILD；再次运行确认 BUILD_EXIT_CODE=0。
`pyv8/_pyv8.pyd` 与 `build/lib.win-amd64-cpython-310/pyv8/_pyv8.pyd` SHA256 相同：

```text
882746D29D0FA0DCB5BFFA5684695C4B08E45749BCDC1E7C0F2A936F9D1797DD
```

这是同步到工作区并经 Python 3.10/3.12 验证的原生扩展，不是已发布的正式 abi3 wheel；
本轮未执行发布流程或完整 Python 3.10–3.14 wheel 矩阵。

## 真实 Chrome 前端

本机 Chrome 152.0.7977.76，通过 `tools/devtools_chrome_smoke.py` 自动控制真实浏览器和真实 DevTools 前端。
读取本服务 `/json/list` 后，验证前端完成握手、example.js 首条语句暂停、恢复结果为 42、
Worker 前端模型显示暂停，以及关闭前端后重连时主目标和 Worker 都存在且不重复首句暂停。

这一验证发现旧 URL 中 v8only=true 会使前端按 Node 模式运行，不自动附加 Worker；
现已删除该标记，使用支持子目标附加的普通 Inspector 前端。

验证工具通过 Chrome 自己的远程调试端口提供的 `/devtools/inspector.html` 加载同一 bundled 前端，
保留本服务的 WebSocket 地址。直接用 Target.createTarget 导航 devtools:// 地址在本机返回 ERR_INVALID_URL，
所以没有把这种程序化导航失败当作服务握手失败。
**尚未进行人在地址栏粘贴 handle.url、点击 Sources/断点的交互式验收**；自动前端验证不能替代这一步。
工具使用单独临时 profile，关闭并删除的只有该工具创建的浏览器实例和临时目录。

## 重现

在各仓运行测试前设置本地导入路径：

```powershell
$env:PYTHONPATH='D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
```

`_pyv8`：`python -m pytest -q --timeout=30`。
`pyv8c`：`python -m pytest -q --timeout=30`。
重复 CDP：`python -m pytest tests/browser/test_devtools.py -q --timeout=20 --count=10`。
Chrome：`python tools/devtools_chrome_smoke.py`。
两仓 `git diff --check` 均通过。
