# Marivo 架构

本目录描述当前代码的模块职责、数据流和长期契约。精确签名、参数与可运行语法由
`marivo.help(...)` 和 CLI `marivo help <target>` 披露；架构文档解释这些接口为什么这样组合。
数学参考中的定律另有前提，不等于公开 API 或优化器支持清单。

## 模块与阅读入口

| 模块 | 入口与职责 |
| --- | --- |
| Datasource | [连接与凭据](datasource/README.md)、[物理来源与执行](datasource/sources-and-execution.md)、[发现与证据](datasource/discovery.md) |
| Semantic | [总览](semantic/overview.md)、[对象模型](semantic/semantic-object-model.md)、[表达式](semantic/expressions.md)、[加载与验证](semantic/loading-validation-introspection.md)、[编写工作流](semantic/authoring-workflow.md)、[时间认证](semantic/temporal-authority.md) |
| Analysis | [分析架构](analysis/README.md)：Population、来源观察、Frame 运算、执行、持久化与审计 |
| 跨模块契约 | [时间语义](temporal-semantics.md)、[Agent 公共界面](agent-friendly-public-surface.md) |
| API 参考 | [Sphinx 源文件](../api/)；具体调用以当前安装版本的 Live Help 为准 |

```text
datasource: connection / source / scoped evidence
    -> semantic: identity / fields / Metric definitions / certified time
    -> analysis: explicit Population and source bindings
    -> AnalysisFrame: expression -> complete memory -> stored data
    -> Artifact: submission and audit
```

`marivo.datasource`、`marivo.semantic` 和 `marivo.analysis` 分别拥有物理访问、业务含义和
分析求值。固定 Frame 的后续数据操作不重新加载原语义定义或访问原来源。
项目运行状态位于 `.marivo/`；凭据配置与保存边界见 Datasource 文档。

## 文档边界

每项长期事实只在一个主题中完整定义，其它文档链接到该主题。代码定位表指向实际实现，
不把模块名、设计说明或一次测试结果当作完整后端支持证明。静态检查、本地执行、provider
Runtime、安装包和发布验收应分别报告。

临时设计、实施计划、评审过程与验收记录留在本地未跟踪目录
`docs/design/`、`docs/implementation/`、`docs/testing/`，不作为本目录的规范依赖。
版本迁移叙事和未来能力提案不属于当前架构参考。
