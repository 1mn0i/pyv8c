# IdlSpec 重构性能验收（2026-09-08）

## 环境与口径

- 同一台 Windows x64 机器，CPython 3.12.9，V8 15.2.20.1，原生 release 构建。
- 冷启动前后各 30 个独立 Python 进程；使用相同 baseline binary-cache 条件。
- 计时从 `import pyv8c` 完成后创建 Browser 开始，到 Browser.close 完成结束；
  不包含 Python 进程启动及模块导入时间，不应解读为完整 CLI 启动耗时。
- p50 为中位数，p95 为 nearest-rank：排序后第 `ceil(0.95 * N)` 个样本。
  为统一口径，前后 p95 均从原始样本重算；基准脚本也已修正原来的向下取整索引。
- 完整测试与构建结束后串行采样；性能数据不与测试或编译负载并行采集。

## 可直接对比的冷启动

| 指标 | 改造前 | 改造后 | p50 变化 |
| --- | ---: | ---: | ---: |
| 总耗时 p50 / p95（ms） | 100.89 / 132.84 | 60.29 / 68.66 | -40.24% |
| binding_plan_ms p50（ms） | 30.86 | 32.21 | 4.36% |
| context_create_ms p50（ms） | 3.24 | 3.03 | -6.39% |
| lazy_register_ms p50（ms） | 41.62 | 1.28 | -96.92% |

主要收益来自 lazy 注册阶段；第一次结构编译并非所有指标都更快，冷 binding_plan
的 p50 有小幅增加。该成本现在按 Universe 缓存，而不再按页面重复产生。
原始样本：`benchmarks/idl-startup-before.csv`、`benchmarks/idl-startup-after.csv`。

## 改造后工作负载（30 个独立进程）

| 工作负载 | p50（ms） | p95（ms） |
| --- | ---: | ---: |
| warm_page_ms | 4.098 | 4.347 |
| lazy_first_touch_ms | 0.549 | 0.806 |
| batch_config_ms | 1.986 | 2.312 |
| batch_template_ms | 0.622 | 0.792 |
| dom_20000_calls_ms | 55.021 | 63.387 |

- warm_page 为同一 BrowserContext 下连续 5 次创建并关闭页面的单页均值。
- lazy_first_touch 为 CSSStyleDeclaration 首次访问。
- batch_config 为 Text/Comment/DocumentFragment/Attr/Range 及祖先的一次配置批提交。
- batch_template 为随后首次获取这 5 个构造器；不包含前一行的编译及注册成本。
- dom_20000_calls 为预热后 10,000 次 setAttribute + 10,000 次 getAttribute。
- 这些场景没有保存对应的改造前采样，因此这里只给绝对值，不计算加速比。
原始样本：`benchmarks/idl-workloads-after.csv`。

## 结构与正确性

- 每个进程初始编译 18 个接口，签名缓存 110 项。
- 连续创建 5 个新页面后，30/30 个进程的额外接口编译和额外签名缓存项均为 0。
- 后续 lazy/批量/DOM 场景后为 28 个接口、120 个签名缓存项。
- 原生描述创建时保存回调解析状态；已编译的描述直接复用，不再重复扫描回调字段。
- IDL 安装没有位置元组解析、ClassConfig、callback-owner 类或 direct/raw-name 探测。
- 回归测试验证每个接口最多一次结构编译、realm 无关配置身份共享及静态回调隔离。
- 原生扩展重新构建成功；`_pyv8` 207 项、`pyv8c` 345 项全量测试通过。

## 验收范围与限制

冷启动以及 lazy 注册路径有可重复改善，现有行为回归和结构性验收通过。
但没有改造前 DOM 热点、warm-page、lazy 首触及批量安装的同口径原始样本，
因此不能宣称已完成这些项目的前后性能验收，尤其不能证明热点吞吐回退不超过 5%。
此报告记录的是缓存条目/编译次数，并非整个 Python/V8 堆的分配事件计数。
如需关闭剩余性能验收项，应在保留的改造前工作树和扩展产物上运行同一基准；
不能拿当前 Git HEAD 代替本轮开始时含未提交修改的版本。

## 复现命令

在两仓库均位于 PYTHONPATH、原生扩展已构建的环境中，于 pyv8c 根目录执行：

```powershell
python tools/benchmark_startup.py --runs 30 --json
python tools/benchmark_bindings.py --runs 30
```
