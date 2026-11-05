# 开发指南

[文档索引](../README.md)

## 环境与工作约定

在仓库根目录使用 CPython 3.12 依次安装两个包，并确认加载的是本仓库的代码：

```powershell
$env:PYTHONUTF8 = "1"
python312 -m pip install --config-settings editable_mode=compat -e ./pyv8
python312 -m pip install --config-settings editable_mode=compat -e "./pyv8c[network,dev,reference]"
python312 -c "import pyv8, pyv8c; print(pyv8.__file__, pyv8c.__file__)"
```

在仓库根目录使用 setuptools 的普通 editable 安装时，同名项目目录可能遮蔽实际包；以上命令启用兼容模式。构建 pyv8 前须配置 V8 构建目录，详见原生项目文档。使用 Python 3.15 时按[项目安装说明](../../README.md#安装)先以 Python 3.12 构建 ABI3 wheel。

修改前记录仓库的工作区状态，保留已有修改。核对实际加载的原生二进制、解释器、IDL、环境档案和 Chrome；修改 C++ 后必须重建正在使用的扩展，不能只用旧二进制跑 Python 测试。

核心规则见[架构设计](architecture.md)：修复进入实际拥有行为和状态的层，Web API 不直接依赖 Page 控制器；缓存明确所属实例、失效和释放条件，不建立进程全局的页面可变状态。

## 实现接口

接口声明使用 `pyv8c.idl.interface`。方法常用 `fn_name`，属性常用 `get_name`、`set_name`；named/indexed 与跨源 handler 使用对应 `__v8_*__` 约定。成员表面与属性位以 IDL 为依据，Python 负责参数转换、状态及实际语义，不重复手写基线已经确定的形状。

Feature 的通用运行机制位于 `pyv8c/pyv8c/features/runtime.py`，内置声明位于 `pyv8c/pyv8c/features/catalog.py`，核心接口组装位于 `pyv8c/pyv8c/features/core.py`。Feature 实现模块显式导出 FEATURE，并在 FeatureSpec 中声明依赖。新增可选功能不应使基础包导入时加载全部实现或原生运行时。纯元数据、提取器及基础资源记录仍需支持无原生环境导入。Web API 的目录归属见[架构设计](architecture.md#web-api-归属)。

开发前先建立最小失败场景，再修复和添加回归测试。普通 JS 错误、宿主故障、执行终止与资源失败分别处理；宽泛捕获 BaseException 时必须确认是否需要传播执行控制信号，不能靠吞掉终止后继续页面流程保证宿主存活。

异步回调校验取消、文档代次和身份。真实网络、Python 阻塞及工作线程结果回投不能绕过 owner 规则。新增公共接口同时更新类型声明和调用方；新增生命周期记录必须有统一终态和释放出口。

## 测试通道

```powershell
# 从仓库根目录运行两个包的测试，不含 slow
python -m pytest -q

# 只运行浏览器框架测试
python -m pytest -c pyv8c/pyproject.toml pyv8c/tests -q

# 不运行 Chrome 对照的常规主套件
python -m pytest -c pyv8c/pyproject.toml pyv8c/tests/unit pyv8c/tests/integration pyv8c/tests/browser -q

# 慢速生命周期、堆预算及策略复查
python -m pytest -c pyv8c/pyproject.toml pyv8c/tests -o addopts=--strict-markers -m slow -q

# 标记竞态用例至少重复五轮
python -m pytest -c pyv8c/pyproject.toml pyv8c/tests -o addopts=--strict-markers -m race --count=5 -q

# 架构、改动配套、启动、离线导入和重复用例检查
python tools/check_policy.py
```

默认 pytest 配置排除 slow，所以 `-m slow` 同时覆盖 addopts，避免选择器冲突。竞态测试也可能标记 slow，重复运行时同样需要覆盖。不要默认并行运行 Chrome 对照。

修改原生支持时，在 `pyv8/` 项目运行构建、测试及其 `tools/check_layout.py`，再回到仓库根目录验证受影响链路：

```powershell
Push-Location pyv8
try {
    python setup.py build_ext --inplace
    python -m pytest tests -q
    python tools/check_layout.py
} finally {
    Pop-Location
}
```

构建需要该仓库要求的 V8 SDK、编译器和相关设置。不要在其他进程仍加载目标二进制时覆盖它，也不要清理非本次创建的进程。

## Chrome 行为对照

```powershell
$env:PYV8C_CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"
python -m pytest -c pyv8c/pyproject.toml pyv8c/tests/repros/test_execution_model_vs_chrome.py -q
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
