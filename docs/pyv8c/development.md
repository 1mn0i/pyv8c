# 开发指南

**简体中文** | [English](../en/pyv8c/development.md)

[文档索引](../README.md)

## 环境与工作约定

在仓库根目录使用 CPython 3.12 安装项目，并确认加载的是本仓库的代码：

```powershell
$env:PYTHONUTF8 = "1"
python312 -m pip install -e ".[dev,reference]"
python312 -c "import pyv8c; from pyv8c import pyv8; print(pyv8.__file__, pyv8c.__file__)"
```

构建前须配置 V8 构建目录，详见原生项目文档。使用 Python 3.15 时先按下方说明以 Python 3.12 构建 ABI3 wheel。

修改前记录仓库的工作区状态，保留已有修改。核对实际加载的原生二进制、解释器、IDL、环境档案和 Chrome；修改 C++ 后必须重建正在使用的扩展，不能只用旧二进制跑 Python 测试。

核心规则见[架构设计](architecture.md)：修复进入实际拥有行为和状态的层，Web API 不直接依赖 Page 控制器；缓存明确所属实例、失效和释放条件，不建立进程全局的页面可变状态。

## 源码构建

### Windows 本地构建

本机构建发行包时，先按 [pyv8 文档](../../pyv8/README.md)准备 V8，然后在仓库根目录使用 CPython 3.12：

```powershell
py -3.12 -m pip install build wheel
py -3.12 -m build --wheel --outdir dist .
```

生成的 `pyv8c-*-cp312-abi3-win_amd64.whl` 是单包发行物。

### Linux 本地构建

Ubuntu 22.04 x86-64 可以在 Linux 本机准备 V8 并构建 wheel，使用启用 GIL 的
CPython 3.12、C++20 编译器和 Python 构建工具：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel build
python setup.py build_v8
python -m build --wheel --no-isolation
python -m pip install dist/pyv8c-*-linux_x86_64.whl
```

如需代理，可向 `build_v8` 传入 `--proxy=http://主机:端口`。首次构建会下载
V8 和 Chromium 工具链并耗费较长时间及磁盘空间；可通过 `PYV8_V8_JOBS` 控制并行度。
上述 Linux wheel 已在 Ubuntu 22.04 x86-64 验证基本运行，不代表兼容所有 Linux 发行版。

### Windows、Linux 和 macOS CI

仓库的 GitHub Actions 工作流 `Build wheels` 可手动选择并并行构建三个平台的 wheel。
Windows 和 macOS arm64 使用 GitHub 托管 runner；Linux 使用带 `pyv8c-v8` 标签的自托管 x64 Ubuntu
runner。先在仓库 **Settings → Actions → Runners → New self-hosted runner** 中选择
Linux x64，在虚拟机上按 GitHub 页面提供的命令下载并注册 runner；配置时添加自定义
标签 `pyv8c-v8`，并让 runner 以安装了 Python 3.12 的用户运行。注册令牌由 GitHub
页面临时生成，不要提交到仓库。启动 runner 后，在 **Actions → Build wheels → Run workflow**
勾选需要的平台并手动运行。对应产物为 `windows-wheels`、`linux-wheels` 和
`macos-arm64-wheels`；Ubuntu 未运行时，可取消勾选 Linux。

Linux 构建会把 V8 放在运行用户的 `~/.cache/pyv8c-ci/v8` 下供后续任务复用；首次
构建需要足够的磁盘空间和较长时间。若虚拟机访问 V8 需要代理，应在启动 runner
进程前配置 `http_proxy` 和 `https_proxy`；如果使用系统服务，也需为该服务配置代理。
各平台任务检查安装后冒烟与原生崩溃冒烟；Linux 和 macOS 另检查原生参数桥接，Windows wheel 测试矩阵另跑架构层门禁。这些都不代表浏览器完整测试全部通过。
自托管 runner 会执行工作流代码，应仅用于受信任的工作流和代码来源。

## 实现接口

接口声明使用 `pyv8c.idl.interface`。方法常用 `fn_name`，属性常用 `get_name`、`set_name`；named/indexed 与跨源 handler 使用对应 `__v8_*__` 约定。成员表面与属性位以 IDL 为依据，Python 负责参数转换、状态及实际语义，不重复手写基线已经确定的形状。

Feature 的通用运行机制位于 `pyv8c/pyv8c/features/runtime.py`，内置声明位于 `pyv8c/pyv8c/features/catalog.py`，核心接口组装位于 `pyv8c/pyv8c/features/core.py`。Feature 实现模块显式导出 FEATURE，并在 FeatureSpec 中声明依赖。新增可选功能不应使基础包导入时加载全部实现或原生运行时。纯元数据、提取器及基础资源记录仍需支持无原生环境导入。Web API 的目录归属见[架构设计](architecture.md#web-api-归属)。

开发前先建立最小失败场景，再修复和添加回归测试。普通 JS 错误、宿主故障、执行终止与资源失败分别处理；宽泛捕获 BaseException 时必须确认是否需要传播执行控制信号，不能靠吞掉终止后继续页面流程保证宿主存活。

异步回调校验取消、文档代次和身份。真实网络、Python 阻塞及工作线程结果回投不能绕过 owner 规则。新增公共接口同时更新类型声明和调用方；新增生命周期记录必须有统一终态和释放出口。

## 测试通道

```powershell
# 从仓库根目录运行原生模块与框架测试，不含 slow
python -m pytest -q

# 只运行浏览器框架测试
python -m pytest -c pytest.ini pyv8c/tests -q

# 不运行 Chrome 对照的常规主套件（含原生崩溃冒烟）
python -m pytest -c pytest.ini pyv8c/tests/unit pyv8c/tests/integration pyv8c/tests/browser pyv8c/tests/native -q

# 只跑原生崩溃冒烟（独立子进程）
python -m pytest -c pytest.ini pyv8c/tests/native -q

# 慢速生命周期、堆预算及策略复查
python -m pytest -c pytest.ini pyv8c/tests -o addopts=--strict-markers -m slow -q

# 标记竞态用例至少重复五轮
python -m pytest -c pytest.ini pyv8c/tests -o addopts=--strict-markers -m race --count=5 -q

# 架构、改动配套、启动、离线导入和重复用例检查
python tools/check_policy.py
```

默认 pytest 配置排除 slow，所以 `-m slow` 同时覆盖 addopts，避免选择器冲突。竞态测试也可能标记 slow，重复运行时同样需要覆盖。不要默认并行运行 Chrome 对照。

修改原生支持时，在仓库根目录运行构建、测试和布局检查：

```powershell
python312 setup.py build_ext --inplace
python -m pytest -c pytest.ini pyv8/tests -q
python pyv8/tools/check_layout.py
```

构建需要该仓库要求的 V8 SDK、编译器和相关设置。不要在其他进程仍加载目标二进制时覆盖它，也不要清理非本次创建的进程。

## 测试分层与归位规则

`pyv8c/tests/` 下五个目录不是按功能模块划分，而是按「跑起来需要什么」和「断言的判据由谁给」分层。这套划分被 `tox.ini`、`pytest.ini` 和 `tools/check_policy.py` 直接引用，新增测试放错层会破坏门禁。

| 目录 | 判据来源 | 运行环境 | 典型内容 |
| --- | --- | --- | --- |
| `unit` | 纯 Python 断言 | 必须能在没有已编译扩展时导入 | 架构静态检查、IDL/元数据、参数转换、CookieJar、工具脚本 |
| `browser` | 单个 Web API 在 pyv8c 里的返回值 | 离线（`default_loader` 返回空 200），不用 Playwright | DOM/BOM 接口、事件、存储 |
| `integration` | 多个子系统接起来是否还成立 | 本地 HTTP/WebSocket server、Worker、定时器、线程拆除 | 导航、资源、执行预算 |
| `native` | 子进程是否活着 | 独立进程 + `-X faulthandler` | `test_native_smoke.py`；C++ 段错误会直接杀死 pytest 进程，只能在进程外断言退出码 |
| `repros` | 真实 Chrome 与 pyv8c 是否一致 | 本机 Chrome + Playwright（`reference` extra） | `corpus.py` 的行为语料，同一 case 两侧各跑一次 |

归位规则：不碰 V8 → `unit`；单个页面行为 → `browser`；跨组件或真实 I/O → `integration`；担心 C++ 崩溃 → `native`；要跟 Chrome 对答案 → `repros`。

三条硬约束：

- `tox.ini` 的 py312~py315 wheel 矩阵只跑 `pyv8/tests` + `tests/unit` + `tests/browser` + `tests/integration`，刻意排除 `native` 和 `repros`：矩阵验证的是同一份 abi3 wheel 在四个 CPython 版本上的行为，不能依赖本机 Chrome，也不必把子进程冒烟重复四次。
- `tools/check_policy.py` 的 `offline` 门设置 `PYV8C_OFFLINE=1`，由 `tests/conftest.py` 屏蔽 `pyv8c.pyv8` 后只跑 `tests/unit`。因此 **unit 中任何顶层导入会连带加载原生扩展的测试文件，都必须在所有 `pyv8c` 导入之前写一行 `pytest.importorskip("pyv8c.pyv8")`**。该门先做静态预检：按包内 import 图的传递闭包算出「原生绑定模块」（含独立的 `pyv8c._audio_native`），报出违规的文件与行号。`@pytest.mark.native` 只表示「此用例需要已编译扩展」，不能替代这行守卫，因为收集阶段就已经失败了。干净的 CI checkout 同样要设 `PYV8C_OFFLINE=1`，否则 `pytest.importorskip` 会把 `cannot import name '_pyv8'` 重新抛出，而不是跳过。
- `pytest.ini` 注册 `native` / `race` / `slow` 三个 marker 并默认 `-m "not slow"`。**`@pytest.mark.native` 与 `native/` 目录不是一回事**：前者是「需要扩展」的标记，`unit` 里也有；后者表示必须在独立子进程里验证不崩溃。

`repros` 是双引擎行为语料，不是普通回归测试：本机 Chrome 版本、IDL 基线（`154.0.8030.0`）与 `chrome-environment.json` 的环境档案（`156.0.8078.80`）互不相同，版本一漂移就会整批报错。默认 `pytest -q` 也会收集它，上面「不运行 Chrome 对照的常规主套件」是日常改动更合适的判据；不要用改 `expected` 的方式把漂移修绿。

`native/` 已接进 `Build wheels` 工作流：Windows wheel 测试矩阵、Linux 与 macOS 构建任务在安装 wheel 后各跑一次原生崩溃冒烟，Windows 矩阵另在 Python 3.12 上跑 `layers` 架构门；源码树里没有编译扩展时，冒烟测试自动从已安装的 wheel 加载，本地有扩展时仍按原来的源码路径运行。`layers` 门会启动子进程真实导入 Web 模块，`startup` 门要构造 Browser，二者都需要编译扩展，因此留在 wheel 任务与本地 pre-commit；`.github/workflows/source-checks.yml` 的 PR 门禁只跑 `check_license.py`、三个纯源码策略门和离线 `tests/unit`。

## Chrome 行为对照

```powershell
$env:PYV8C_CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"
python -m pytest -c pytest.ini pyv8c/tests/repros/test_execution_model_vs_chrome.py -q
```

对照工具使用本机可执行文件；缺失 Chrome 或 Playwright 的用例可能跳过，跳过不能算通过。记录实际版本和启动参数，不静默替换另一 Chromium 或修改自动化状态。

两侧使用同一 HTML、origin、响应头、脚本和操作序列。真实访问用于发现问题，本地服务器或 fixture 用于稳定验收；fixture 只服务明确资源，未知请求失败。先比较导航、资源、脚本、事件和就绪条件，定位最早因果分歧，再检查接口表面或环境分支。

区分框架缺陷、功能缺失、配置/版本/设备差异、服务端响应及探针干扰。WebGL、媒体、权限和网络差异尤其需要记录环境因素。页面的环境检查应提炼为可重复的浏览器语义，不用常量成功、站点补丁或替换脚本代替框架修复。

## 提取与性能

IDL 和环境采集命令见 [IDL 格式](idl-format.md)。生成产物前核对来源版本，先写独立输出验证；不要无授权覆盖 Chromium/V8/profile 基线。

Window 顺序来自环境档案的 `bindings.own_order`；更新顺序应重新采集目标 Chrome，记录版本和采集条件。IDL 只记录接口与成员，不再保存单独的 Window 顺序基线。运行时统一安装实际暴露的属性，不能满足的 V8 内置属性顺序由差异诊断报告。

性能先剖析再优化，同机同解释器、同源码/二进制标识和同工作负载串行交替测量至少五轮，记录中位数、最小值、波动和关闭后回收。无稳定收益或引入行为回退的改动不保留。

```powershell
python benchmarks/benchmark.py --runs 5 --output .\benchmark-result.json
python benchmarks/measure_startup.py
```

这些命令会运行测量。`benchmark.py --record` 和策略工具的 `--record-startup` 会改写性能基线，不能用它们消除回退。环境变化和工作负载变更需要单独说明，不能直接比较不等价数字。

## 交付要求

说明具体问题、触发条件、所属层修复、回归测试与实际验证结果。区分已通过、未验证、跳过和已知失败；保存竞态失败原始证据，不靠复跑成功宣称根因消失。不放宽断言、扩大排除或更新基线掩盖失败。

文档只维护当前可用接口与已知限制。命令、链接和示例变更后需验证；专题文件统一使用小写短横线命名，索引使用 README.md，正文统一中文。
