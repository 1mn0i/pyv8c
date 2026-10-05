# 探针与骨架

## 0. 加一个探针（最常用）

探针 = 一个 JavaScript 表达式，返回 JSON 安全的数据。加进 `scripts/probes.py` 的 `PROBES`：

```python
SOMEPAGE = """<!doctype html><html><body>…</body></html>"""

PROBES = {
    # … 已有的 …
    "myname": {
        "note": "一句话：这个探针回答什么",
        "fixture": (SOMEPAGE, 0.6),      # 有 fixture 就能离线跑；不需要站点
        "probe": _probe("""
  var out = {};
  // _probe() 已经把 __view / __tags / __pick（元素读数工具）放进作用域
  out.body = __view(document.body);
  return out;
"""),
    },
}
```

- `_probe(body)` 会把共享的元素读数工具（`__view`、`__tags`、`__pick`）铺在前面，并整体包成
  一个表达式——`page.evaluate` 只接受表达式，不要写两条语句。
- 探针必须**容错**：被页面藏起来的节点上 `getBoundingClientRect()`、`attributes`、`children`
  可能不是对象；所有读取都 `try/catch`，失败给一个字符串标记（`'threw'`/`'missing'`）。
- 探针必须**可复现**：不要用 `Math.random()`、不要依赖当前时间（除 `timing` 那种本来就测时间的）。
- 用 `String()` 收口：pyv8c 会把 JS 的 `null` 交回成 pyv8 的 `Null`，直接 `json.dumps` 会失败。

跑它：`python scripts/harness.py --probe myname`（两引擎）或 `--side here`（只看 pyv8c）。

## 1. 直接对某个站点写临时探针

```python
"""一个页面、一个探针，两侧并排比。"""
import sys
sys.path.insert(0, r"<本 skill>\scripts")
from harness import chrome_path, diff, run_chrome, run_here

URL = "https://example.com/somewhere"
PROBE = """(function () {
  var node = document.querySelector('#app') || document.body;
  var rect = node.getBoundingClientRect() || { width: 0, height: 0 };
  return { box: Math.round(rect.width) + 'x' + Math.round(rect.height),
           children: node.children ? node.children.length : 'no-children',
           style: getComputedStyle(node).display };
})()"""

mine, viewport = run_here(URL, None, PROBE, 20, None)
theirs = run_chrome(URL, None, PROBE, 20, None, viewport)
diff(mine, theirs)
```

`run_here` / `run_chrome` 已经把"同一页面、同一视口、同一等待"处理好了：
先跑 pyv8c 拿到它报的 `innerWidth/innerHeight`，再用同一个视口开 Chrome。

## 2. 注入钩子（在页面脚本之前）

要观察页面自己做了什么，就在页面脚本前插一段。pyv8c 侧用 `resource_interceptor`
换掉主文档正文；Chrome 侧用 `page.add_init_script`。

```python
import curl_cffi.requests
from pyv8c import Browser
from harness import pump

HOOK = r"""
window.__watch = { seen: [] };
(function () {
  var probe = function (where) {
    try {
      var node = document.querySelector('#target');
      window.__watch.seen.push(where + ' ' + (node ? String(node.tagName) : 'absent'));
    } catch (e) { }
  };
  var wrap = function (proto, name) {
    var original = proto && proto[name];
    if (typeof original !== 'function') { return; }
    proto[name] = function () {
      var result = original.apply(this, arguments); probe(name); return result;
    };
  };
  wrap(Node.prototype, 'appendChild');
  wrap(Element.prototype, 'setAttribute');
  setInterval(function () { probe('timer'); }, 500);
})();
"""

done = {"document": False}
def intercept(url, request):
    if done["document"] or request.get("destination") != "document":
        return None
    done["document"] = True
    answer = curl_cffi.requests.get(str(url), impersonate="chrome")   # 以 Chrome 指纹取原文
    html = answer.text.replace("<head>", "<head><script>" + HOOK + "</script>", 1)
    return {"status": answer.status_code, "body": html, "url": str(url),
            "headers": dict(answer.headers)}

browser = Browser(stub_logging="off", resource_interceptor=intercept, threaded_loaders=True)
page = browser.new_context().new_page()
page.goto(URL)
pump(page, 30)
print(page.evaluate("({seen: window.__watch.seen})"))
```

钩子必须**原样转发返回值**，钩子内的异常一律吞掉，否则会把页面弄坏。

## 3. 引擎侧追踪（pyv8c 独有）

pyv8c 是 Python：直接包住内部方法就能看到"引擎以为发生了什么"。

```python
from pyv8c.platform.sessions import BindingSession      # 接口物化
from pyv8c.platform.resource_loading import ResourceController  # 资源与 iframe 上下文

log = []
for attribute in ("materialize", "_stage"):
    original = getattr(BindingSession, attribute)
    def traced(self, name, _original=original, _attribute=attribute):
        log.append((_attribute, str(name)[:40]))
        return _original(self, name)
    setattr(BindingSession, attribute, traced)
```

常用观察点：

| 想回答 | 包住 |
| --- | --- |
| 哪个资源被取、有没有用上 | `ResourceController.discover_element_resource`、`complete_document_resource` |
| iframe 有没有自己的浏览上下文 | `ensure_iframe_context`、`discard_iframe_context` |
| 哪个接口在哪次调用里被物化 | `BindingSession.materialize` / `_stage` / `class_config_for` |
| 某个节点在宿主侧到底是什么状态 | 直接读 Python 对象：`node.attributes`、`node.parentNode`、`node.ownerDocument`、`node._child_services` |

包法必须 `setattr` 成方法并用默认参数捕获 `original`（闭包陷阱）。

## 4. 读 `--trace` 日志

`python px3.py --trace`（案例里的 runner）或自己开 `trace=True` 会把每个 IDL 调用记进日志：
`[TOP] <getter|setter|method|descriptor|has>: Interface.member(args) -> value`。

- `待实现` 命中 = 页面撞到了未实现的成员（**这是最直接的"缺实现"证据**）；
- `has:` 表示页面在做特性探测（`'x' in y`），**不是调用**，别当成调用；
- `descriptor: … -> False` 表示宿主没有该自有属性；
- 用"某成员最后一次成功调用前后 N 行"来定位"何时开始出错"，比通读日志有效得多。

## 5. 网络/载荷对照（站点有 API 时）

先比**请求集合与顺序**，再比**体量与共同前缀**，最后才逐字段。两条经验：

- 体量差异要先排除"等待时长/采样次数不同"——同一条件各跑两次，看是否稳定；
- 载荷往往是站点自己的编码。定位编码器最省事的路径是**在页面里钩住发送前的明文**
  （钩 `JSON.stringify` / `Array.prototype.join` / `XMLHttpRequest.send` 并留一份快照），
  而不是去读混淆后的源码。

## 6. 结论模板（写进记录）

```markdown
### 症状短语

症状：一句话 + 两侧数字（哪条命令得到的）。

根因：哪一层、哪个函数、哪一行；用一句话说清机制。

修法：改了什么（文件 + 一句话），新加的常量/表放哪。

验证：最小探针两侧对齐的数字；真站上随之变化的量；回归结果。
```

## 7. 已知的坑

1. **页面忙时不能阻塞等待**：`run_until_idle(timeout=N)` 抛 `RealmBusyError`，用 `pump()`。
2. **无头 Chrome 不是参照**：UA、可见性、指纹项都不同。
3. **采样时间不同不是差异**：控件几何、载荷长度会随时间变，先在同条件下各跑两次。
4. **页面会隐藏自己的东西**：读不出来先做两侧同样的观测；`Chrome 也一样` 就不是缺陷。
5. **`evaluate` 只接受表达式**；**`Null` 不能直接 JSON 序列化**。
6. **资源 kind 要对齐**：登记资源用的 `kind` 与消费处认的 `kind` 必须一致
   （`<link rel=stylesheet>` 的 CSS 曾因两边不一致而从未被保存）。
