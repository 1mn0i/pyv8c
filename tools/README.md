# 仓库开发工具

在仓库根目录、安装好本仓库的 `pyv8` 与 `pyv8c` 后使用；各工具均支持 `--help`。

- `extract_idl.py`、`idl_audit.py` 和 `collect_environment.py` 是 `pyv8c.tools` 的命令行入口。
- `fetch_chromium.py` 获取 Chromium 并生成 Blink 绑定，运行会下载源码和调用构建工具。
- `collect_fonts.py`、`collect_text_layout.py` 使用本机 Chrome 采集或对比；文本布局默认使用框架随包的 IDL，可用 `--idl` 指定其他基线。
- `check_policy.py` 运行架构、改动配套、启动、离线导入与重复测试检查。性能测量入口位于仓库根目录的 `benchmarks/`。

`startup_baseline.json` 是历史启动测量，缺少完整环境信息，不是跨机器或跨版本的通用性能标准。检查结果必须结合机器、解释器和工作负载判断；需要新基线时先在同环境串行测量并记录原因。
