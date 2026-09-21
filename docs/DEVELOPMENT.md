# 开发、构建与验证

`pyv8c` 与相邻 `_pyv8` 是两个独立仓库。Python 包安装、原生扩展构建、Chromium baseline 生成分别处理。

## 环境

```powershell
Set-Location D:\src\pyv8env\pyv8c
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pip install -e ".[dev]"
python -c "import pyv8, pyv8c; print(pyv8.__file__); print(pyv8c.__file__)"
```

运行时必须能导入相邻 `_pyv8` 的 `pyv8` 扩展。修改 `_pyv8` 的 C++、V8、Inspector 或 trace 时，在相邻仓库单独构建并运行其测试；不要把本地非发布构建当作正式 abi3 产物。

## 代码工作流

1. 先查看 `git status --short`，保留已有工作区修改。
2. 用 `rg` 找到入口、调用者、相关测试和文档链接。
3. 判断修改属于 platform、foundation、engine、web Feature 还是 `_pyv8` 原生层。
4. 先补专项测试，再修改实现。
5. 检查 capability 边界、Feature 所有权、Realm 物化和关闭顺序。
6. 运行专项测试、完整测试和 diff 检查。

## 常用验证

```powershell
python -m pytest -q
python -m pytest tests/unit/test_lazy_interface_modules.py tests/integration/test_lazy_interfaces.py -q
python -m pytest tests/integration/test_indexeddb.py -q
python -m pytest tests/browser/test_devtools.py -q
python -m pytest tests/unit/test_interface_extractor.py tests/unit/test_idl_compilation.py -q
git diff --check
```

Feature 全量结构校验使用：

```python
from pyv8c.platform.bootstrap import validate_feature_catalog
validate_feature_catalog()
```

## 策略检查

提交前跑一次，三层闸门：

```powershell
python tools/check_policy.py                  # 工作区 vs HEAD
python tools/check_policy.py --base main      # 与分支比较
python tools/check_policy.py --strict         # 把"实现改动没配测试"的告警当成失败
python tools/check_policy.py --record-startup # 接受当前启动时延作为新预算
```

- `layers`：分层规则，委托 `tests/unit/test_architecture.py`，不在此重复实现。
- `companions`：`pyv8c/` 下改了 `.py` 却没有任何 `tests/` 文件随之改动时告警。
- `startup`：与 `tools/startup_baseline.json` 记录的预算比较（中位数 +10% +5ms）。测量的是新
  进程里 `Browser() -> new_context() -> new_page() -> set_content()` 的耗时与导入模块数；换
  机器、或有意改变启动开销后需要重新记录。

## Baseline 更新

运行 `tools/fetch_chromium.py` 和 `tools/extract_interfaces.py` 前确认 Chromium 版本、生成目录和输出 ZIP。baseline 不应依赖人工 review JSON；抽取器只使用 Chromium 生成元数据。更新后运行抽取器、baseline、IDL 编译和完整浏览器测试。

## 性能和并发

不要用未经测量的“提速”描述替代基准。Feature 模块应在首次使用时导入，Browser Runtime 状态不能跨实例共享；Realm Session 配置不能跨页面共享。网络线程不得同步等待页面 owner 线程，关闭操作必须幂等。

## 交付

最终说明列出实现变化、测试命令和结果、环境限制、未处理失败以及是否创建 commit。删除代码或文档时说明原因；不恢复历史日志文件，不修改任务范围外的 IndexedDB、DevTools、trace 或相邻仓库改动。
