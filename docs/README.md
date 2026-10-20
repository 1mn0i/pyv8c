# 文档索引

本文档集描述当前实现和接口契约。阅读示例前请完成[项目安装](../README.md#安装)。命令默认从 pyv8c 仓库根目录执行；原生仓库命令会单独说明。

| 文档 | 内容 |
| --- | --- |
| [使用指南](usage.md) | 创建对象、页面执行、等待、环境配置、输入和关闭 |
| [架构设计](architecture.md) | 模块分层、绑定装配、所有权和状态归属 |
| [任务调度](task-scheduling.md) | 执行组、Agent、任务源、微任务、定时器和渲染机会 |
| [资源加载](resource-loading.md) | 拦截器、加载器、重定向、缓存及文档资源生命周期 |
| [网络传输](network-transports.md) | HTTP/WebSocket 引擎、代理、Cookie 和自定义传输 |
| [请求头](request-headers.md) | 请求场景、来源、头生成及传输差异 |
| [窗口与同源访问](window-security.md) | iframe、WindowProxy、origin 和跨源访问 |
| [IDL 格式](idl-format.md) | 接口基线、迭代元数据、提取和环境档案 |
| [调试器](devtools.md) | Inspector、连接、暂停、子目标和退出 |
| [运行跟踪](tracing.md) | 跟踪配置、事件格式、线程约束和观测边界 |
| [开发指南](development.md) | 实现规范、验证命令、行为对照和性能测量 |

专题文件使用小写英文单词和短横线命名，索引保留 `README.md`；正文、标题和示例说明统一使用中文。Python、JavaScript、协议及配置项保留实际名称。

这些文档不替代源码和测试。排查差异时，应同时记录解释器、原生扩展、IDL、环境档案、本机 Chrome 和网络传输版本，不把这些版本当作同一个版本号。
