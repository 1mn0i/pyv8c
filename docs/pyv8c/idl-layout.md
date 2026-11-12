# IDL 的三个位置

**简体中文** | [English](../en/pyv8c/idl-layout.md)

- `pyv8c.engine.idl` 读取 IDL 基线文档、应用 profile 并提供查询。
- `pyv8c.idl.spec` 定义 Python 实现使用的接口和成员声明。
- `pyv8c/pyv8c/idl/idl.zip` 是随包分发的 Chromium IDL 数据，不是 Python 实现。

文档和查询视图使用有界缓存复用，生成的接口类由每个 Browser 的 Bindings 持有；
加载计时属于一次 `Idl.load` 调用，不在 Browser 之间共享。接口实现覆盖情况可使用
`python tools/idl_audit.py --coverage` 查看；添加 `--json report.json` 可保存完整列表。

发行包使用的基线是 `CHROMIUM_VERSION` 对应的 `pyv8c/pyv8c/idl/idl.zip`。打包前会核对归档内版本。
