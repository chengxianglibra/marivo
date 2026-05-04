# Semantic Layer Predicate Schema Contract -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 Predicate（`predicate.*` 独立过滤语义对象、`carrier_row_filter` / `metric_qualifier` / `request_scope` / `governance_policy` usage 分类、Effective Scope 合成公式）已在 OSI 对齐重写中删除。

**替代方案：** 过滤语义由 `Metric.filters`（MARIVO 扩展）承载。数据集级行过滤通过 Dataset 和 Field 级约束表达。不再有独立的 Predicate 对象类型或 `predicate.*` 引用前缀。

**关键变更：** Predicate 作为独立对象类型被删除。`allowed_usage` 分类（`carrier_row_filter`、`metric_qualifier`、`request_scope`、`governance_policy`）不再适用。Effective Scope 合成由编译器从 Metric.filters + 数据集约束直接推导。`PredicateUsage` Literal 类型和 `carrier_row_filter` 概念已移除。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/overview.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
