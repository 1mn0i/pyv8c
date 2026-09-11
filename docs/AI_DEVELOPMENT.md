# AI 接手开发规范

这份文档是给后续 AI coding agent 的工作协议。它描述当前架构、修改边界、验证要求和常见陷阱；源码与测试优先于本文件。

## 开始工作前

1. 在 `D:\src\pyv8env\pyv8c` 执行 `git status --short`，保留用户已有改动，不使用 `git reset --hard` 或覆盖式 checkout。
2. 同时确认相邻 `D:\src\pyv8env\_pyv8` 是否有原生改动；除非任务明确要求，不把两个仓库混成一次修改。
3. 设置：

   ```powershell
   $env:PYTHONPATH = 'D:\src\pyv8env\_pyv8;D:\src\pyv8env\pyv8c'
   ```

4. 先用 `rg` 搜索入口、调用者和测试，再修改文件。不要根据旧文档猜测架构。

## 架构不变量

- `Browser -> BrowserContext -> Page -> PageRealm` 是宿主生命周期主链。
- Web API 只能依赖 `foundation` 合同和 `PageServices` capability，不能反向导入 `platform` 控制器、`Page` 或 `Browser`。
- `FeatureCatalog` 只保存声明；`FeatureRuntime` 才拥有模块加载状态。每个 Browser 有独立 Runtime，Python `sys.modules` 的进程级缓存不等于 Browser 状态。
- Feature 实现模块必须显式导出 `FEATURE`，接口映射必须完整；不要重新引入 `vars(module)` 扫描、注册表冻结后写入或散落的 loader。
- Optional Feature 首次接口/Window 槽位访问时加载；同一 Feature 在一个 Browser 中只加载一次，在一个 Realm 中原子物化其关联接口。
- Window 值必须通过 Feature 槽位创建并保持对象身份；不要重新引入由 Window 自己维护的 `_lazy_bom` 或局部动态 import。
- Feature 内部的低频子对象必须在对应属性或方法首次使用时实例化。Window 槽位和 Feature 内部子对象统一使用 `LazyValueStore`：getter 只做可用性判断并调用 `.value("slot")`，集中式 `_create_value()` 负责构造。不要新增 `_object()`、逐属性 nullable 字段或 `dict.get()` 缓存。重复访问必须返回同一实例；构造父对象时不得预先创建 NetworkInformation、BatteryManager、Geolocation、Plugin 集合、存储配额等低频对象。
- 参数语义、转换顺序、同步异常和 Promise 拒绝属于 API 实现；不要增加签名驱动的统一校验 wrapper。
- `interface-baseline.zip` 提供结构、继承、成员和模板元数据，不是运行时参数调度器。

## 修改方法

- 新增 Web API：先确定所属 Feature、接口组、依赖和 Window 槽位，再写实现；在模块尾部之外定义显式 `FEATURE` 映射，并添加最小行为测试。若接口返回低频子对象，测试首次访问前未实例化、首次访问创建和重复访问身份稳定。
- 修改绑定：检查 `InterfaceUniverse`、`BindingSession`、`InterfaceCompiler` 和 `_pyv8` Context lazy factory 的数据流，确保失败时不提交半成品配置。
- 修改页面生命周期：沿 `PageServices` capability 传递，不给 Web 对象增加宽 owner/backdoor。
- 修改 IndexedDB、DevTools、trace 或资源系统时，只改变任务指定的子系统；避免顺手重写无关模块。
- 删除代码前先用 `rg` 检查所有 import、测试、命令行入口和文档链接；删除后运行专门测试再运行完整套件。

## 验证清单

```powershell
python -m pytest -q
python -m ruff check pyv8c tests
python -m mypy
git diff --check
```

按改动范围额外运行：

- Feature/懒加载：`tests/unit/test_lazy_interface_modules.py`、`tests/unit/test_window_lazy_bom.py`、`tests/integration/test_lazy_interfaces.py`。
- IndexedDB：`tests/integration/test_indexeddb.py`。
- DevTools：`tests/browser/test_devtools.py`。
- 绑定编译和 baseline：`tests/unit/test_idl_compilation.py`、`tests/unit/test_interface_extractor.py`、`tests/integration/test_baseline_versions.py`。

报告测试结果时给出实际命令和通过/失败数量；区分代码失败、环境缺少 `pyv8`、原生构建失败和既有无关工作区改动。

## 不要做的事情

- 不恢复 `WORKLOG.md`、`HANDOFF.md` 或一次性验收记录来替代测试和源码。
- 不新增第二套参数验证器、第二套 Feature loader、兼容旧内部路径的转发层或隐式全局 Page 状态。
- 不为了“清理”删除用户未提交的 IndexedDB、DevTools、trace 或其他无关修改。
- 不在没有明确请求时创建 Git commit、推送远程、重建正式 abi3 产物或修改相邻仓库。
- 不把本地测试构建宣称为发布构建。

## 交付格式

最终说明应包含：实现结果、关键文件/行为变化、测试命令及结果、未处理的环境或无关失败、是否创建 commit。若删除文件，说明删除原因；若保留用户脏改动，明确指出未触碰。
