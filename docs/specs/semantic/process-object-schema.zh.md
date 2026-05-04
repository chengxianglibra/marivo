# Semantic Layer Process Object Schema -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 Process Object（funnel、cohort、session、experiment、path、lifecycle 等过程语义对象）已在 OSI 对齐重写中删除。

**替代方案：** 过程语义将作为专用 Metric 子类型或数据集级约束建模。OSI Metric 是扁平表达式模型，通过 MARIVO 扩展承载观测数据集、粒度、时间和过滤条件。

**关键变更：** Process Object 作为独立对象类型被删除。不再有 `process.*` 引用前缀。process_exported_dimension_refs、process_dependency 等字段已删除。过程相关的窗口/阶段/路径语义将另行设计，不在当前 OSI v2 模型范围内。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/overview.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
