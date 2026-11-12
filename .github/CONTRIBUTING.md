# 贡献指南

感谢你愿意改进 pyv8c。本项目采用 [MIT License](../LICENSE)，参与贡献即表示你同意按该许可证授权你所提交的代码，并遵守本文档与 [行为准则](CODE_OF_CONDUCT.md)。

## 1. 贡献范围

pyv8c 是用于复现浏览器脚本行为的研究与测试工具。我们欢迎：

- 以本机 Chrome 为行为对照补齐 Web API 实现，并附规范或实测依据；
- 修复执行链、生命周期、网络层与原生桥接的正确性问题；
- 文档、测试、构建与跨平台（Windows / Linux / macOS）支持；
- 使用自建靶站或合成数据编写的可复现示例。

**不受理**以绕过特定第三方站点的访问控制、规避反自动化措施或批量采集真实站点数据为唯一目的的 issue、讨论与 PR。请勿添加以规避为唯一目标的能力（例如自动过验证码、指纹伪装、代理轮换、批量并发压测、凭据复用）；请勿提交真实站点的凭据、Cookie、挑战令牌、HAR、完整请求体或私有脚本。新增及修改的示例应优先使用合成域名、自建靶站与脱敏数据；仓库中的历史示例不代表符合此要求，需单独审查后再修改或分享。

> 本节是社区支持范围的说明，不是对 MIT 许可证所授予权利的附加限制。

## 2. 开发环境与常用命令

需要 Python 3.12+；原生扩展依赖仓库内的 V8 构建产物，具体安装步骤见 `docs/pyv8c/development.md`。

```bash
python -m pytest -q                  # 纯 Python 单元与集成测试（根 pytest.ini）
python tools/check_policy.py         # 策略六道门：layers / native-layout / companions / startup / offline / duplicates
tox -e lint                          # 静态检查
tox                                  # py312~py315 wheel 矩阵
python -m build --wheel --outdir dist .
python tools/smoke_wheels.py         # 从安装后的环境冒烟
```

改过 C++ 源码后必须重建扩展（`python setup.py build_ext --inplace`）再跑测试。

## 3. 代码约定

- 分层方向：`foundation` → `network` / `engine` / `features` → `platform` → `web/*` → `pyv8` 原生 → V8；由 `pyv8c/tests/unit/test_architecture.py` 静态检查。
- 修复必须落在拥有该状态或行为的层；不伪造成功值、不删除检测、不复用真实浏览器的认证状态。
- 先建立最小失败场景，再改实现，并配回归测试。
- 新增 `web/**` 实现必须同批改测试（companions 门）；unit 测试用 `pytest.importorskip("pyv8c.pyv8")` 以通过 offline 门。
- 与 Chrome 对齐使用规范或实测真值，不硬编码环境差异值。
- 专题文档采用小写短横线命名，正文使用中文。
- 不提交大体积 HAR、构建产物、虚拟环境目录或任何凭据文件。

## 4. 提交与 PR

- 小步提交，提交信息说明「为什么」而非只写「改了什么」。
- 每个 commit 使用 `git commit -s` 追加 `Signed-off-by`（见下一节 DCO）。
- PR 描述写明验证命令与结果；CI 与 `check_policy.py` 需全部通过。
- 若改动涉及打包元数据或第三方组件，同步更新 `THIRD_PARTY_NOTICES.md`。

## 5. 开发者原创声明（DCO）

本项目采用 [Developer Certificate of Origin 1.1](https://developercertificate.org/)。在提交信息中加入：

```text
Signed-off-by: Your Name <you@example.com>
```

即表示你声明：

- (a) 贡献由你创作（全部或部分），且你有权按项目许可证提交；或
- (b) 贡献基于先前的作品，你有权按相应开源许可证提交修改后的版本；或
- (c) 贡献由已作出上述声明的他人直接提供给你，且你未作修改；
- (d) 你理解本贡献与签署记录是公开的、会被长期保存，并可能随项目或其许可证再分发。

`git commit -s` 会自动追加该行。无法签署的贡献请勿提交，可在 PR 中说明原因，由维护者决定处理方式。

## 6. 相关文档

- [行为准则](CODE_OF_CONDUCT.md)
- [安全政策与滥用举报](SECURITY.md)
- [第三方声明](../THIRD_PARTY_NOTICES.md)
