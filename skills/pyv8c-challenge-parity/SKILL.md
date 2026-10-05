---
name: pyv8c-challenge-parity
description: This skill should be used when pyv8c has to behave like a real browser on a live site — picking a site that probes browser details (bot walls, captcha gates, fingerprint checks, JS-heavy apps), running it in pyv8c next to a real headed Chrome, and turning every difference into either a fixed engine defect or a recorded, re-probeable fact. It covers choosing targets, the two-engine harness shipped here, the probe library, how to tell a pyv8c gap from the page's own behaviour, and where in the engine to fix what. Triggers include "pyv8c 跑某个站点结果不对", "pyv8c 和 Chrome 不一样", "挑战/验证码被拦", "对照 Chrome 找差异", "用真站来完善 pyv8c", "challenge page parity", "为什么 pyv8c 拿不到那个值", and any task that drives a real site with pyv8c and expects a browser's behaviour.
---

# 用真站挑战 pyv8c

## 这个 skill 做什么

pyv8c 是运行在原生 `pyv8` 扩展之上的、可控可测的 Chromium-like 页面环境（V8 执行 JS，
Python 实现页面、DOM/BOM、资源、事件循环等）。它**不承诺完整 Chrome 兼容**，所以最好的改进
方式不是读规范，而是**拿会挑刺的真站当靶子**：那些专门探测浏览器细节的站点（bot wall、
验证码门、指纹检测、重度前端应用）会把 pyv8c 与真 Chrome 的差异直接放大成可观测的现象。

做法固定：**同一站点、同一条件、两侧跑同一件事 → 用数字对齐 → 把差异压成最小复现 →
定性（页面还是 pyv8c）→ 处置（修 / 绕 / 记）→ 把探针留下当回归**。

这个 skill 自带工具，站点无关：
`scripts/harness.py`（两引擎并排跑任意页面 + 任意探针）、`scripts/probes.py`（探针库）。
一轮下来，被验证过的行为就变成了以后可以离线复查的探针。

## 先决条件

- **pyv8c**：按仓库 `README.md` 装好（`pip install -e .`）时直接 `import pyv8c`；在开发树里跑
  则要把 `pyv8c` 源码目录**与相邻的原生扩展 `_pyv8` 目录**都放进 `PYTHONPATH`
  （仓库布局是 `<parent>/pyv8c` 与 `<parent>/_pyv8`）。
- **参照浏览器**：一律**正常模式（headed）Chrome**。无头 Chrome 是另一个页面（UA 带
  `HeadlessChrome`，可见性与若干指纹项都不同），拿它对照会得出错误结论。
  `harness.py` 会自动找本机 Chrome（`CHROME_PATH` 优先），找不到会退到 Playwright 自带的
  chromium 并**明确警告**——那时结论要打折。
- **Playwright**（对照时用）：`pip install playwright`，用它驱动 Chrome 即可，不必 `playwright install`。

## 硬性规则

1. **参照必须是 headed Chrome，两侧条件必须一致。** `harness.py` 已经处理了最要紧的一条：
   先跑 pyv8c，读它报的 `innerWidth/innerHeight`，再用同一个视口开 Chrome。
   视口不一致会让所有布局读数错位。
2. **先压成最小复现，再谈结论。** 真站上的现象要能在几十行页面里重现，才算真的定位了；
   把探针写进 `probes.py` 就顺带完成了这件事。
3. **结论必须有数字。** 记"哪条命令、两侧各是多少"，不要记"感觉不一样"。
4. **两侧都要观测，再判断是谁的问题。** 站点会主动反探测：把节点换成"读什么都 `undefined`"
   的包装、隐藏自己的 iframe、对同一接口给不同答案。同一现象在 Chrome 里也出现，就不是 pyv8c 的
   缺陷，而是页面行为——照第 6 节处理。
5. **改引擎才需要跑回归**：`pytest tests -q --ignore=tests/repros`，再 `pytest tests/repros -q`。
   `tests/repros/test_navigator_members_vs_chrome.py` 的 `mediaDevices` 是**已知**差异；
   devtools 与个别 frame 用例会抖，隔离跑一遍再判断是不是自己引入的。
6. **不要只看父文档。** 子框架/跨源/影子树里的内容父文档看不到；判断"页面到底渲染了什么"要看
   浏览器自己的 frame 列表，或从 pyv8c 引擎侧读子文档。

## 流程

1. **选靶**：按 `references/targets.md` 挑一个会探测浏览器细节的站点，并定好"它看起来正常"
   的判据（页面出现什么、跳到哪里、拿到什么值）。
2. **跑起来**：`python scripts/harness.py --url <url> --probe all --seconds 20`。
   探针里凡是有 fixture 的先跑一遍，确认工具链是通的：`--probe ua --probe rect --probe style`。
3. **找差异**：同一个 URL 上跑 `layout`、`style`、`globals` 这些"需要 --url"的探针；
   `harness.py` 逐字段列出不一致。需要盯某个具体节点/时序时，照
   `references/probe-cookbook.md` 写一个临时探针。
4. **压成最小复现**：把现象写成一个页面 + 一个探针，加进 `scripts/probes.py` 的 `PROBES`
   （带 `fixture`），从此不再依赖那个站点。
5. **定性**：是 pyv8c 缺实现 / 语义不同，还是页面行为 / 你的用法？
   缺实现的特征是 `--trace` 日志里出现 `待实现`，或该方法返回 `undefined`。
6. **处置**：
   - **页面行为** → 记进 `references/findings.md`（"已排除"那一节），改自己的代码或绕开；
   - **pyv8c 的差异，而你在改引擎** → 按下表定位、改掉、用同一个探针验证两侧对齐、跑回归；
   - **pyv8c 的差异，而你只是使用者** → 留下最小复现 + 数字，反馈给 pyv8c。
7. **记录**：症状、根因、修法/绕法、证据数字、涉及文件。仓库里的完整案例见
   `references/worked-example-px3.md`，新结论照它的格式追加。

## 症状 → 代码位置（改引擎时）

| 症状 | 先看 |
| --- | --- |
| 盒子尺寸/位置不对、替换元素没有 300×150、行内元素基线怪、UA 默认值缺失 | `pyv8c/web/dom/layout.py`（`_place`、`_parse_style_elements`）、`pyv8c/web/dom/css.py`（`TAG_DISPLAY`、`TAG_BOX_INITIALS`、`initial_values_for`） |
| 级联/样式表不生效、`cssRules` 为空、`getComputedStyle` 给出空串 | `pyv8c/web/dom/css.py`、`pyv8c/web/dom/layout.py`（`document_rules`） |
| 资源没被取、取了却没用上（`<link>`、`<img>`、`<script>`） | `pyv8c/platform/resource_loading.py`（登记资源时的 `kind` 与消费处认的 `kind` 必须一致） |
| 接口成员读不到、原型/`instanceof` 不对、返回类型不对（如 `DOMRect`） | `pyv8c/web/dom/` 对应模块 + `pyv8c/web/features/{catalog,core}.py` 注册 + `pyv8c/platform/sessions.py` |
| 时间戳粒度、单调性、`Date` 与 `performance` 的关系 | `pyv8c/foundation/clock.py` |
| 定时器/rAF 节奏、任务顺序 | `pyv8c/web/bom/scheduling.py`、`pyv8c/foundation/` |
| 事件监听顺序、`once`/`capture`/`preventDefault` 语义 | `pyv8c/web/dom/events.py` |
| 页面报错、或日志里出现 `待实现` | 先看日志里的命中，再决定实现谁 |

## pyv8c 用法要点（都被踩过）

- `Browser(stub_logging="off", threaded_loaders=True)` 走真实网络；离线对照用
  `real_network=False` + `resource_loader=lambda url, request: {"body": …, "headers": …}`。
- **页面忙时不要用阻塞等待**：长任务/挑战跑起来后页面不会空闲，
  `run_until_idle(timeout=N)` 会抛 `RealmBusyError`。用非阻塞泵 + 截止时间
  （`harness.py` 的 `pump()`）：
  `while 未超时: try: page.run_until_idle(timeout=0) except: pass; sleep(0.2)`。
- `page.evaluate()` 只接受**表达式**：`(function () { … return x; })()`；两条语句会报错。
- pyv8c 把 JS 的 `null` 交回成 `pyv8` 的 `Null`、`undefined` 交回成 `Undefined`，
  直接 `json.dumps` 会抛 `Object of type NullType is not JSON serializable`——探针里先 `String()`。
- 探针要容错：被页面藏起来的节点上 `getBoundingClientRect()` / `attributes` / `children`
  可能不是对象（两个引擎都一样）。
- pyv8c 是 Python，可以直接包住它的内部方法观察（资源加载、iframe 上下文、接口物化），
  这是它比"只有浏览器"更好用的地方；观察点见 `references/workflow.md`。

## 参考

- `references/targets.md`：怎么挑靶子、每个引擎领域拿什么站点/接口压它、检查清单。
- `references/workflow.md`：完整方法——两侧运行器、探针写法、引擎内打点、注入钩子、
  读 `--trace` 日志、真站抓包与载荷对照、回归与验证。
- `references/probe-cookbook.md`：可直接照抄的骨架 + 结论模板 + 已知坑。
- `references/findings.md`：已修的与**已排除的**差异清单——动手前先读，别把排除过的假设重走一遍。
- `references/worked-example-px3.md`：一个完整的实战案例（PX 验证码），含它抓到并修掉的缺陷。
