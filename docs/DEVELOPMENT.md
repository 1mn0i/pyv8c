# 开发、构建与验证

[返回文档索引](README.md)。本项目与相邻 _pyv8 是两个独立仓库；Python 包安装、原生扩展构建和 Chromium baseline 生成是三条不同流程。

## 本地开发环境

pyproject.toml 声明 Python 3.10+，运行时依赖 loguru 和 websockets；network extra 提供 cycronet，dev extra 提供测试与静态检查工具。
浏览器运行还需要能导入的 pyv8 原生扩展，它不由本仓库的 pip 安装自动构建。

当前双仓库布局：

```text
D:\src\pyv8env\
├── _pyv8\     原生桥、V8 构建配置、pyv8 包
└── pyv8c\     本项目
```

```powershell
Set-Location D:\src\pyv8env\pyv8c
$env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
python -m pip install -e ".[dev]"
python -c "import sys, pyv8, pyv8c; print(sys.version); print(pyv8.__file__); print(pyv8c.__file__)"
```

优先确认实际加载路径，避免测试了 site-packages 中的旧包或旧 .pyd。
其他目录布局应相应调整路径；POSIX 的 PYTHONPATH 分隔符为冒号，不使用 Windows 的分号。

## 原生扩展

构建依据是相邻 [README](../../_pyv8/README.md)、[setup.py](../../_pyv8/setup.py) 和本机 V8 构建配置。
只修改 Python/Markdown 不需要重建 native；修改 C++、头文件、V8 或编译配置时应重新构建。

发布 ABI3 构建要求 CPython 3.10 头文件；正确构建的 ABI3 产物与“任意解释器本地编出的 .pyd”不是同一保证。
本机此前使用 CPython 3.12.9 的本地非发布构建，不能据此宣称完成全部 Python/平台 ABI 验证。

已准备好本地 V8 和工具链时，发布构建在 CPython 3.10 环境执行：

```powershell
Set-Location D:\src\pyv8env\_pyv8
python setup.py build_ext --inplace --force
```

只有明确用于本地非发布验证、且当前解释器不是 3.10 时，才使用项目提供的显式覆盖：

```powershell
Set-Location D:\src\pyv8env\_pyv8
$env:PYV8_ALLOW_NEWER_ABI3_BUILD = '1'
try {
    python setup.py build_ext --inplace --force
} finally {
    Remove-Item Env:PYV8_ALLOW_NEWER_ABI3_BUILD
}
```

不要把覆盖开关加入发布默认配置。原生项目不支持 free-threaded CPython。
Windows 上先关闭加载旧扩展的进程，否则可能无法覆盖 .pyd；构建后重启运行进程再验证。
本指南不代替 V8/ICU 下载、编译器和平台依赖的完整准备步骤。

## 验证顺序

在 pyv8c 根目录、已配置本地 PYTHONPATH 的同一个解释器执行：

```powershell
python -m pytest tests/integration/test_trace.py -q
python -m pytest tests/unit tests/browser tests/integration tests/native -q
python -m ruff check pyv8c/foundation
python -m mypy --config-file pyproject.toml
```

第一条是 trace 改动的定向示例；其他改动替换为对应测试。涉及 native 语义时，再到 _pyv8 根目录执行其测试。

| 测试目录 | 主要职责 |
| --- | --- |
| tests/unit | registry/编译、提取器、executor、资源作用域等；部分测试仍需 native |
| tests/browser | DOM/BOM、集合、Window 成员与命名属性、Cookie、DevTools |
| tests/integration | 导航、事件循环、模块、iframe/Worker、资源、生命周期、trace |
| tests/native | 原生依赖 smoke test |

native 缺失时，部分测试会跳过；仅执行 unit 或得到 skips 不等于验证了浏览器运行。
pytest 配置启用 strict markers 和每测试 300 秒安全超时。

涉及并发时可额外执行，不要将历史执行结果当成本轮结果：

```powershell
python -m pytest tests/integration/test_race_regressions.py -m race --count 20 -q
```

ruff/mypy 当前只对 foundation 设置阻断门禁，不表示全仓库都完成了类型和格式治理。
当前实际验证记录集中在 [HANDOFF](../HANDOFF.md) 与 [WORKLOG](../WORKLOG.md)。

## CI 实际做什么

[ci.yml](../.github/workflows/ci.yml) 包含：

- Linux Python 3.10/3.11/3.12 的 foundation lint/mypy。
- 同版本的 unit 子集；需要 native 而无法导入的测试会跳过。
- Windows Python 3.12 检出相邻仓库预构建 .pyd，验证导入，跑 unit/integration/browser 和 race 重复 20 次。

当前 full job 不在 CI 中完整构建 V8，也未显式把 tests/native 加入其目录列表。
原生来源可由 PYV8_REPOSITORY/PYV8_REF 仓库变量覆盖；发布应固定可追溯版本，不盲目依赖另一个仓库的 main。

## 添加或修改接口

1. 在 web 对应领域实现行为，通过 services 消费任务、资源和 realm 能力。
2. 声明接口身份与不可变 IdlSpec；不要增加 cls trace 注入、位置元组配置或 Page 私有后门。
3. 在相应 registration 模块登记；核对 baseline 的父接口、exposure、成员位置、描述符与 length。signature 只用于离线核对。
4. 在方法/构造器/setter 内处理参数，测试缺参、undefined/null、转换副作用和同步抛错/Promise 拒绝。setter 不检查 MISSING。
5. 覆盖新旧 realm、lazy 首触、异常、关闭和重入等受影响路径。
6. 修改 [IDL 文档](IDL_MIGRATION.md) 或相关专题；更新版本/构建信息时同步交接记录。

Browser 没有公开 registry 参数。实验 registry/baseline 使用 engine 的显式 API 和现有单元测试模式，不发明不存在的 Browser 参数。

## 常用排障

| 现象 | 先检查 |
| --- | --- |
| import pyv8 失败 | 实际解释器、PYTHONPATH、扩展平台/ABI、ICU 和原生依赖 |
| 接口存在但返回 Undefined | 是否为 StubFactory 生成的未实现成员，检查 stub_logging |
| 修改回调后旧页面没变化 | IdlSpec/模板持有快照；重建 Browser/Context |
| 页面挂起 | owner 线程是否被同步 loader/脚本占用，是否等待 DevTools 启动释放 |
| trace 没输出 | traceable/页面开关、sink 日志级别、操作是否经过原生绑定；参见 TRACE |
| baseline 加载失败 | schema=5、compact=true、父接口闭包、缓存/ZIP 是否匹配 |
| 文档链接失效 | 专题路径统一 docs/，不要新增 doc/ 链接 |

PYV8C_AUTO_PUMP=0 关闭 executor 自动推进，用于特定测试；PYV8C_CALL_TIMEOUT 控制默认同步等待上限（默认 60 秒，非正值取消该上限）。
这些开关不提供强制杀死已经运行的 Python 回调的能力。
