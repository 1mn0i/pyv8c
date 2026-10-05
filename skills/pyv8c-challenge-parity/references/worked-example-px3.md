# 实战案例：PX 验证码（px3）

一个完整走完的案例，用来示范这套流程能挖到什么。**完整记录与原始数字**在
`examples/px3/px3.md`；案例专用工具在 `examples/px3/script/`（本 skill 的
`scripts/harness.py` 就是从那四个工具里提炼出来的通用版）。

## 靶子

一个 bot wall 的验证码页（`…/sttc/px/captcha-v2/index.html?url=…&uuid=…&vid=…`）。
它的价值在于：整套东西专门探测浏览器细节，而且有明确的成功信号（页面离开 captcha 地址）。
URL 里带一次性 `uuid`，换一次就是一次新挑战。

## 案例的形态（便于对照自己的靶子）

- 页面自身 UI 很简单；需要人操作时在 `#px-captcha` 里放一个无 `src` 的
  `<iframe title="Human verification challenge">`，挑战应用在其中渲染 PRESS & HOLD 按钮。
- 父文档**看不到**这个 frame 的内容：元素对象被站点自己换成一层"读什么都 `undefined`"的包装
  （`getOwnPropertyNames` 有 373 个自有属性、原型与 `instanceof` 都正常）。**两个引擎都一样**——
  这是案例里最容易走错的一步：它看起来像"pyv8c 的包装坏了"，实际是站点反探测。
- 网络层：`POST …/xhr/assets/js/bundle`，体是 `payload=<token>&pc=…&rsc=n`，
  token 是站点自己的编码（`base64(XOR50(JSON))` 里按 uuid 派生的位置插入 20 个字符，
  案例里已破解并验证，解码器见 `examples/px3/script/payload.py`）。

## 挖到并修掉的缺陷

| # | 缺陷 | 之前 → 现在（Chrome 实测） |
| --- | --- | --- |
| 1 | `requestAnimationFrame` 当作立刻到期的定时器 | 3 秒 183153 帧 → 179~181 帧（Chrome 165~166）；同帧共享时间戳、间隔锁 16.667 ms 网格 |
| 2 | `performance.now()` 暴露原始精度（约 0.02 ms） | 量化到 100 µs（Chrome 0.1000） |
| 3 | `element.style` 只枚举 9 个成员 | Chrome 的 748 个名字、顺序一致、`webkit` 前缀各 151 |
| 4 | `speechSynthesis.getVoices()` 恒为空 | 与 Chrome 相同的 3 个语音，先空后到并派发 `voiceschanged` |
| 5 | 替换元素没有默认尺寸 | iframe/canvas/video/object/svg `0x0` → **300×150**（iframe 含 UA `2px inset` 边框 = 304×154） |
| 6 | 行内级空盒被拉成整行宽 | 无 `src` 的 `img` `1594x0` → `0x0` |
| 7 | `getBoundingClientRect()` 返回普通对象 | 真正的 `DOMRect`（`instanceof`、`toJSON`、`Object.keys` 为空） |
| 8 | `p`/`h1`/`ul` 等被当成行内元素、UA 外边距全缺 | 块级堆叠（`li`/`summary` 为 `list-item`）+ UA 外边距表 |
| 9 | **链接样式表从不被解析** | `cssRules` 0 → 3；真站容器 `1888x0` → `381x0` |

第 5~9 条现在都能用本 skill 的探针离线复查：
`python scripts/harness.py --probe replaced --probe rect --probe ua --probe style`。

## 案例教给流程的三件事

1. **先做两侧同样的观测**：第 6 节那层"读不出来"的包装浪费了两轮排查，直到把它放到 Chrome 上
   跑同一个监视器，才发现两边逐字相同。
2. **条件不一致会伪造差异**：早期用无头 Chrome、两侧采样时间也不同，得出的"载荷差 1.5 KB"
   里有一部分是方法造成的。固定条件后剩余的差异才是真差异。
3. **把发现压成最小页面才动引擎**：第 9 条（链接样式表）最后落在三行改动上，
   靠的是一个"页面 + 一段 CSS + 一个探针"的最小复现。

## 还没解决的（案例遗留）

- `#px-captcha` 的高度差：Chrome `468x102`（计算 `height:102px`）、pyv8c `381x0`——
  宽度已修，高度仍 0，说明还有一条给容器定高的规则没生效。
- 载荷里三个稳定缺口字段（一对约 1~2 秒的毫秒数、一个 `webkit` 字符串、一块 896 字符的高熵数据）。
