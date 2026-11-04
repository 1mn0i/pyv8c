# 运行与本地复现

## 前置条件

在 pyv8c 仓库根运行命令，使用项目的 Python 3.12 环境。源码开发时令 PYTHONPATH 指向本仓库中的两个项目。先验证 import 路径，不让已安装旧包替代开发树。

```powershell
$env:PYTHONPATH="$PWD/pyv8c;$PWD/pyv8;$PWD"
python skills/pyv8c-challenge-parity/scripts/harness.py --list
python skills/pyv8c-challenge-parity/scripts/harness.py --help
```

显式选择可读的 IDL。当前用户保留默认 154 压缩包，若其格式与新读取器不匹配，指定已生成的 `idls/145.0.7632.162.zip`，不要修改默认包；以后目录变化应重新检查。

Chrome 必须用 `--chrome-path` 或 `PYV8C_CHROME` 指定现有可执行文件。默认 headed、Playwright 默认参数，不增加隐藏自动化的开关；`--headless` 是另一个需记录的实验条件。若自动化 Chrome 和人工访问本身就不同，记录此边界，用可用的人工 DevTools 证据补充，不冒充普通浏览器会话。

## 在线基线与局部观测

```powershell
python skills/pyv8c-challenge-parity/scripts/harness.py --url https://example.com/ --idl-path idls/145.0.7632.162.zip --chrome-path 'C:/Program Files/Google/Chrome/Application/chrome.exe' --seconds 5 --timeout 45 --out evidence/baseline.json
```

不传 probe/trace：页面脚本执行期间不注入 JS，结束后读取状态和环境。运行器仍有自动化及事件监听影响，绝不是无观察者的人工会话。

第二轮按假设增加 `--probe-file probe.js`、`--wait-for 'Boolean(window.appReady)'` 或 `--trace`。表达式应同步返回可序列化的观测对象；不要返回 Promise（两侧桥接等待语义不相同）、DOM 对象或带副作用的 getter。保存观察者效应；不要默认 `--probe all`。

`--seconds` 是导航返回后的观测/就绪预算；`--timeout` 是每侧进程总预算，含 import、导航、执行和关闭。`--navigation-timeout` 约束 Chrome 导航；pyv8c 阻塞由进程总超时兜底。两侧串行。没有就绪表达式时 `ok` 只代表工具完成，不代表网站成功。

两侧视口配置均为 1280×720，结果另记录实际宽高；布局对照先核对实际值、字体与缩放。不要将设备或字体未匹配的布局结果直接称为缺陷。代理、Cookie、权限等复杂实验需基于现有测试/独立案例程序明确配置，运行器不自动迁移会话。

## 显式离线资源表

创建 `fixture.json`：

```json
{
  "url": "https://local.test/",
  "resources": {
    "https://local.test/": {
      "body": "<!doctype html><script src='/app.js'></script>",
      "headers": {"content-type": "text/html"}
    },
    "https://local.test/app.js": {
      "body": "window.order=['script'];Promise.resolve().then(()=>order.push('microtask'));setTimeout(()=>{order.push('timer');window.ready=true},0)",
      "headers": {"content-type": "application/javascript"}
    }
  },
  "ready": "window.ready===true",
  "probe": "({order:window.order,missing:!('notHere' in window),u:undefined,n:null})"
}
```

```powershell
python skills/pyv8c-challenge-parity/scripts/harness.py --fixture fixture.json --idl-path idls/145.0.7632.162.zip --chrome-path 'C:/Program Files/Google/Chrome/Application/chrome.exe' --out evidence/fixture.json
```

资源键是绝对 HTTP(S) URL，匹配去掉 fragment 后的完整 URL，查询参数不忽略。每项支持 `status`（默认 200）、`headers`、`body` 或相对清单的 UTF-8 `body_file`。资源表按 URL 匹配，不区分请求方法和请求体；需要验证这些条件时使用真实本地服务器。未登记请求明确返回 404；本地 data/blob/about 由各引擎处理，不能统一替换成 HTML。

该工具适合文本 HTTP fixture；不用于二进制媒体、真实 TLS、缓存、Service Worker、WebSocket、Cookie 重定向端到端验收。Chrome fixture 阻止 Service Worker，以避免绕过路由；此条件写入配置。上述能力使用仓库现有本地 HTTP 测试服务器和相关对照测试，保留真实头和状态，不用路由模拟替代。

`--probe events` 等内置 fixture 在不传 `--url` 时运行；传 URL 时对该页面运行探针，不悄悄换成 fixture。旧 `_ask_page/run_here/run_chrome/pump` 导入接口已移除，调用方应迁移到 CLI 或 JSON fixture。仓库正式回归仍使用 `tests/repros` 的现有 harness，不导入 skill 私有工具。

## 结果解释

主结果关联每侧目录中的配置、增量 `result.json`、`events.jsonl`、stdout/stderr。配置保存资源正文；清单来源文件内容已在运行前读入，足以复查同一资源表。保留敏感信息的证据只在本地存放。

| 字段 | 含义 |
| --- | --- |
| `status=ok` | 工具正常完成；有 ready 时要求其达到 |
| `not_ready` | 已观察但未达到就绪条件 |
| `error` | 执行、桥接、关闭或进程监管失败 |
| `timeout` | 总预算耗尽，父进程回收拥有的进程树 |
| `comparison=equal/different` | 仅指该 probe 的类型化观测 |
| `inconclusive` | 至少一侧执行失败，不能宣布一致 |
| `target_not_reached` | 至少一侧未就绪 |
| `not_observed` | 未选择/未完成 probe，不是读取到 undefined |

返回值按 `{type, value}` 递归编码；undefined、null、数组空位 missing、循环引用各有标签。对象中属性是否缺失请显式观测 `in`/`Object.hasOwn`/descriptor：直接读缺失属性本来就是 undefined，工具不能替你推断属性存在性。异常保存类和消息，不将异常等同普通返回值；需比较 DOMException 的 JS name 时在 probe 内显式捕获并返回结构。

CLI 退出码：0 为工具正常且没有已观测差异，1 为失败/未就绪/差异，2 为参数错误。无 probe 的 0 不表示网站验收通过。超时中的未完成阶段从增量结果读取，未出现的阶段属于未观测。

## 资源释放与复现升级

Windows 父进程先将受门控的子进程加入 Job Object，再允许其创建浏览器；超时只清理该 job。正常关闭在创建线程执行。无法建立所有权时失败退出，不降级为按进程名杀 Chrome。正常退出仍有后代需强制回收时报告错误，不伪装释放成功。

进程清理不是句柄/线程无泄漏的证明。涉及生命周期的生产修复，应追加仓库的慢速和竞态测试，观察同进程关闭后的资源计数。不要把 skill 的超时杀进程用作生产修复。
