# 文档索引

本目录描述 **2026-09-09 工作区中的实际实现**，不是完整浏览器规范，也不把待办事项写成已有能力。
公开参数以源码为准；文档中的行为结论应能定位到对应测试。

## 阅读路径

| 你要做什么 | 阅读文档 |
| --- | --- |
| 第一次运行 | [项目 README](../README.md) → [使用与配置](USAGE.md) |
| 接手项目或排查架构问题 | [交接状态](../HANDOFF.md) → [架构与不变量](ARCHITECTURE.md) |
| 添加浏览器接口或修复绑定 | [开发与验证](DEVELOPMENT.md) → [IDL 声明与编译](IDL_MIGRATION.md) |
| 开关 trace、编写观察回调 | [被动 Trace](TRACE.md) |
| Mock 资源、接入网络、控制磁盘缓存 | [资源加载与拦截](resource-interception.md) |
| 处理 iframe、跨源和消息 | [Window 安全模型](WINDOW_SECURITY.md) |
| 调试首句、Worker 和重连 | [DevTools 与迁移](DEVTOOLS.md) → [验证记录](DEVTOOLS_VALIDATION.md) |
| 更新 Chromium 元数据 | [baseline 格式与生成](interface-baseline-fields.md) |
| 查历史变更及验证记录 | [工作日志](../WORKLOG.md) |

## 文档职责

- 根 README 只提供项目定位、最小示例和导航，不复制完整设计说明。
- HANDOFF 记录当前代码事实、接手检查表和未解决约束。
- WORKLOG 区分已提交历史、未提交工作区改动及本轮实际执行的验证。
- 本目录专题文档是对应主题的详细说明；已有文件名保留以减少链接迁移成本。
- 修改 API、原生协议或生命周期行为时，同步更新专题文档和回归测试；只在影响交接时更新 HANDOFF。
