# pyv8 原生基准

本目录的脚本使用 `pyv8c` 内置的 V8 引擎，统一通过 `from pyv8c import pyv8` 导入：

- `bench_collections.py`：Python 集合转换和跨边界传递。
- `bench_install.py`：Realm 中接口成员的安装开销。
- `bench_object_pairs.py`：Python 与 JavaScript 对象映射。
- `bench_strings.py`：字符串跨边界传递。

从仓库根目录运行，例如 `python pyv8/benchmarks/bench_strings.py`。正式性能比较需要在相同环境中串行交替运行，并记录源码、二进制与 V8 版本。涉及浏览器框架的基准和历史实验数据位于仓库根目录的 `benchmarks/`。
