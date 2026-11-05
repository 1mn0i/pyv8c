# pyv8 benchmarks

这些脚本用于测量原生桥接、对象转换以及浏览器页面生命周期。运行前先安装本仓库的 `pyv8` 和 `pyv8c`；从仓库根目录执行，例如：

```powershell
python benchmarks/bridge.py --output "$env:TEMP/bridge-current.json"
python benchmarks/benchmark.py --runs 5 --output "$env:TEMP/framework-current.json"
python benchmarks/measure_startup.py --quiet
python benchmarks/lifecycle.py --count 1000 --output "$env:TEMP/lifecycle-current.json"
python benchmarks/plot_results.py --input "$env:TEMP/lifecycle-current.json" --output-prefix "$env:TEMP/lifecycle-current"
```

绘图需要可选依赖 matplotlib；生命周期脚本和 bridge 的 DOM、页面项目需要 `pyv8c`。仅依赖原生绑定的专项测量脚本位于 `pyv8/benchmarks/`。

`results/` 中的 JSON 和图表是历史实验的原始证据，供 `docs/pyv8/bridge-refactor-results.md` 引用，不代表当前性能基线。不要直接覆盖它们或与不同 V8、二进制、机器或工作负载的结果比较。正式比较应在同一环境串行交替运行至少五轮，核对版本与二进制摘要；bridge 的工作区摘要只覆盖 Git 已跟踪文件的变更，不包含未跟踪文件。

`perf_baseline.json` 同样是历史测量；`benchmark.py` 在记录的 Python 版本与当前版本不同时拒绝比较。启动阶段测量的包内入口也可通过 `python -m pyv8c.tools.measure_startup` 调用。
