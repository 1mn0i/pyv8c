# 已知结论（动手前先读）

这里的条目来自 pyv8c 仓库里那轮实战案例（PX 验证码，见 `worked-example-px3.md`
与 `examples/px3/px3.md`），但结论本身是**引擎级**的，适用于任何站点：
**已修好的**不必再查，**已排除的假设**不要再走一遍，**未解决的**是下一步的方向。

## 已修好（都有 Chrome 实测）

| 缺陷 | 之前 → 现在 | 改动位置 |
| --- | --- | --- |
| `requestAnimationFrame` 没按刷新率节流 | 3 秒 183153 帧 → 179~181 帧（Chrome 165~166）；同帧共享时间戳，间隔锁 16.667 ms 网格 | `pyv8c/web/bom/scheduling.py` |
| `performance.now()` 暴露原始精度 | ~0.02 ms → 量化到 100 µs（Chrome 0.1000） | `pyv8c/foundation/clock.py` |
| `element.style` 只枚举 9 个成员 | → Chrome 的 748 个名字、顺序一致、`webkit` 前缀各 151 | `pyv8c/web/dom/css.py` 等 |
| `speechSynthesis.getVoices()` 恒为空 | → 与 Chrome 相同的 3 个语音，先空后到并派发 `voiceschanged` | `pyv8c/web/bom/` |
| 替换元素没有默认尺寸 | iframe/canvas/video/object/svg `0x0` → **300×150**（iframe 含 UA `2px inset` 边框 = 304×154） | `pyv8c/web/dom/layout.py`（`_DEFAULT_OBJECT_SIZE`）、`css.py`（`TAG_BOX_INITIALS`） |
| 行内级空盒被拉成整行宽 | 无 src 的 `img` `1594x0` → `0x0` | `pyv8c/web/dom/layout.py`（`_place`） |
| `getBoundingClientRect()` 返回普通对象 | → 真正的 `DOMRect`（`[object DOMRect]`、`instanceof`、`toJSON`、`Object.keys` 为空） | 新增 `pyv8c/web/dom/rect.py` + `features/{catalog,core}.py` 注册 |
| `p`/`h1`~`h6`/`ul`/`ol`/`dl`/`blockquote`/`figure`/`pre`/`hr`/`fieldset`/`li` 被当成行内 | 全挤在 y=0 一行 → 块级堆叠（`li`/`summary` 为 `list-item`），并补 UA 外边距 | `pyv8c/web/dom/css.py`（`TAG_DISPLAY`、`TAG_BOX_INITIALS`） |
| **链接样式表从不被解析** | `cssRules` 0 → 3；`.px-loading-area` `1888x19` → `310x100`；真站 `#px-captcha` `1888x0` → `381x0` | `pyv8c/platform/resource_loading.py`（`kind in ("style","link")`）、`css.py`（`stylesheet_source`）、`layout.py`（级联用它 + `style_version` 失效） |

## 已排除的假设（不是缺陷，别再查）

1. **控件 iframe "读不出来"**：`tagName`/`attributes`/`parentNode`/`contentDocument` 全
   `undefined`、自有属性 373 个、原型与 `instanceof` 正确——**Chrome 逐字相同**。
   这是 PX 自己把 frame 元素换成隐藏包装（`--trace` 里可见它对每个属性先 `getter` 再
   `descriptor`）。因此：不要在这条线上找"pyv8c 的包装坏了"。
2. 下列最小复现在 pyv8c 里与 Chrome 逐字一致，都不是触发条件：
   - 子文档收养、无 window 的 `createHTMLDocument` 文档收养；
   - 子框架向父文档 append、父节点/父 iframe 被子 realm 读取（含该 realm 随后关闭）；
   - iframe 移出再插回、在类从未 stage 过的 realm 里新建 iframe；
   - 构造/派发各类事件、`postMessage` 与 `MessageChannel`；
   - 模板内容 / `cloneNode` / `importNode` / `Range` 片段里取节点。
3. 接口物化是**惰性**的：页面开始时 configs 里只有基础链，具体元素类要等原生侧来问才 stage
   ——正常机制，不是 bug。
4. `待实现` 命中 0 次：PX 没有调用任何未实现的成员，所以"缺实现"不是它走岔的原因。
5. 视口：pyv8c 报的 `innerWidth 1904×929` 正是环境档案里记的值，不需要改。

## 未解决

1. **行内替换元素的基线偏移**：`python scripts/harness.py --probe rect` 里 `<svg>` 的 y 坐标
   pyv8c 比 Chrome 小 4px（`28` 对 `32`，同为行内上下文、盒子都是 300×150）。
   说明行内级替换元素的下沿/基线对齐还没完全一致，值得单独压一个最小页面。
2. **`getComputedStyle` 不解析 `margin-*`/`border-*`**：`--probe layout` / `--probe style`
   会单列一个 `__computedMargin`，pyv8c 给 `(empty)` 而 Chrome 给 `5px`；
   布局用的值是对的，只有读计算样式的页面会看到差异。
3. **表单控件的 UA 尺寸**：`--probe replaced` 里 `button`/`input`/`select`/`textarea`/`progress`
   的默认盒子与 Chrome 不同（pyv8c `0x0`/`10x19`，Chrome 有真实尺寸如 `177x21`/`161x36`/`160x16`），
   而 `canvas`/`iframe`/`img`/`object`/`embed`/`svg` 已经对齐（`300x150`、`304x154` 等）。
   即替换元素有了默认对象尺寸，表单控件的 UA 样式与内在尺寸还没有。
4. 某挑战页 `#px-captcha` 的高度差：Chrome `468x102`（计算 `height:102px`）、pyv8c `381x0`——
   还有一条给容器定高的规则没生效（复现见 `worked-example-px3.md`）。
5. 载荷里三个稳定缺口字段：一对约 1~2 秒的毫秒数、一个 `webkit` 字符串、一块 896 字符的高熵数据。
6. 某些站点自带的第三方脚本（`crcldu.com/bd/auditor.js` 一类）来源未查。

## 站点级通用知识

- 页面真正在跑（挑战、长任务、动画循环）时**不会空闲**，`run_until_idle(timeout=N)` 抛
  `RealmBusyError`，必须用非阻塞泵（本 skill `scripts/harness.py` 的 `pump()`）。
- 内容可能在**跨源子框架**里，父文档的 `textContent` 永远看不到它；
  判断"页面到底渲染了什么"要看浏览器自己的 frame 列表，或从 pyv8c 引擎侧读子文档。
- 站点会**反探测**：把节点换成读什么都 `undefined` 的包装、延时渲染、按环境分流。
  同一现象先在 Chrome 上做一遍同样的观测，再判断是不是 pyv8c 的问题。
