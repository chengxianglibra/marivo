# Enum Value Set Schema Contract -- SUPERSEDED

> **本文档已被取代，不应作为新设计或实现的依据。**

本文档描述的 Enum Value Set（`enum_set_ref` / `enum_version` 引用的受治理值域本体）已在 OSI 对齐重写中删除。

**替代方案：** 枚举值域作为 `Field.dimension` 属性在 Dataset 模型内表达，不再是独立对象类型。维度值治理通过字段级约束实现。

**关键变更：** EnumSet 作为独立对象类型被删除。不再有 `enum.*` 引用前缀。`enum_set_ref` 和 `enum_version` 字段已从 dimension contract 中移除。

**权威参考：** `docs/superpowers/specs/2026-04-30-osi-alignment-v2-design.md` 及更新后的 `docs/specs/semantic/overview.md`。

**历史说明：** 本文档保留用于可追溯性。原始内容可从 git 历史中本通知提交之前的版本恢复。
