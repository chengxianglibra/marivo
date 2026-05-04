# Compiler Compatibility Profile 契约 -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 Compiler Compatibility Profile（独立发布的组合兼容性与前置能力 artifact）已在 OSI 对齐重写中删除。

**替代方案：** 编译器能力推导直接使用 OSI 对象字段（Dataset、Field、Relationship、Metric 的核心属性），不再通过独立的 compatibility profile artifact 表达。

**关键变更：** Compatibility Profile 作为独立对象类型被删除。组合兼容性由 Relationship 上的 cardinality 和 Field 类型匹配表达。编译器从 OSI 对象核心字段直接推导能力。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/compiler-spec.zh.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
