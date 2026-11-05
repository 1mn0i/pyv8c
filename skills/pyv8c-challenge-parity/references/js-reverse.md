# 环境校验：定位输入、分支和加载后果

环境检查包括普通 feature detection、兼容性降级、权限判断，以及页面对浏览器一致性的检查。不要根据变量名或站点类型猜用途；用实际控制流定性。

## 分析记录

每个有因果作用的检查，记录以下链条：

```text
脚本 URL/摘要/位置 → Realm 与调用时机 → 输入来源
→ Chrome 值/异常/对象关系 → pyv8c 对应结果
→ 分支条件 → 应产生的请求、任务、DOM 或消息 → 实际差异
```

分别保存未注入与带探针结果。包装 getter、Function、eval、Promise、计时或网络入口会改变身份、时序和异常，只有在不改变目标分支的对照下才使用。调试器暂停导致的超时不要当运行时缺陷。

## 检查维度

| 维度 | 局部观测 |
| --- | --- |
| 接口暴露 | `typeof`、`in`、own property、可用上下文、IDL/profile 版本；不存在可能正确 |
| 对象结构 | own/prototype descriptor、枚举性、getter/setter、方法 length/name、继承链 |
| 接收者与 Realm | 借用方法、非法接收者、跨 frame 构造器与返回对象身份、关闭后的对象 |
| 值与关系 | 值类型、同一对象是否稳定、别名是否同函数、live collection 是否实时 |
| 安全上下文 | 实际 origin、继承/opaque origin、sandbox、CORS/CSP、跨源访问与异常类型 |
| 状态与能力 | 权限、用户激活、可见性、存储/Cookie、设备/驱动；配置差异不要硬编码为 Chrome 常量 |
| 异步语义 | Promise/微任务、事件顺序、帧消息、取消、导航替换、生命周期完成屏障 |

“宿主 Python 对象有值，JS 读不到”也可能是绑定、接收者、转换或旧 Realm 的错误，不能直接认定页面在反探测。

## 阅读脚本的顺序

1. 保存实际加载的脚本 URL、正文摘要和对应响应。优先 source map/未压缩源码；确认版本相同。
2. 从首个异常、未触发资源或等待条件定位调用点，只沿相关函数追踪。
3. 属性名查表时，先静态提取字符串表和引用位置；必要时离线执行最小解码器。不要直接执行整包来代替理解。
4. 将比较表达式与后续分支提取成无第三方依赖的本地场景。对照调用顺序、返回类型、异常、身份和消费者结果。
5. 修复框架语义，再用原脚本确认；不删除检查、不伪造校验结果、不复用真实 Chrome 的认证结果或令牌。

静态工具示例（在仓库根）：

```powershell
python skills/pyv8c-challenge-parity/scripts/js_reverse.py strings bundle.js --min 8
python skills/pyv8c-challenge-parity/scripts/js_reverse.py calls bundle.js lookupName
python skills/pyv8c-challenge-parity/scripts/js_reverse.py diff before.js after.js
```

`tables`/`pool` 用于候选表定位，基于文本匹配，不是完整 JS 解析器。不要沿用历史“本机没有 AST 工具”的假设；按当前环境选择解析器。

离线计算局部表达式：

```powershell
python skills/pyv8c-challenge-parity/scripts/js_reverse.py run --prelude decoder.js --snippet 'JSON.stringify(names)' --timeout 20 --out evidence/expression.json
```

该入口禁用真实网络，由父进程限制执行和关闭总时长；不是安全执行任意宿主代码的沙箱。输入、表达式和结果均属于诊断证据，不构成完整页面行为证明。
